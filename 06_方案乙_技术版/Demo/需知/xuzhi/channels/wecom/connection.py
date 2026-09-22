"""企微智能机器人 · 长连接客户端。

职责：连接 / 订阅 / 心跳 / 断线重连 / 收发帧。业务处理交给回调（on_message / on_event），
回调以 task 形式并发执行，不阻塞读循环。

协议：https://developer.work.weixin.qq.com/document/path/101463
"""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import random
import uuid
from typing import Any, Awaitable, Callable, Dict, Optional

import websockets

from .config import BotConfig
from .models import Inbound

log = logging.getLogger(__name__)

MessageHandler = Callable[[Inbound], Awaitable[None]]
EventHandler = Callable[[Dict[str, Any]], Awaitable[None]]


class SubscribeFailed(Exception):
    pass


def new_req_id() -> str:
    return uuid.uuid4().hex


def connect_kwargs(proxy_mode: str) -> Dict[str, Any]:
    """none 直连（忽略环境变量代理）；env 用环境变量；或显式代理 URL。websockets<15 无 proxy 参数。"""
    kw: Dict[str, Any] = dict(ping_interval=None, max_size=16 * 1024 * 1024)
    try:
        supports_proxy = "proxy" in inspect.signature(websockets.connect).parameters
    except (TypeError, ValueError):
        supports_proxy = False
    if not supports_proxy:
        if proxy_mode not in ("none", "env"):
            log.warning("当前 websockets 版本不支持 proxy 参数，忽略 proxy=%s", proxy_mode)
        return kw
    kw["proxy"] = None if proxy_mode == "none" else (True if proxy_mode == "env" else proxy_mode)
    return kw


class WeComLongConnection:
    def __init__(self, cfg: BotConfig, on_message: MessageHandler, on_event: Optional[EventHandler] = None):
        self.cfg = cfg
        self.on_message = on_message
        self.on_event = on_event
        self._ws = None
        self._stop = asyncio.Event()
        self._connected = asyncio.Event()
        self._tasks: set = set()
        self.stats = {"connects": 0, "frames_in": 0, "frames_out": 0, "ack_errors": 0}

    # ---------------- 生命周期 ----------------
    async def run_forever(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                await self._session()
                backoff = 1.0
            except SubscribeFailed as e:
                # 凭据/模式错误重试没有意义，但也不要退出进程：等较长时间再试，便于后台改完配置自动恢复
                log.error("订阅失败：%s；120s 后重试", e)
                await self._sleep(120)
                continue
            except (websockets.ConnectionClosed, OSError, asyncio.TimeoutError) as e:
                if self._stop.is_set():
                    break
                delay = min(backoff, 60) * (0.8 + 0.4 * random.random())
                log.warning("连接断开：%s；%.1fs 后重连", e, delay)
                await self._sleep(delay)
                backoff = min(backoff * 2, 60)
            except Exception:  # noqa: BLE001
                log.exception("连接循环异常，5s 后重连")
                await self._sleep(5)
        log.info("长连接已停止")

    async def stop(self) -> None:
        self._stop.set()
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass

    async def wait_connected(self, timeout: float = 30) -> None:
        await asyncio.wait_for(self._connected.wait(), timeout)

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def _session(self) -> None:
        log.info("连接 %s（proxy=%s）", self.cfg.ws_url, self.cfg.proxy)
        async with websockets.connect(self.cfg.ws_url, **connect_kwargs(self.cfg.proxy)) as ws:
            self._ws = ws
            self.stats["connects"] += 1
            await self._subscribe(ws)
            self._connected.set()
            log.info("订阅成功，开始收消息")
            ping_task = asyncio.create_task(self._ping_loop(ws))
            try:
                async for raw in ws:
                    self.stats["frames_in"] += 1
                    self._handle_raw(raw)
            finally:
                self._connected.clear()
                ping_task.cancel()
                self._ws = None

    async def _subscribe(self, ws) -> None:
        req = {"cmd": "aibot_subscribe", "headers": {"req_id": new_req_id()},
               "body": {"bot_id": self.cfg.bot_id, "secret": self.cfg.secret}}
        await ws.send(json.dumps(req))
        raw = await asyncio.wait_for(ws.recv(), timeout=20)
        resp = json.loads(raw)
        if resp.get("errcode", -1) != 0:
            raise SubscribeFailed(f"errcode={resp.get('errcode')} errmsg={resp.get('errmsg')}")

    async def _ping_loop(self, ws) -> None:
        while True:
            await asyncio.sleep(self.cfg.ping_interval)
            try:
                await ws.send(json.dumps({"cmd": "ping", "headers": {"req_id": new_req_id()}}))
            except websockets.ConnectionClosed:
                return

    # ---------------- 收帧 ----------------
    def _handle_raw(self, raw: str) -> None:
        try:
            frame = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("非 JSON 帧：%r", raw[:200])
            return
        cmd = frame.get("cmd")
        if cmd == "pong":
            return
        if cmd == "aibot_msg_callback":
            self._spawn(self.on_message(Inbound.from_frame(frame)), "on_message")
            return
        if cmd == "aibot_event_callback":
            if self.on_event:
                self._spawn(self.on_event(frame), "on_event")
            else:
                log.info("事件：%s", json.dumps(frame, ensure_ascii=False))
            return
        if cmd is None and "errcode" in frame:
            if frame.get("errcode") != 0:
                self.stats["ack_errors"] += 1
                log.warning("服务端应答错误：%s", json.dumps(frame, ensure_ascii=False))
            return
        log.info("未知帧：%s", json.dumps(frame, ensure_ascii=False)[:500])

    def _spawn(self, coro: Awaitable[None], name: str) -> None:
        task = asyncio.create_task(self._guard(coro, name))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @staticmethod
    async def _guard(coro: Awaitable[None], name: str) -> None:
        try:
            await coro
        except Exception:  # noqa: BLE001
            log.exception("%s 处理异常", name)

    # ---------------- 发帧 ----------------
    async def send(self, frame: Dict[str, Any]) -> None:
        ws = self._ws
        if ws is None:
            raise ConnectionError("长连接未建立")
        await ws.send(json.dumps(frame, ensure_ascii=False))
        self.stats["frames_out"] += 1

    async def respond_stream(self, req_id: str, msgid: str, stream_id: str, content: str, finish: bool) -> None:
        """回复回调消息（流式）。content 是整条当前内容，企微按整条刷新。"""
        await self.send({
            "cmd": "aibot_respond_msg",
            "headers": {"req_id": req_id},
            "body": {"msgid": msgid, "msgtype": "stream",
                     "stream": {"id": stream_id, "finish": finish, "content": content}},
        })

    async def send_text(self, chattype: str, chatid: str, content: str) -> None:
        """主动推送（无回调触发）。字段名依据第三方 SDK 整理，官方文档未逐字确认——首次使用前请实测。"""
        await self.send({
            "cmd": "aibot_send_msg",
            "headers": {"req_id": new_req_id()},
            "body": {"chattype": chattype, "chatid": chatid,
                     "msgtype": "text", "text": {"content": content}},
        })
