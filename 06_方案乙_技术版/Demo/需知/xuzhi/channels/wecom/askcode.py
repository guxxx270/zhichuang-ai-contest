"""问码：基于需知自己的代码库回答问题（只读）。

和「需求分析」那条路完全分开：那条把消息当需求跑流水线，这条把消息当"关于这个项目的问题"，
用 Claude Agent SDK（与 Claude Code 同一引擎）在需知源码里 Glob/Grep/Read 找答案。

安全边界（无人值守，硬约束）：
- permission_mode="dontAsk"：任何会弹审批的调用直接拒绝，绝不等人
- 只放行 Read/Grep/Glob；Bash/Write/Edit/Web* 用 disallowed_tools 从模型视野里整体移除
- cwd 锁在需知 Demo 目录；setting_sources=[] 不加载本机任何 ~/.claude 配置
- max_turns / 墙钟超时 双重上限
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

Progress = Callable[[str], Awaitable[None]]

DENY_TOOLS = ["Bash", "Write", "Edit", "MultiEdit", "NotebookEdit", "WebFetch", "WebSearch",
              "Task", "Agent", "TodoWrite", "KillShell", "BashOutput", "AskUserQuestion"]

AUTH_ENV_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK",
                 "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")

AUTH_HINT = ("问码要走 Anthropic 格式的接口，和需求分析用的 OpenAI 兼容口不是同一套。"
             "请在 .env 里设置 ANTHROPIC_BASE_URL 与 ANTHROPIC_API_KEY（Bedrock/Vertex 用对应 CLAUDE_CODE_USE_* 变量）后重启。")

SYSTEM_PROMPT = """你是「需知」这个项目的代码答疑助手。当前工作目录就是需知 Demo 的源码（只读）。

提问的人是这个项目的作者（技术部工程师），他会问：某个功能在哪实现、为什么这么写、
要加或改一个东西涉及哪些文件、某段逻辑的口径是什么、哪里有坑、某个测试在测什么。

工作方式：
1. 先用 Glob 与 Grep 定位，再 Read 关键片段。不要凭印象回答——每个结论都要能指到 `文件:行号`。
2. 项目结构速记：`app.py` 是 Streamlit 界面；`xuzhi/pipeline/` 是流水线（intake 问清 / spec 写单 /
   estimate 估量 / tasks 双轨任务 / architect 定架 / prototype 出样）；`xuzhi/privacy.py` 是隐盾脱敏；
   `xuzhi/llm.py` 是 OpenAI 兼容客户端与 mock；`xuzhi/channels/wecom/` 是企微入口；
   `knowledge/` 与 `data/` 是知识库与样本；`tests/` 是 pytest。
3. 你没有写权限也没有 shell，不要尝试修改文件或执行命令。需要改动时只给出"改哪个文件的哪里、怎么改"。
4. 找不到就说没找到，不要编。问的是事实（在哪、有没有、几处）就直接给事实，不要硬凑段落。

