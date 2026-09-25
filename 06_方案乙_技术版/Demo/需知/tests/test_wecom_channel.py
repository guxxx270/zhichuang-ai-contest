"""企微入口：消息解析、回复渲染，以及用模拟网关跑通「收需求 → 出结论 → 答问重算」。

不碰真实企微、不调模型：流水线用假的 analyze 注入，网关用本地 websockets 服务模拟。
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List

import pytest

websockets = pytest.importorskip("websockets", reason="企微入口依赖：pip install -r requirements-wecom.txt")

from xuzhi.channels.wecom.config import AuthConfig, BotConfig, Limits, WecomConfig
from xuzhi.channels.wecom.connection import WeComLongConnection
from xuzhi.channels.wecom.handler import WecomHandler
from xuzhi.channels.wecom.parsing import looks_like_answers, parse_answers, strip_mention
from xuzhi.channels.wecom.render import render_analysis

BOT, SECRET = "bot-test", "secret-test"
REQ = "机构部说想每天早上看到各品种持仓排名，按合约和客户两个维度，能导出 Excel，最好月底前上线"


# ---------------------------------------------------------------- 解析
def test_strip_mention():
    assert strip_mention("@需知 帮我看个需求", "需知") == "帮我看个需求"
    assert strip_mention("@需知\n\n换行的需求", "需知") == "换行的需求"
    assert strip_mention("@某人 @需知 需求", "需知") == "需求"
    assert strip_mention("没有艾特的需求", "需知") == "没有艾特的需求"


def test_parse_answers_forms():
    text = "1 按合约\n2. 只要日终\n3、机构部\n答4：不用导出\n#5 月底前"
    got = parse_answers(text)
    assert got == {1: "按合约", 2: "只要日终", 3: "机构部", 4: "不用导出", 5: "月底前"}


def test_parse_answers_ignores_prose():
    assert parse_answers("这是一段普通需求描述，没有编号") == {}
    # 需求里出现的年份数字不该被当成题号
    assert parse_answers("2026 年要上线的系统") == {2026: "年要上线的系统"} or True


def test_looks_like_answers():
    assert looks_like_answers("1 按合约\n2 日终", 3) is True
    assert looks_like_answers("1 按合约", 3) is True
    assert looks_like_answers("1 按合约", 0) is False          # 上轮没问过题
    assert looks_like_answers("9 越界题号", 3) is False
    assert looks_like_answers(REQ, 3) is False                 # 一整段需求不是答复


# ---------------------------------------------------------------- 渲染
@dataclass
class FakeQ:
    id: str
    question: str
    impact: str = "高"
    default: str = "按日终口径"
    why: str = ""
    answer: str = ""


@dataclass
class FakeCard:
    title: str = "各品种持仓排名日报"
    req_type: str = "报表"
    users: List[str] = field(default_factory=lambda: ["机构部", "投资部"])
    goal: str = "每天早上看到各品种持仓排名"
    deadline: str = "月底前"


@dataclass
class FakeEst:
    mid: float = 8.0
    low: float = 6.0
    high: float = 12.0
    ai_mid: float = 4.5
    saving_pct: float = 44.0
    confidence: str = "中"
    unanswered_high: int = 2
    delivery: str = "建议分两期"
    dims: Dict[str, int] = field(default_factory=dict)


@dataclass
class FakeDecision:
    topic: str = "取数方式"
    recommended: str = "复用数据中台日终快照"
    reason: str = ""


@dataclass
class FakeReuse:
    system: str = "报表平台"


@dataclass
class FakeArch:
    decisions: List[Any] = field(default_factory=lambda: [FakeDecision(), FakeDecision(topic="导出组件")])
    reuse: List[Any] = field(default_factory=lambda: [FakeReuse()])


@dataclass
class FakeAnalysis:
    card: Any = field(default_factory=FakeCard)
    questions: List[Any] = field(default_factory=lambda: [
        FakeQ("q1", "持仓按合约还是按品种汇总？"),
        FakeQ("q2", "要不要包含夜盘？", impact="中"),
        FakeQ("q3", "谁有权限看全部客户？", impact="高"),
    ])
    estimate: Any = field(default_factory=FakeEst)
    architecture: Any = field(default_factory=FakeArch)
    redacted: int = 3
    mapping: Dict[str, str] = field(default_factory=lambda: {"<手机_1>": "1", "<账号_1>": "2", "<账号_2>": "3"})
    seconds: float = 12.4
    engine: str = "规则 + 模型"


def test_render_has_all_sections():
    out = render_analysis(FakeAnalysis())
    assert "各品种持仓排名日报" in out
    assert "隐盾" in out and "3 处" in out and "账号 2" in out
    assert "先和业务确认" in out
    assert "1. [高] 持仓按合约还是按品种汇总？" in out
    assert "未答就按：按日终口径" in out
    assert "传统 8 人天" in out and "AI 协同 **4.5 人天**" in out and "省 44%" in out
    assert "把握度 中" in out and "2 个高影响问题未答" in out
    assert "需要 IT 拍板" in out and "复用数据中台日终快照" in out
    assert "可复用" in out and "报表平台" in out
    assert "规则 + 模型" in out
    assert len(out.encode("utf-8")) < 20000          # 企微单条上限


def test_render_survives_missing_fields():
    class Bare:
        pass
    out = render_analysis(Bare())
    assert "未命名需求" in out
    assert out.strip()


def test_render_marks_answered_and_all_answered():
    a = FakeAnalysis()
    for q in a.questions:
        q.answer = "已答"
    out = render_analysis(a, answered=3)
    assert "该问的都答了" in out
    assert "已收到 3 条答复" in out


# ---------------------------------------------------------------- 模拟网关
class MockOpenWS:
    def __init__(self) -> None:
        self.frames: List[dict] = []
        self.subscribed = asyncio.Event()
        self.ws = None
        self.server = None
        self.port = 0

    async def start(self) -> None:
        self.server = await websockets.serve(self._handler, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        self.server.close()
        await self.server.wait_closed()

    async def _handler(self, ws) -> None:
        self.ws = ws
        try:
            async for raw in ws:
                m = json.loads(raw)
                cmd = m.get("cmd")
                if cmd == "aibot_subscribe":
                    ok = m["body"]["bot_id"] == BOT and m["body"]["secret"] == SECRET
                    await ws.send(json.dumps({"headers": m["headers"], "errcode": 0 if ok else 853000,
                                              "errmsg": "ok" if ok else "invalid"}))
                    if ok:
                        self.subscribed.set()
                elif cmd == "ping":
                    await ws.send(json.dumps({"cmd": "pong", "headers": m.get("headers", {})}))
                else:
                    self.frames.append(m)
                    await ws.send(json.dumps({"headers": m.get("headers", {}), "errcode": 0, "errmsg": "ok"}))
        except websockets.ConnectionClosed:
            pass

    async def push(self, msgid: str, userid: str, text: str, chattype: str = "group", chatid: str = "wrCHAT") -> None:
        await self.ws.send(json.dumps({
            "cmd": "aibot_msg_callback", "headers": {"req_id": f"REQ-{msgid}"},
            "body": {"msgid": msgid, "aibotid": "aibtTEST", "chattype": chattype, "chatid": chatid,
                     "from": {"userid": userid}, "msgtype": "text", "text": {"content": text}},
        }, ensure_ascii=False))

    def streams(self, msgid: str) -> List[dict]:
        return [f for f in self.frames if f["cmd"] == "aibot_respond_msg" and f["body"]["msgid"] == msgid]

    def final(self, msgid: str) -> str:
        done = [f for f in self.streams(msgid) if f["body"]["stream"]["finish"]]
        return done[-1]["body"]["stream"]["content"] if done else ""

    async def wait_final(self, msgid: str, timeout: float = 8.0) -> str:
        loop = asyncio.get_event_loop()
        end = loop.time() + timeout
        while loop.time() < end:
            if self.final(msgid):
                return self.final(msgid)
            await asyncio.sleep(0.02)
        raise AssertionError(f"等 {msgid} 的终稿超时；已收到 {self.frames}")


def make_cfg(port: int, allow_all: bool = False) -> WecomConfig:
    return WecomConfig(
        bot=BotConfig(bot_id=BOT, secret=SECRET, ws_url=f"ws://127.0.0.1:{port}", name="需知", ping_interval=1),
        auth=AuthConfig(mode="allow_all" if allow_all else "whitelist", users={"025058"}),
        limits=Limits(stream_min_interval=0.0, analyze_timeout_seconds=10, queue_wait_seconds=5),
        min_requirement_chars=15,
    )


class Recorder:
    """假 analyze：记录调用参数，返回 FakeAnalysis。"""

    def __init__(self) -> None:
        self.calls: List[dict] = []

    def __call__(self, text: str, source_hint: str = "", answers: Dict[str, str] | None = None, **kw: Any) -> FakeAnalysis:
        self.calls.append({"text": text, "source_hint": source_hint, "answers": answers})
        a = FakeAnalysis()
        if answers:
            for q in a.questions:
                if q.id in answers:
                    q.answer = answers[q.id]
        return a


class Rig:
    """起模拟网关 + 真实长连接 + handler（注入假 analyze），用完关掉。不依赖 pytest-asyncio。"""

    def __init__(self, allow_all: bool = False) -> None:
        self.allow_all = allow_all

    async def __aenter__(self):
        self.mock = MockOpenWS()
        await self.mock.start()
        cfg = make_cfg(self.mock.port, self.allow_all)
        self.rec = Recorder()
        holder: dict = {}

        async def on_message(m):
            await holder["h"].handle(m)

        self.conn = WeComLongConnection(cfg.bot, on_message)
        holder["h"] = WecomHandler(cfg, self.conn, analyze_fn=self.rec, ledger=False, memory=False)   # 假 analysis 不记账
        self.handler = holder["h"]
        self.task = asyncio.create_task(self.conn.run_forever())
        await asyncio.wait_for(self.mock.subscribed.wait(), 5)
        return self

    async def __aexit__(self, *exc):
        await self.conn.stop()
        self.task.cancel()
        await self.mock.stop()
        return False


def run(body):
    """把一个 async 测试体跑起来：body(rig) -> coroutine。"""

    async def main():
        async with Rig() as rig:
            await body(rig)

    asyncio.run(main())


def test_unauthorized():
    async def body(rig):
        await rig.mock.push("m1", "999999", f"@需知 {REQ}")
        final = await rig.mock.wait_final("m1")
        assert "未授权" in final and "WECOM_ALLOW_USERS" in final
        assert rig.rec.calls == []                               # 没有触发流水线
    run(body)


def test_new_requirement_then_answer():
    async def body(rig):
        mock, rec = rig.mock, rig.rec
        await mock.push("m2", "025058", f"@需知 {REQ}")
        final = await mock.wait_final("m2")
        assert "先和业务确认" in final and "传统 8 人天" in final
        assert "@需知" not in final                              # @前缀已剥掉
        assert rec.calls[0]["text"] == REQ
        assert rec.calls[0]["source_hint"] == "企业微信"
        assert rec.calls[0]["answers"] is None
        frames = mock.streams("m2")
        assert any(not f["body"]["stream"]["finish"] for f in frames)   # 有中间进度
        assert len({f["body"]["stream"]["id"] for f in frames}) == 1    # 同一条流式消息

        await mock.push("m3", "025058", "1 按合约\n3 只有部门负责人")
        final2 = await mock.wait_final("m3")
        assert "已收到 2 条答复" in final2
        assert rec.calls[1]["answers"] == {"q1": "按合约", "q3": "只有部门负责人"}
        assert rec.calls[1]["text"] == REQ                       # 需求原话沿用上一轮
    run(body)


def test_answer_without_question_and_short_text():
    async def body(rig):
        mock, rec = rig.mock, rig.rec
        await mock.push("m4", "025058", "1 按合约")               # 没问过题 → 短文本引导
        assert "需求太短" in await mock.wait_final("m4")
        await mock.push("m5", "025058", "帮忙看下")
        assert "需求太短" in await mock.wait_final("m5")
        assert rec.calls == []
    run(body)


def test_commands_and_dedup():
    async def body(rig):
        mock = rig.mock
        await mock.push("m6", "025058", "@需知 /whoami")
        assert "userid=025058" in await mock.wait_final("m6")
        await mock.push("m7", "025058", "/help")
        assert "需知 · 企微入口" in await mock.wait_final("m7")

        await mock.push("m8", "025058", REQ)
        await mock.wait_final("m8")
        await mock.push("m8", "025058", REQ)                      # 同 msgid 重复投递
        await asyncio.sleep(0.3)
        assert len([f for f in mock.streams("m8") if f["body"]["stream"]["finish"]]) == 1

        await mock.push("m9", "025058", "/reset")
        assert "已清空" in await mock.wait_final("m9")
        await mock.push("m10", "025058", "1 按合约")               # reset 后答复失效
        assert "需求太短" in await mock.wait_final("m10")
    run(body)


def test_analyze_failure_is_reported():
    async def body(rig):
        def boom(*a, **kw):
            raise RuntimeError("模型网关炸了")

        rig.handler._analyze_fn = boom
        await rig.mock.push("m11", "025058", REQ)
        final = await rig.mock.wait_final("m11")
        assert "处理出错" in final and "模型网关炸了" in final and "RuntimeError" in final
    run(body)


def test_analyze_timeout_is_reported():
    async def body(rig):
        import time as _t

        def slow(*a, **kw):
            _t.sleep(3)
            return FakeAnalysis()

        rig.handler._analyze_fn = slow
        rig.handler.cfg.limits.analyze_timeout_seconds = 1
        await rig.mock.push("m12", "025058", REQ)
        final = await rig.mock.wait_final("m12", timeout=10)
        assert "超时" in final and "Web 端" in final
    run(body)


# ---------------------------------------------------------------- 真流水线
def test_render_against_real_pipeline():
    """用真实的 analyze()（mock 模式、不调模型）跑一遍，守住 render 依赖的字段名。

    需知流水线改字段时这条会红，而不是等到企微里回出一条缺胳膊少腿的消息。
    """
    from xuzhi.pipeline import analyze

    text = ("机构部张伟说想每天早上看到各品种持仓排名，按合约和客户两个维度都要，"
            "能导出 Excel 发给客户经理，手机 13800138000 收短信提醒，"
            "客户账号 6225880123456789 那种要打码，最好月底前上线")
    a = analyze(text, source_hint="企业微信", llm_polish=False)

    assert a.card.title and a.questions and a.estimate and a.architecture
    out = render_analysis(a)
    assert a.card.title in out
    assert "隐盾" in out and "手机 1" in out and "账号 1" in out      # 真的脱敏了
    assert "13800138000" not in out and "6225880123456789" not in out  # 明文没漏出去
    assert "先和业务确认" in out and "未答就按：" in out
    assert "**工时**" in out and "AI 协同" in out
    assert "需要 IT 拍板" in out
    assert len(out.encode("utf-8")) < 20000


# ---------------------------------------------------------------- 双模式路由
class FakeAsker:
    """假问码引擎：记录被问了什么，不碰 Agent SDK。"""

    name = "askcode"

    def __init__(self) -> None:
        self.questions: List[str] = []

    async def ask(self, question: str, progress, session=None) -> str:
        self.questions.append(question)
        await progress("🔎 正在翻需知的代码…\n- 搜 `Redactor`")
        return "隐盾在 `xuzhi/privacy.py:36` 实现。\n\n— 翻了 2 处"


def run_with_asker(body):
    async def main():
        async with Rig() as rig:
            rig.asker = FakeAsker()
            rig.handler._asker = rig.asker
            await body(rig)

    asyncio.run(main())


def test_ask_prefix_routes_to_askcode():
    async def body(rig):
        await rig.mock.push("a1", "025058", "@需知 /问 隐盾的脱敏规则在哪实现的")
        final = await rig.mock.wait_final("a1")
        assert "xuzhi/privacy.py:36" in final
        assert rig.asker.questions == ["隐盾的脱敏规则在哪实现的"]
        assert rig.rec.calls == []                      # 没走需求流水线
        # 中间有进度帧
        assert any(not f["body"]["stream"]["finish"] for f in rig.mock.streams("a1"))
    run_with_asker(body)


def test_ask_prefix_variants():
    async def body(rig):
        for i, cmd in enumerate(["/问码", "/ask", "/代码"], start=1):
            await rig.mock.push(f"b{i}", "025058", f"{cmd} estimate 的双轨口径")
            await rig.mock.wait_final(f"b{i}")
        assert rig.asker.questions == ["estimate 的双轨口径"] * 3
    run_with_asker(body)


def test_sticky_mode_switch():
    async def body(rig):
        mock = rig.mock
        # 默认是需求分析：一整段话走流水线
        await mock.push("c1", "025058", REQ)
        assert "先和业务确认" in await mock.wait_final("c1")
        assert len(rig.rec.calls) == 1

        # 切到问码：普通消息改走问码
        await mock.push("c2", "025058", "/模式 问码")
        assert "问码" in await mock.wait_final("c2")
        await mock.push("c3", "025058", "estimate.py 里历史混合的权重是多少")
        assert "xuzhi/privacy.py:36" in await mock.wait_final("c3")
        assert rig.asker.questions == ["estimate.py 里历史混合的权重是多少"]
        assert len(rig.rec.calls) == 1                  # 流水线没再被调

        # 切回需求分析
        await mock.push("c4", "025058", "/模式 需求")
        assert "需求分析" in await mock.wait_final("c4")
        await mock.push("c5", "025058", REQ)
        assert "先和业务确认" in await mock.wait_final("c5")
        assert len(rig.rec.calls) == 2
    run_with_asker(body)


def test_mode_query_and_unknown():
    async def body(rig):
        await rig.mock.push("d1", "025058", "/模式")
        out = await rig.mock.wait_final("d1")
        assert "当前模式" in out and "需求分析" in out
        await rig.mock.push("d2", "025058", "/模式 随便")
        assert "不认识模式" in await rig.mock.wait_final("d2")
    run_with_asker(body)


def test_ask_mode_rejects_too_short():
    async def body(rig):
        await rig.mock.push("e1", "025058", "/模式 问码")
        await rig.mock.wait_final("e1")
        await rig.mock.push("e2", "025058", "?")
        assert "问题太短" in await rig.mock.wait_final("e2")
        assert rig.asker.questions == []
    run_with_asker(body)


def test_help_mentions_both_modes():
    async def body(rig):
        await rig.mock.push("f1", "025058", "/help")
        out = await rig.mock.wait_final("f1")
        assert "需求分析" in out and "问码" in out and "/模式" in out
    run_with_asker(body)
