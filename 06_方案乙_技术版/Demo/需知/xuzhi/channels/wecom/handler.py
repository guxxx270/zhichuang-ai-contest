"""企微消息分发：去重 → 白名单 → 命令/答复/新需求 → 排队 → 流式回复 → 跑需知流水线。

`analyze()` 是同步且可能几十秒（要调模型），所以放线程里跑，同时用一个心跳任务把进度刷到企微，
避免聊天窗口长时间没动静。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Dict, Optional

from .config import WecomConfig
from .connection import WeComLongConnection
from .dedup import TTLSet
from .models import Inbound
from .parsing import looks_like_answers, parse_answers, strip_mention
from .render import GUIDE_TEXT, HELP_TEXT, render_analysis
from .session import SessionStore
from .streaming import StreamReply

log = logging.getLogger(__name__)

_STAGES = [
    "🛡 隐盾脱敏 → 拆解需求…",
    "❓ 正在想该和业务确认什么…",
    "⏱ 正在比历史需求、估工时…",
    "🧭 正在梳理数据来源与 IT 决策点…",
    "✍️ 正在收拢结论…",
]


class WecomHandler:
    def __init__(
        self,
        cfg: WecomConfig,
        conn: WeComLongConnection,
        analyze_fn: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.cfg = cfg
        self.conn = conn
        self._analyze_fn = analyze_fn
        self.dedup = TTLSet(cfg.limits.dedup_ttl_seconds)
        self.sessions = SessionStore(cfg.limits.session_ttl_minutes)
        self.sem = asyncio.Semaphore(cfg.limits.max_concurrent)
        self.started_at = time.time()
        self.handled = 0

    # ---------------- 依赖 ----------------
    @property
    def analyze(self) -> Callable[..., Any]:
        if self._analyze_fn is None:
            from ...pipeline import analyze as _analyze   # 延迟导入：测试可注入假实现
            self._analyze_fn = _analyze
        return self._analyze_fn

    # ---------------- 入口 ----------------
    async def handle(self, m: Inbound) -> None:
        if self.dedup.seen_or_add(m.msgid):
            log.info("重复投递 msgid=%s，忽略", m.msgid)
            return
        text = strip_mention(m.text, self.cfg.bot.name)
        log.info("收到 msgid=%s %s/%s from=%s msgtype=%s len=%d",
                 m.msgid, m.chattype, m.chatid, m.userid, m.msgtype, len(text))

        ok, reason = self._authorized(m)
        if not ok:
            log.warning("拒绝：%s", reason)
            await self._reply_once(m, f"未授权：{reason}\n把你的 userid 加进 .env 的 WECOM_ALLOW_USERS 即可（发 /whoami 可查）。")
            return

        if text.startswith("/"):
            await self._command(m, text)
            return

        session = self.sessions.get(m.session_key)

        # 在回答上一轮的问清？
        if looks_like_answers(text, len(session.numbered)):
            await self._answer(m, session, text)
            return

        if len(text) < self.cfg.min_requirement_chars:
            await self._reply_once(m, GUIDE_TEXT)
            return

        await self._new_requirement(m, session, text)

    # ---------------- 三条主路径 ----------------
    async def _new_requirement(self, m: Inbound, session, text: str) -> None:
        session.start(text)
        await self._run(m, session, answered=0)

    async def _answer(self, m: Inbound, session, text: str) -> None:
        numbers = parse_answers(text)
        applied = 0
        for num, ans in numbers.items():
            qid = session.qid_for(num)
            if qid:
                session.answers[qid] = ans
                applied += 1
        if not applied:
            await self._reply_once(m, "没认出是在回答哪一题。用「1 你的答复」这样的编号开头，编号对应上一条消息里的题号。")
            return
        session.touch()
        await self._run(m, session, answered=applied)

    async def _run(self, m: Inbound, session, answered: int) -> None:
        try:
            await asyncio.wait_for(self.sem.acquire(), timeout=self.cfg.limits.queue_wait_seconds)
        except asyncio.TimeoutError:
            await self._reply_once(m, "正在处理的需求有点多，排队超时了，稍后再发一次。")
            return
        try:
            # 异常在流式块内部接住：StreamReply 退出时会吞异常并发通用兜底话，
            # 在里面处理才能把具体原因发到同一条消息上。
            async with StreamReply(self.conn, m, self.cfg.limits) as sr:
                await sr.update(_STAGES[0], force=True)
                ticker = asyncio.create_task(self._tick(sr))
                try:
                    analysis = await asyncio.wait_for(
                        asyncio.to_thread(self._analyze_blocking, session),
                        timeout=self.cfg.limits.analyze_timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    log.warning("analyze 超时（%ss）", self.cfg.limits.analyze_timeout_seconds)
                    await sr.finish("⏱ 这条需求处理超时了。可以拆短一点再发，或者去需知 Web 端跑完整流程。")
                    return
                except Exception as e:  # noqa: BLE001
                    log.exception("跑需知流水线失败")
                    await sr.finish(f"❌ 处理出错：{type(e).__name__}: {str(e)[:160]}\n可以重发一次；一直失败就去 Web 端看。")
                    return
                finally:
                    ticker.cancel()
                session.analysis = analysis
                session.turns += 1
                session.remember_questions(list(getattr(analysis, "questions", None) or []))
                await sr.finish(render_analysis(analysis, answered=answered))
                self.handled += 1
        finally:
            self.sem.release()

    def _analyze_blocking(self, session) -> Any:
        """在线程里跑。answers 为空时就是第一遍，有答复时按答复重算。"""
        return self.analyze(
            session.raw_text,
            source_hint="企业微信",
            answers=dict(session.answers) or None,
        )

    async def _tick(self, sr: StreamReply) -> None:
        i = 0
        while True:
            await asyncio.sleep(3.5)
            i += 1
            stage = _STAGES[min(i, len(_STAGES) - 1)]
            await sr.update(f"{stage}（{int(sr.elapsed)}s）", force=True)

    # ---------------- 命令与工具 ----------------
    async def _command(self, m: Inbound, text: str) -> None:
        cmd = text.split()[0].lower()
        if cmd in ("/help", "/帮助"):
            await self._reply_once(m, HELP_TEXT)
        elif cmd == "/ping":
            up = int(time.time() - self.started_at)
            await self._reply_once(m, f"pong · 已运行 {up}s · 已处理 {self.handled} 条 · 连接 {self.conn.stats['connects']} 次")
        elif cmd == "/whoami":
            await self._reply_once(m, f"userid={m.userid}\nchattype={m.chattype}\nchatid={m.chatid}")
        elif cmd in ("/reset", "/重来", "/换需求"):
            self.sessions.reset(m.session_key)
            await self._reply_once(m, "已清空上一条需求，把新的需求发过来吧。")
        else:
            await self._reply_once(m, f"没有 {cmd} 这个命令。\n\n{HELP_TEXT}")

    def _authorized(self, m: Inbound) -> tuple[bool, str]:
        auth = self.cfg.auth
        if auth.mode == "allow_all":
            return True, ""
        if not m.userid:
            return False, "无法识别发送者"
        if m.userid not in auth.users:
            return False, f"用户 {m.userid} 不在白名单"
        if m.chattype == "group" and auth.chats and m.chatid not in auth.chats:
            return False, f"群 {m.chatid} 不在白名单"
        return True, ""

    async def _reply_once(self, m: Inbound, content: str) -> None:
        async with StreamReply(self.conn, m, self.cfg.limits) as sr:
            await sr.finish(content)
