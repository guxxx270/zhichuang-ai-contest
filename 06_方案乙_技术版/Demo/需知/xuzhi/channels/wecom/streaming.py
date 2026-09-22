"""流式回复助手：节流、字节上限、超时兜底，保证每条交互最终都会 finish。"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from .config import Limits
from .models import Inbound
from .connection import WeComLongConnection, new_req_id

log = logging.getLogger(__name__)


def _truncate_utf8(s: str, max_bytes: int) -> str:
    b = s.encode("utf-8")
    if len(b) <= max_bytes:
        return s
    cut = b[: max_bytes - 30].decode("utf-8", errors="ignore")
    return cut + "\n…（内容过长已截断）"


class StreamReply:
    """
    async with StreamReply(conn, inbound, limits) as sr:
        await sr.update("收到，正在解析…")
        ...
        await sr.finish(final_text)
    退出 with 时若未 finish（异常/超时），自动补一条 finish，避免企微那头一直转圈。
    """

    def __init__(self, conn: WeComLongConnection, inbound: Inbound, limits: Limits):
        self.conn = conn
        self.inbound = inbound
        self.limits = limits
        self.stream_id = new_req_id()
        self.started = time.monotonic()
        self._last_sent = 0.0
        self._finished = False
        self._lock = asyncio.Lock()
        self.sent_chunks = 0

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started

    @property
    def finished(self) -> bool:
        return self._finished

    async def update(self, content: str, force: bool = False) -> None:
        """中间进度。距上次发送不足 min_interval 时直接跳过（终稿一定会发，不丢内容）。"""
        if self._finished:
            return
        now = time.monotonic()
        if not force and now - self._last_sent < self.limits.stream_min_interval:
            return
        await self._send(content, finish=False)

    async def finish(self, content: str) -> None:
        if self._finished:
            return
        await self._send(content, finish=True)
        self._finished = True

    async def _send(self, content: str, finish: bool) -> None:
        async with self._lock:
            content = _truncate_utf8(content, self.limits.stream_max_bytes)
            await self.conn.respond_stream(self.inbound.req_id, self.inbound.msgid, self.stream_id, content, finish)
            self._last_sent = time.monotonic()
            self.sent_chunks += 1

    def time_left(self) -> float:
        return self.limits.stream_max_seconds - self.elapsed

    async def __aenter__(self) -> "StreamReply":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> Optional[bool]:
        if not self._finished:
            msg = "处理超时，结果稍后另行发送。" if self.time_left() <= 0 else "处理过程中出错，请稍后再试或联系管理员。"
            if exc is not None:
                log.exception("流式回复期间异常，已用兜底消息收尾")
            try:
                await self.finish(msg)
            except Exception:  # noqa: BLE001
                log.exception("兜底 finish 失败")
        return True  # 异常已记录，不向上抛，避免拖垮读循环的 task 组