输出（简体中文，企微 markdown：可用 **加粗**、列表、`行内代码`，不要表格和 HTML，控制在 600 字以内）：
先用一两句把问题直接回答掉；再给「依据」，最多 5 条，每条 `路径:行号` + 一句说明；
如果问题是"要怎么改"，最后给「怎么改」，按文件列出改动点。
"""


class ProgressTracker:
    """把 Agent 的工具调用翻成企微里看得懂的进度（整条刷新）。"""

    def __init__(self, progress: Progress, repo: Path, max_shown: int = 8):
        self.progress = progress
        self.repo = repo
        self.steps: List[str] = []
        self.last_text = ""
        self.started = time.monotonic()
        self.max_shown = max_shown

    def _rel(self, p: Any) -> str:
        s = str(p or "")
        try:
            return str(Path(s).resolve().relative_to(self.repo))
        except (ValueError, OSError):
            return s

    def describe(self, name: str, inp: Dict[str, Any]) -> str:
        if name == "Read":
            loc = self._rel(inp.get("file_path"))
            if inp.get("offset"):
                loc += f":{inp['offset']}"
            return f"读 {loc}"
        if name == "Grep":
            where = self._rel(inp.get("path")) or inp.get("glob") or ""
            return f"搜 `{inp.get('pattern', '')}`" + (f" in {where}" if where else "")
        if name == "Glob":
            return f"找文件 `{inp.get('pattern', '')}`"
        if name == "ToolSearch":
            return "加载工具"
        return name.split("__")[-1]

    async def on_tool(self, name: str, inp: Dict[str, Any]) -> None:
        self.steps.append(self.describe(name, inp))
        await self.progress(self.render())

    def render(self, note: str = "") -> str:
        shown = self.steps[-self.max_shown:]
        hidden = len(self.steps) - len(shown)
        lines = [f"🔎 正在翻需知的代码…（第 {len(self.steps)} 步，{int(time.monotonic() - self.started)}s）"]
        if hidden:
            lines.append(f"…（前 {hidden} 步省略）")
        lines += [f"- {s}" for s in shown]
        if note:
            lines += ["", note]
        return "\n".join(lines)

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started


class CodeAsker:
    """一个问题 → 在需知代码库里找答案 → 一段能发到企微的回答。"""

    name = "askcode"

    def __init__(self, repo: Path, max_turns: int = 20, timeout_seconds: int = 240, model: str = ""):
        self.repo = Path(repo).resolve()
        self.max_turns = max_turns
        self.timeout_seconds = timeout_seconds
        self.model = model
        self._sdk = None

    # ---------------- 准备 ----------------
    def _ensure_sdk(self) -> None:
        if self._sdk is not None:
            return
        try:
            import claude_agent_sdk as sdk  # noqa: WPS433
        except ImportError as e:
            raise RuntimeError("未安装 claude-agent-sdk：pip install -r requirements-wecom.txt") from e
        self._sdk = sdk

    @staticmethod
    def auth_configured() -> bool:
        return any(os.environ.get(k) for k in AUTH_ENV_KEYS)

    def _options(self, resume: Optional[str]):
        sdk = self._sdk
        from ... import sandbox   # 工具白名单 / 黑名单以 sandbox.yaml askcode 为准（缺省即上面的常量）

        kw: Dict[str, Any] = dict(
            cwd=str(self.repo),
            system_prompt=SYSTEM_PROMPT,
            allowed_tools=list(sandbox.get("askcode.allowed_tools") or ["Read", "Grep", "Glob"]),
            disallowed_tools=list(sandbox.get("askcode.denied_tools") or DENY_TOOLS),
            permission_mode="dontAsk",
            setting_sources=[] if not sandbox.get("askcode.load_user_settings", False) else None,
            max_turns=self.max_turns,
        )
        if kw["setting_sources"] is None:
            kw.pop("setting_sources")
        if self.model:
            kw["model"] = self.model
        if resume:
            kw["resume"] = resume
        return sdk.ClaudeAgentOptions(**kw)

    # ---------------- 主流程 ----------------
    async def ask(self, question: str, progress: Progress, session: Any = None) -> str:
        try:
            self._ensure_sdk()
        except RuntimeError as e:
            return f"问码不可用：{e}"
        if not self.repo.is_dir():
            return f"问码配置错误：代码目录不存在 {self.repo}"
        if not self.auth_configured():
            log.warning("未检测到 Anthropic 鉴权环境变量，仍尝试调用")

        resume = getattr(session, "ask_session_id", None) if session is not None else None
        tracker = ProgressTracker(progress, self.repo)
        await progress(tracker.render("已收到，正在启动问码引擎…"))
        try:
            return await asyncio.wait_for(
                self._run(question, resume, tracker, session), timeout=self.timeout_seconds
            )
        except asyncio.TimeoutError:
            log.warning("问码超时（%ss）", self.timeout_seconds)
            return tracker.render(f"⏱ 超时（{self.timeout_seconds}s）。把问题问得更具体一点会快很多，"
                                  "比如点名模块或文件。")
        except Exception as e:  # noqa: BLE001
            log.exception("问码失败")
            return tracker.render(f"❌ 问码出错：{type(e).__name__}: {str(e)[:200]}" + self._explain(str(e)))

    async def _run(self, question: str, resume: Optional[str], tracker: ProgressTracker, session: Any) -> str:
        try:
            return await self._query(question, resume, tracker, session)
        except Exception as e:  # noqa: BLE001
            if resume:
                log.warning("resume=%s 失败（%s），改为新会话重试", resume, e)
                if session is not None:
                    session.ask_session_id = None
                tracker.steps.clear()
                return await self._query(question, None, tracker, session)
            raise

    async def _query(self, question: str, resume: Optional[str], tracker: ProgressTracker, session: Any) -> str:
        sdk = self._sdk
        prompt = question if resume else f"## 问题\n{question}\n\n## 任务\n在当前代码库里查证后回答，按规定格式输出。"
        final: Optional[str] = None
        result = None
        async for msg in sdk.query(prompt=prompt, options=self._options(resume)):
            if isinstance(msg, sdk.AssistantMessage):
                for block in msg.content:
                    if isinstance(block, sdk.ToolUseBlock):
                        await tracker.on_tool(block.name, block.input or {})
                    elif isinstance(block, sdk.TextBlock) and block.text.strip():
                        tracker.last_text = block.text.strip()
            elif isinstance(msg, sdk.ResultMessage):
                result = msg
                final = msg.result
        if result is None:
            raise RuntimeError("问码引擎没有返回结果")
        if session is not None and getattr(result, "session_id", None):
            session.ask_session_id = result.session_id
        if result.is_error or not final:
            detail = final or tracker.last_text or f"subtype={result.subtype}"
            return tracker.render(f"❌ 没答完：{detail[:600]}" + self._explain(detail))
        steps = len(tracker.steps)
        cost = getattr(result, "total_cost_usd", 0) or 0
        footer = (f"\n\n— 翻了 {steps} 处 · {getattr(result, 'num_turns', 0)} 轮 · "
                  f"{(getattr(result, 'duration_ms', 0) or 0) / 1000:.0f}s · ${cost:.3f}")
        return final.strip() + footer

    @staticmethod
    def _explain(msg: str) -> str:
        low = msg.lower()
        if any(k in low for k in ("api key", "not logged in", "authentication", "unauthorized", "401")):
            return "\n\n" + AUTH_HINT
        if "402" in low or any(k in msg for k in ("套餐", "订单", "余额")) or "quota" in low or "billing" in low:
            return "\n\n模型账户余额/套餐不足或不含当前模型，去模型服务控制台看一下。"
        if "maximum budget" in low:
            return "\n\n触发了 SDK 的预算上限（它按接口返回的模型名自估价，走中转站常虚高）。"
        if "maximum number of turns" in low or "max turns" in low:
            return "\n\n到轮数上限还没查完。把问题缩小，或调大 WECOM_ASK_MAX_TURNS。"
        if "429" in low or "rate limit" in low or "overloaded" in low or "529" in low:
            return "\n\n模型服务限流/过载，等一两分钟再问。"
        return ""
