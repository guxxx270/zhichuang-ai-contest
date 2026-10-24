"""企微入口：一本账记账 + 追问记忆 + /摘要 /记忆 命令。复用 test_wecom_channel 的模拟网关，但注入真实流水线与临时库。"""
from __future__ import annotations

import asyncio

from xuzhi.channels.wecom.connection import WeComLongConnection
from xuzhi.channels.wecom.handler import WecomHandler
from xuzhi.ledger import Ledger
from xuzhi.memory import Memory
from xuzhi.pipeline import analyze

from test_wecom_channel import NUMBERED_NEW_REQ, MockOpenWS, make_cfg

REQ = "资管运营小周：净值日报能不能加一列'较上一交易日变动'，风险指标也放进去，周五给客户那版也要，最好下周就能用。"
REQ2 = "资管运营：净值日报再加一列累计净值，也要给客户版，下周要。"


class Rig:
    def __init__(self, tmp_path, analyze_fn=None) -> None:
        self.ledger = Ledger(tmp_path / "l.sqlite3")
        self.memory = Memory(tmp_path / "l.sqlite3")
        self.analyze_fn = analyze_fn or (lambda *a, **k: analyze(*a, llm_polish=False, **k))

    async def __aenter__(self):
        self.mock = MockOpenWS()
        await self.mock.start()
        cfg = make_cfg(self.mock.port, False)
        holder: dict = {}

        async def on_message(m):
            await holder["h"].handle(m)

        self.conn = WeComLongConnection(cfg.bot, on_message)
        holder["h"] = WecomHandler(cfg, self.conn, analyze_fn=self.analyze_fn, ledger=self.ledger, memory=self.memory)
        self.handler = holder["h"]
        self.task = asyncio.create_task(self.conn.run_forever())
        await asyncio.wait_for(self.mock.subscribed.wait(), 5)
        return self

    async def __aexit__(self, *exc):
        await self.conn.stop()
        self.task.cancel()
        await self.mock.stop()
        return False


def test_wecom_books_ledger_and_learns_memory(tmp_path):
    async def main():
        async with Rig(tmp_path) as rig:
            mock = rig.mock
            await mock.push("w1", "025058", f"@需知 {REQ}")
            final = await mock.wait_final("w1")
            assert "先和业务确认" in final
            rows = rig.ledger.recent()
            assert len(rows) == 1 and rows[0]["source"] == "企业微信" and rows[0]["status"] == "受理"
            rid = rows[0]["id"]
            assert any(e["action"] == "企微受理" for e in rig.ledger.events_of(rid))
            # 按编号答两题 → 同一条账更新，口径进记忆
            await mock.push("w2", "025058", "1 以上一交易日结算价为基准\n2 客户版隐去内部字段")
            final2 = await mock.wait_final("w2")
            assert "已收到 2 条答复" in final2
            assert len(rig.ledger.recent()) == 1
            row = rig.ledger.get(rid)
            assert row["n_answered"] >= 2 and row["status"] == "已确认"
            assert [v["label"] for v in rig.ledger.versions_of(rid)] == ["受理", "业务答复重算"]   # 版本对比：企微答复也存一版
            assert rig.memory.stats()["entries"] >= 1
            # 换一条同提出方的需求 → 沿用，不再问
            await mock.push("w3", "025058", "/reset")
            await mock.wait_final("w3")
            await mock.push("w4", "025058", f"@需知 {REQ2}")
            final4 = await mock.wait_final("w4")
            assert "沿用上次口径" in final4
            assert len(rig.ledger.recent()) == 2
            # 命令：/摘要 与 /记忆
            await mock.push("w5", "025058", "/摘要")
            out = await mock.wait_final("w5")
            assert "催办摘要" in out and ("本周受理" in out or "暂无" in out)
            await mock.push("w6", "025058", "/记忆")
            out = await mock.wait_final("w6")
            assert "追问记忆" in out and "累计沿用" in out
            await mock.push("w7", "025058", "/记忆 清 资管运营")
            out = await mock.wait_final("w7")
            assert "已清掉" in out
            await mock.push("w8", "025058", "/记忆 清空")
            assert "已清空" in await mock.wait_final("w8")
            assert rig.memory.stats()["entries"] == 0
            await mock.push("w9", "025058", "/help")
            assert "/摘要" in await mock.wait_final("w9")

    asyncio.run(main())


def test_numbered_new_requirement_is_not_learned_as_answers(tmp_path):
    async def main():
        async with Rig(tmp_path) as rig:
            await rig.mock.push("n1", "025058", f"@需知 {REQ}")
            await rig.mock.wait_final("n1")
            await rig.mock.push("n2", "025058", NUMBERED_NEW_REQ)
            final = await rig.mock.wait_final("n2")
            assert "已收到" not in final and "基差" in final
            assert len(rig.ledger.recent()) == 2                       # 新需求另起一行，上一条没被重算
            assert rig.memory.stats()["entries"] == 0                 # 整句话没进追问记忆
            first = rig.ledger.recent()[-1]["id"]
            assert not any(e["action"] == "业务答复重算" for e in rig.ledger.events_of(first))

    asyncio.run(main())


def test_wecom_retry_updates_the_same_row(tmp_path):
    """模型估时失败 → 发 /重估 → 同一条账更新为模型估时，不新建行。"""
    calls = []

    def flaky(text, *args, **kwargs):
        a = analyze(text, *args, llm_polish=False, **kwargs)
        calls.append(kwargs)
        est = a.estimate
        if len(calls) == 1:
            est.status, est.error = "failed", "模型估时调用失败，尚未生成工时。请检查模型连接后重新分析。"
        else:
            est.status, est.method, est.basis = "ready", "llm", "requirements_only"
            est.low, est.mid, est.high, est.ai_low, est.ai_mid, est.ai_high = 1.5, 2.0, 3.0, 0.75, 1.0, 1.5
            est.saving_pct, est.confidence = 50.0, "低"
        return a

    async def main():
        async with Rig(tmp_path, flaky) as rig:
            await rig.mock.push("r1", "025058", f"@需知 {REQ}")
            final = await rig.mock.wait_final("r1")
            assert "模型估时未成功" in final and "发 /重估 再试" in final and "重新分析" not in final
            rid = rig.ledger.recent()[0]["id"]
            await rig.mock.push("r2", "025058", "/重估")
            final2 = await rig.mock.wait_final("r2")
            assert "以前 16 小时" in final2 and "现在 **8 小时**" in final2
            assert len(rig.ledger.recent()) == 1
            row = rig.ledger.get(rid)
            assert row["mid_days"] == 2.0 and row["ai_days"] == 1.0
            assert [v["label"] for v in rig.ledger.versions_of(rid)] == ["受理", "模型估时"]
            assert any(e["action"] == "企微重估" and "已更新" in e["detail"] for e in rig.ledger.events_of(rid))
            assert not any(e["action"] == "业务答复重算" for e in rig.ledger.events_of(rid))
            # 重估沿用原话与已有编号：不重复累计记忆命中，模型只做问清与估时
            assert calls[1]["count_memory_hits"] is False and calls[0]["count_memory_hits"] is True
            assert calls[1]["model_stages"] == {"intake", "estimate"}

    asyncio.run(main())
