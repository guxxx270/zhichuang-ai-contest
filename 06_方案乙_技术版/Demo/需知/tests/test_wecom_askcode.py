"""问码引擎：只读锁定、进度、会话续接、鉴权/超时/错误兜底。

用伪 claude_agent_sdk，不装真 SDK 也能跑、不联网、不花钱。
"""
from __future__ import annotations

import asyncio
import os
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ---------------- 伪 SDK ----------------
@dataclass
class TextBlock:
    text: str


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: Dict[str, Any]


@dataclass
class AssistantMessage:
    content: List[Any]
    model: str = "fake"


@dataclass
class ResultMessage:
    subtype: str = "success"
    result: str = ""
    session_id: str = "sess-1"
    total_cost_usd: float = 0.0088
    num_turns: int = 4
    duration_ms: int = 5100
    is_error: bool = False


@dataclass
class ClaudeAgentOptions:
    cwd: str = ""
    system_prompt: Any = None
    allowed_tools: list = field(default_factory=list)
    disallowed_tools: list = field(default_factory=list)
    permission_mode: str = "default"
    setting_sources: list = None
    max_turns: int = None
    model: str = None
    resume: str = None


class FakeSDK(types.ModuleType):
    def __init__(self) -> None:
        super().__init__("claude_agent_sdk")
        self.TextBlock, self.ToolUseBlock = TextBlock, ToolUseBlock
        self.AssistantMessage, self.ResultMessage = AssistantMessage, ResultMessage
        self.ClaudeAgentOptions = ClaudeAgentOptions
        self.calls: List[ClaudeAgentOptions] = []
        self.prompts: List[str] = []
        self.script = None

    def query(self, prompt, options):
        self.calls.append(options)
        self.prompts.append(prompt)
        return self.script(prompt, options)


def good_script(prompt, options):
    async def gen():
        yield AssistantMessage([ToolUseBlock("1", "Grep", {"pattern": "Redactor", "path": options.cwd})])
        yield AssistantMessage([ToolUseBlock("2", "Read", {"file_path": f"{options.cwd}/xuzhi/privacy.py", "offset": 30})])
        yield AssistantMessage([TextBlock("找到了")])
        yield ResultMessage(result="隐盾在 `xuzhi/privacy.py:36` 的 `Redactor.redact`。\n\n**依据**\n- `xuzhi/privacy.py:36` 正则替换")
    return gen()


@pytest.fixture
def sdk(monkeypatch):
    fake = FakeSDK()
    fake.script = good_script
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://relay.example/")
    return fake


@pytest.fixture
def asker():
    from xuzhi.channels.wecom.askcode import CodeAsker
    return CodeAsker(ROOT, max_turns=9, timeout_seconds=10)


def ask(asker, question, session=None):
    steps: List[str] = []

    async def progress(c: str) -> None:
        steps.append(c)

    out = asyncio.run(asker.ask(question, progress, session))
    return out, steps


# ---------------- 只读锁定 ----------------
def test_readonly_lockdown(sdk, asker):
    out, _ = ask(asker, "隐盾在哪实现")
    opt = sdk.calls[0]
    assert opt.permission_mode == "dontAsk"
    assert sorted(opt.allowed_tools) == ["Glob", "Grep", "Read"]
    for denied in ("Bash", "Write", "Edit", "WebFetch", "WebSearch"):
        assert denied in opt.disallowed_tools
    assert opt.setting_sources == []
    assert opt.max_turns == 9
    assert Path(opt.cwd) == ROOT
    assert "需知" in opt.system_prompt and "只读" in opt.system_prompt
    assert "xuzhi/privacy.py:36" in out


def test_progress_and_footer(sdk, asker):
    out, steps = ask(asker, "隐盾在哪实现")
    assert steps, "应当有进度刷新"
    assert "启动问码引擎" in steps[0]
    last = steps[-1]
    assert "搜 `Redactor`" in last and "读 xuzhi/privacy.py:30" in last
    assert "翻了 2 处" in out and "$0.009" in out


def test_prompt_wraps_question_first_turn(sdk, asker):
    ask(asker, "estimate 的双轨口径")
    assert "## 问题" in sdk.prompts[0] and "estimate 的双轨口径" in sdk.prompts[0]


# ---------------- 会话续接 ----------------
class Sess:
    ask_session_id = None


def test_session_resume_then_followup(sdk, asker):
    s = Sess()
    ask(asker, "隐盾在哪实现", s)
    assert s.ask_session_id == "sess-1"
    assert sdk.calls[0].resume is None
    ask(asker, "那还原呢", s)
    assert sdk.calls[1].resume == "sess-1"
    assert "## 问题" not in sdk.prompts[1]        # 续接时直接发问题


def test_resume_failure_falls_back_to_new_session(sdk, asker):
    s = Sess()
    s.ask_session_id = "stale"
    attempts = []

    def script(prompt, options):
        attempts.append(options.resume)
        if options.resume:
            async def bad():
                raise RuntimeError("session not found")
                yield  # noqa
            return bad()
        return good_script(prompt, options)

    sdk.script = script
    out, _ = ask(asker, "隐盾在哪实现", s)
    assert attempts == ["stale", None]
    assert "xuzhi/privacy.py:36" in out
    assert s.ask_session_id == "sess-1"


# ---------------- 兜底 ----------------
def test_auth_error_hint(sdk, asker):
    def err(prompt, options):
        async def gen():
            yield ResultMessage(subtype="error", result="Invalid API key", is_error=True)
        return gen()

    sdk.script = err
    out, _ = ask(asker, "隐盾在哪实现")
    assert "没答完" in out and "ANTHROPIC_BASE_URL" in out


def test_exception_and_timeout(sdk, asker):
    def boom(prompt, options):
        async def gen():
            raise ConnectionError("cli died")
            yield  # noqa
        return gen()

    sdk.script = boom
    out, _ = ask(asker, "隐盾在哪实现")
    assert "问码出错" in out and "ConnectionError" in out

    def slow(prompt, options):
        async def gen():
            await asyncio.sleep(5)
            yield ResultMessage(result="late")
        return gen()

    sdk.script = slow
    asker.timeout_seconds = 0.3
    out2, _ = ask(asker, "隐盾在哪实现")
    assert "超时" in out2


def test_missing_sdk_is_reported(monkeypatch, asker):
    """没装 claude-agent-sdk 时给出安装提示，而不是抛栈。"""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "claude_agent_sdk":
            raise ImportError("no module")
        return real_import(name, *a, **kw)

    monkeypatch.delitem(sys.modules, "claude_agent_sdk", raising=False)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    out, _ = ask(asker, "隐盾在哪实现")
    assert "问码不可用" in out and "requirements-wecom.txt" in out


def test_bad_repo_path(sdk):
    from xuzhi.channels.wecom.askcode import CodeAsker
    a = CodeAsker(ROOT / "不存在的目录")
    out, _ = ask(a, "隐盾在哪实现")
    assert "代码目录不存在" in out
