"""沙箱策略：把散在各处的访问边界收成一份 sandbox.yaml，程序启动时加载、页面原样展示。

用法：
    from xuzhi import sandbox
    sandbox.get("askcode.allowed_tools")          # 取一条（点分路径），缺则回内置默认
    sandbox.rows()                                # 展示用：[(分组, 键, 值, 执行位置), ...]
    sandbox.raw_text()                            # sandbox.yaml 原文

没装 PyYAML 或文件缺失时按 DEFAULTS 运行，并在 note() 里说明；策略文件只允许收紧或放宽已列出的项，
不认识的键忽略。"""
from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import config

POLICY_PATH = config.ROOT / "sandbox.yaml"

DEFAULTS: dict[str, Any] = {
    "version": 1,
    "llm": {
        "redact_before_send": True, "restore_after_receive": True, "key_storage": "session_only",
        "allowed_api_schemes": ["https", "http"], "audit_every_call": True, "block_if_plain_sensitive": False,
    },
    "repo_fetch": {"allowed_schemes": ["http", "https"], "deny_private_networks": True, "shallow_clone": True, "token_via_env_only": True},
    "files": {"deny_read_globs": [".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "id_rsa*",
                                  "*secret*", "*credential*", "*password*", "*token*"]},
    "prototype_preview": {"sandbox_iframe": True},
    "wecom": {"auth_mode": "whitelist", "reply_plain_sensitive": False, "stream_max_bytes": 20000},
    "askcode": {
        "allowed_tools": ["Read", "Grep", "Glob"],
        "denied_tools": ["Bash", "Write", "Edit", "MultiEdit", "NotebookEdit", "WebFetch", "WebSearch",
                         "Task", "Agent", "TodoWrite", "KillShell", "BashOutput", "AskUserQuestion"],
        "cwd_locked": True, "load_user_settings": False, "max_turns": 20, "wall_clock_seconds": 240,
    },
    "api": {"bind": "127.0.0.1", "token_required_if_exposed": True, "accept_keys_in_request": False},
    "data": {"production_db": "none", "demo_data": "fictional", "ledger": "local_sqlite"},
    "notify": {"enabled": False, "content": "counts_and_titles_only", "stale_confirm_days": 3, "stale_reconcile_days": 5},
}

# 每条规则在代码里的执行位置（评审对照用）。没列的是架构约束 / 文档约定。
ENFORCED_BY: dict[str, str] = {
    "llm.redact_before_send": "xuzhi/pipeline/__init__.py analyze() → Redactor；xuzhi/drafts.py redact_material()",
    "llm.restore_after_receive": "xuzhi/privacy.py Redactor.restore()",
    "llm.key_storage": "app.py _render_model_secrets()（st.session_state）；xuzhi/config.py 只读 .env",
    "llm.allowed_api_schemes": "xuzhi/llm.py LLM.__init__()、list_openai_models()",
    "llm.audit_every_call": "xuzhi/llm.py LLM.chat() → xuzhi/audit.py Audit.record()",
    "llm.block_if_plain_sensitive": "xuzhi/llm.py LLM.chat()（出站前 count_plain_sensitive）",
    "repo_fetch.allowed_schemes": "xuzhi/drafts.py check_clone_url()",
    "repo_fetch.deny_private_networks": "xuzhi/drafts.py _is_private_host()",
    "repo_fetch.shallow_clone": "xuzhi/drafts.py clone_repo()（--depth 1）",
    "repo_fetch.token_via_env_only": "xuzhi/drafts.py _git_auth_env()、redact_secrets()",
    "files.deny_read_globs": "xuzhi/drafts.py is_secret_path()",
    "prototype_preview.sandbox_iframe": "app.py _sandbox_preview_html() / _iframe()",
    "wecom.auth_mode": "xuzhi/channels/wecom/handler.py _authorized()（白名单来自 .env）",
    "wecom.reply_plain_sensitive": "xuzhi/channels/wecom/render.py render_analysis()（只给结论，不回显原话）",
    "wecom.stream_max_bytes": "xuzhi/channels/wecom/streaming.py _truncate_utf8()",
    "askcode.allowed_tools": "xuzhi/channels/wecom/askcode.py _options() allowed_tools",
    "askcode.denied_tools": "xuzhi/channels/wecom/askcode.py _options() disallowed_tools",
    "askcode.cwd_locked": "xuzhi/channels/wecom/askcode.py _options() cwd",
    "askcode.load_user_settings": "xuzhi/channels/wecom/askcode.py _options() setting_sources=[]",
    "askcode.max_turns": "xuzhi/channels/wecom/askcode.py _options() max_turns",
    "askcode.wall_clock_seconds": "xuzhi/channels/wecom/askcode.py ask()（asyncio.wait_for）",
    "api.bind": "api.py main()（XUZHI_API_HOST 覆盖）",
    "api.token_required_if_exposed": "api.py main() 启动检查；require_token()",
    "api.accept_keys_in_request": "api.py AnalyzeIn（请求体无 key 字段）",
    "data.production_db": "架构约束：代码里没有任何数据库连接（除本机 SQLite）",
    "data.demo_data": "data/、knowledge/ 全部虚构（README「数据与合规」）",
    "data.ledger": "xuzhi/ledger.py、memory.py、audit.py → config.LEDGER_DB",
    "notify.enabled": "xuzhi/reminders.py push_enabled()",
    "notify.content": "xuzhi/reminders.py render_digest()（只有数量、标题、天数）",
    "notify.stale_confirm_days": "xuzhi/reminders.py build_digest()",
    "notify.stale_reconcile_days": "xuzhi/reminders.py build_digest()",
}

