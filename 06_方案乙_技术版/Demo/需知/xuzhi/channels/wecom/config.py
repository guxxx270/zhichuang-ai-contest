"""企微入口配置：机器人凭据与白名单来自 .env（已 gitignore），其余为带默认值的运行参数。

沿用需知"密钥不进代码与仓库"的约定：本文件只读环境变量，不写死任何 key。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Set

from ... import config as xuzhi_config   # 导入即触发 .env 加载

__all__ = ["BotConfig", "AuthConfig", "Limits", "AskConfig", "WecomConfig", "load_wecom_config"]


def _env(key: str, default: str = "") -> str:
    return (os.getenv(key, default) or "").strip()


def _env_int(key: str, default: int) -> int:
    try:
        return int(_env(key) or default)
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(_env(key) or default)
    except ValueError:
        return default


def _env_set(key: str) -> Set[str]:
    """逗号 / 分号 / 空格分隔均可。"""
    raw = _env(key).replace("；", ",").replace(";", ",").replace("，", ",")
    return {p.strip() for p in raw.replace(" ", ",").split(",") if p.strip()}


@dataclass
class BotConfig:
    bot_id: str
    secret: str
    ws_url: str = "wss://openws.work.weixin.qq.com"
    proxy: str = "none"          # none 直连（默认）| env 用环境变量代理 | http://host:port
    name: str = "需知"            # 机器人在企微里的名字，用于剥掉群聊的 "@名字 " 前缀
    ping_interval: int = 30


@dataclass
class AuthConfig:
    mode: str = "whitelist"      # whitelist | allow_all（仅本机调试）
    users: Set[str] = field(default_factory=set)
    chats: Set[str] = field(default_factory=set)   # 为空表示不限群，只校验人


@dataclass
class Limits:
    max_concurrent: int = 3          # 企微：同一机器人最多 3 个并发交互
    queue_wait_seconds: int = 60
    stream_max_seconds: int = 330    # 企微流式上限 6 分钟，留余量
    stream_min_interval: float = 0.6
    stream_max_bytes: int = 20000    # 官方单条 content 上限 20480 字节
    session_ttl_minutes: int = 120
    session_max_turns: int = 12
    dedup_ttl_seconds: int = 600
    analyze_timeout_seconds: int = 240


@dataclass
class AskConfig:
    """问码：基于需知自己的代码库回答问题（走 Claude Agent SDK，只读）。"""
    enabled: bool = True
    repo_path: str = ""             # 空 = 需知 Demo 根目录
    max_turns: int = 20
    timeout_seconds: int = 240
    model: str = ""                 # 空 = SDK 默认


@dataclass
class WecomConfig:
    bot: BotConfig
    auth: AuthConfig
    limits: Limits
    ask: AskConfig = field(default_factory=AskConfig)
    default_mode: str = "req"         # req 需求分析 | ask 问码
    min_requirement_chars: int = 15   # 短于这个字数且不像命令/答复 → 回引导语，不跑流水线

    @property
    def configured(self) -> bool:
        return bool(self.bot.bot_id and self.bot.secret)


def load_wecom_config() -> WecomConfig:
    """从 .env 读取。缺 BotID/Secret 时返回的配置 configured=False，由入口给出友好提示。"""
    return WecomConfig(
        bot=BotConfig(
            bot_id=_env("WECOM_BOT_ID"),
            secret=_env("WECOM_BOT_SECRET"),
            ws_url=_env("WECOM_WS_URL") or BotConfig.ws_url,
            proxy=_env("WECOM_PROXY") or "none",
            name=_env("WECOM_BOT_NAME") or xuzhi_config.PRODUCT_NAME,
            ping_interval=_env_int("WECOM_PING_INTERVAL", 30),
        ),
        auth=AuthConfig(
            mode=(_env("WECOM_AUTH_MODE") or "whitelist").lower(),
            users=_env_set("WECOM_ALLOW_USERS"),
            chats=_env_set("WECOM_ALLOW_CHATS"),
        ),
        limits=Limits(
            max_concurrent=_env_int("WECOM_MAX_CONCURRENT", 3),
            analyze_timeout_seconds=_env_int("WECOM_ANALYZE_TIMEOUT", 240),
            stream_min_interval=_env_float("WECOM_STREAM_MIN_INTERVAL", 0.6),
        ),
        ask=AskConfig(
            enabled=_env("WECOM_ASK_ENABLED", "1") not in ("0", "false", "no"),
            repo_path=_env("WECOM_ASK_REPO"),
            max_turns=_env_int("WECOM_ASK_MAX_TURNS", 20),
            timeout_seconds=_env_int("WECOM_ASK_TIMEOUT", 240),
            model=_env("WECOM_ASK_MODEL"),
        ),
        default_mode="ask" if _env("WECOM_DEFAULT_MODE").lower() in ("ask", "问码", "问答") else "req",
        min_requirement_chars=_env_int("WECOM_MIN_CHARS", 15),
    )