_note = ""


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if k not in out:
            continue                       # 不认识的键忽略
        if isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


@lru_cache(maxsize=1)
def policy() -> dict[str, Any]:
    """加载并合并策略；结果缓存，reload() 清缓存。"""
    global _note
    _note = ""
    if not POLICY_PATH.exists():
        _note = f"未找到 {POLICY_PATH.name}，按内置默认策略运行"
        return copy.deepcopy(DEFAULTS)
    try:
        import yaml  # type: ignore
    except ImportError:
        _note = "未安装 PyYAML，按内置默认策略运行（pip install pyyaml 后读取 sandbox.yaml）"
        return copy.deepcopy(DEFAULTS)
    try:
        data = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8")) or {}
    except Exception as e:   # noqa: BLE001
        _note = f"{POLICY_PATH.name} 解析失败（{e}），按内置默认策略运行"
        return copy.deepcopy(DEFAULTS)
    if not isinstance(data, dict):
        _note = f"{POLICY_PATH.name} 不是映射，按内置默认策略运行"
        return copy.deepcopy(DEFAULTS)
    return _merge(DEFAULTS, data)


def reload() -> dict[str, Any]:
    policy.cache_clear()
    return policy()


def note() -> str:
    policy()
    return _note


def get(path: str, default: Any = None) -> Any:
    cur: Any = policy()
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def raw_text() -> str:
    try:
        return POLICY_PATH.read_text(encoding="utf-8")
    except OSError:
        return "# （sandbox.yaml 不存在，以下为内置默认值）\n" + json.dumps(DEFAULTS, ensure_ascii=False, indent=2)


def rows() -> list[tuple[str, str, str, str]]:
    """展示用：(分组, 键, 值, 执行位置)。"""
    out: list[tuple[str, str, str, str]] = []
    for group, items in policy().items():
        if not isinstance(items, dict):
            continue
        for k, v in items.items():
            val = "、".join(str(x) for x in v) if isinstance(v, list) else ("是" if v is True else "否" if v is False else str(v))
            out.append((group, k, val, ENFORCED_BY.get(f"{group}.{k}", "")))
    return out


def summary() -> dict[str, str]:
    """给页面顶部 / API 用的一句话摘要。"""
    p = policy()
    return {
        "模型": "先脱敏再进模型；Key 只留会话；每次调用留审计" if p["llm"]["audit_every_call"] else "先脱敏再进模型",
        "仓库": "只拉 http(s) 公网 / 公司托管仓，拒内网；Token 走环境变量",
        "文件": f"凭据类文件不读（{len(p['files']['deny_read_globs'])} 类）",
        "企微": f"{p['wecom']['auth_mode']} 白名单；问码只读 {'/'.join(p['askcode']['allowed_tools'])}",
        "接口": f"默认只听 {p['api']['bind']}；对外须配 Token",
        "数据": "不接生产库；演示数据虚构；台账本机 SQLite",
        "提醒": "默认关闭；开启也只发数量与标题" if not p["notify"]["enabled"] else "已开启：只发数量与标题",
    }
