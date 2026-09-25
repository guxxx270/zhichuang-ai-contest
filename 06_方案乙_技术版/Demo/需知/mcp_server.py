"""需知 · MCP 服务：把问清 / 写单 / 估量 / 定架 暴露成 MCP 工具，任何支持 MCP 的智能体或 IDE 都能调。

启动：
  python mcp_server.py                         # stdio（给 Claude Desktop / Cursor / Claude Code 等本机客户端）
  python mcp_server.py --transport streamable-http --port 8771   # HTTP（给公司 AI 平台里的智能体，只听 127.0.0.1）

客户端配置示例（Claude Code）：
  claude mcp add xuzhi -- python /path/to/需知/mcp_server.py
Cursor / Claude Desktop 的 mcpServers：{"xuzhi": {"command": "python", "args": ["/path/to/需知/mcp_server.py"]}}

约定：工具默认走规则引擎（毫秒级、可复现、不调模型）；polish=true 才用服务端 .env 里的模型润色。
每次调用同样进一本账（渠道 MCP）。
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

from xuzhi import config, sandbox, service
from xuzhi.ledger import Ledger
from xuzhi.memory import Memory

mcp = FastMCP(
    "xuzhi",
    instructions=(
        f"{config.PRODUCT_NAME}（XuZhi）：期货公司技术部的需求分析智能体。把业务的一堆话交给 xuzhi_clarify 先问清，"
        "拿到业务答复后用 answers 重算；xuzhi_estimate 给双轨工时（传统 vs AI 协同）；xuzhi_architect 给数据地图与 IT 决策；"
        "xuzhi_spec 出双版需求单。所有数字由程序算，模型只组织语言。原话进入前已本地脱敏。"
    ),
)

_ledger: Ledger | None = None
_memory: Memory | None = None


def _deps() -> tuple[Ledger, Memory]:
    global _ledger, _memory
    if _ledger is None:
        _ledger = Ledger()
    if _memory is None:
        _memory = Memory()
    return _ledger, _memory


def _dump(d: Any) -> str:
    return json.dumps(d, ensure_ascii=False, indent=1)


def _run(text: str, answers: dict[str, str] | None, req_id: int, polish: bool, record: bool = True) -> service.RunResult:
    ledger, memory = _deps()
    return service.run(text, answers=answers, polish=polish, req_id=req_id, channel="MCP", record=record,
                       ledger=ledger if record else None, memory=memory)


@mcp.tool()
def xuzhi_clarify(text: str, answers: dict[str, str] | None = None, req_id: int = 0, polish: bool = False) -> str:
    """问清：从一堆话里抽出需求卡片，按「期货 IT 需求追问知识库」生成待确认清单（影响高/中/低，附"不问会怎样"与默认假设）
    和一段可直接发给业务的确认消息。业务答复后把 answers（问题 id → 答复）和 req_id 传回来重算。
    同一提出方答过的题会自动沿用（recalled 非空），不再问。"""
    res = _run(text, answers, req_id, polish)
    d = service.brief(res.analysis, res.req_id)
    return _dump({k: d[k] for k in ("req_id", "engine", "redacted", "recalled", "card", "questions", "confirm_message")})


@mcp.tool()
def xuzhi_estimate(text: str, answers: dict[str, str] | None = None, req_id: int = 0) -> str:
    """估量（双轨）：六维复杂度 + 历史需求类比 → 传统人天；任务逐项标 AI 能做 / AI 做人审 / 人必须做 → AI 协同人天、
    省时比例、建议交付方式（技术部主做 vs 业务在 AI 平台自建）。纯程序计算。"""
    res = _run(text, answers, req_id, polish=False)
    d = service.to_dict(res.analysis, req_id=res.req_id, include_spec=False, include_adr=False)
    return _dump({"req_id": d["req_id"], "title": d["card"]["title"], "estimate": d["estimate"]})


@mcp.tool()
def xuzhi_architect(text: str, answers: dict[str, str] | None = None, req_id: int = 0) -> str:
    """定架：复用发现（对照公司系统目录）、数据地图（每个数据项定位到 系统·数据域·接口·时效，标可复用/需申请/需新建/时效不符）、
    IT 决策清单（选项/建议/影响）与 ADR（架构决策记录，Markdown）。"""
    res = _run(text, answers, req_id, polish=False)
    d = service.to_dict(res.analysis, req_id=res.req_id, include_spec=False, include_tasks=False)
    return _dump({"req_id": d["req_id"], "title": d["card"]["title"], "architecture": d["architecture"]})


@mcp.tool()
def xuzhi_spec(text: str, answers: dict[str, str] | None = None, req_id: int = 0, version: str = "both", polish: bool = False) -> str:
    """写单：双版需求单 Markdown——业务版一页纸（可签字）与技术版（用户故事、Given/When/Then 验收、数据字典、接口草案、非功能）。
    version: business / tech / both。"""
    res = _run(text, answers, req_id, polish)
    a = res.analysis
    out: dict[str, Any] = {"req_id": res.req_id, "title": a.card.title}
    if version in ("business", "both"):
        out["business_md"] = a.spec.business_md
    if version in ("tech", "both"):
        out["tech_md"] = a.spec.tech_md
    return _dump(out)


@mcp.tool()
def xuzhi_analyze(text: str, answers: dict[str, str] | None = None, req_id: int = 0, polish: bool = False) -> str:
    """整条链一次跑完（问清 + 写单 + 估量 + 定架），返回完整 JSON（不含原型 HTML）。"""
    res = _run(text, answers, req_id, polish)
    return _dump(service.to_dict(res.analysis, req_id=res.req_id))


@mcp.tool()
def xuzhi_ledger(n: int = 20) -> str:
    """一本账：最近 n 条需求（编号、标题、部门、状态、问题数/已答复、传统与 AI 协同人天、实际人天）与经营看板汇总。"""
    ledger, memory = _deps()
    return _dump({"recent": ledger.recent(n), "dashboard": ledger.dashboard(), "memory": memory.stats()})


@mcp.tool()
def xuzhi_reconcile(req_id: int, actual_days: float, ai_assisted: bool = False, ai_share: float = 0.0, note: str = "") -> str:
    """对账：交付后回写实际人天 → 状态"已对账" → 写入历史需求库（估算飞轮）。返回估算偏差。"""
    ledger, _ = _deps()
    return _dump(ledger.reconcile(int(req_id), float(actual_days), bool(ai_assisted), float(ai_share), note))


@mcp.resource("xuzhi://sandbox")
def sandbox_resource() -> str:
    """需知沙箱策略（sandbox.yaml）：数据去哪了、谁能用、能碰什么。"""
    return sandbox.raw_text()


@mcp.resource("xuzhi://info")
def info_resource() -> str:
    return _dump(service.info())


def main() -> None:
    ap = argparse.ArgumentParser(description=f"{config.PRODUCT_NAME} · MCP 服务")
    ap.add_argument("--transport", choices=["stdio", "streamable-http", "sse"], default="stdio")
    ap.add_argument("--host", default=os.getenv("XUZHI_MCP_HOST", "").strip() or str(sandbox.get("api.bind") or "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.getenv("XUZHI_MCP_PORT", "8771") or 8771))
    args = ap.parse_args()
    if args.transport != "stdio":
        exposed = args.host not in ("127.0.0.1", "localhost", "::1")
        if exposed and sandbox.get("api.token_required_if_exposed", True) and not os.getenv("XUZHI_MCP_ALLOW_EXPOSED"):
            raise SystemExit("拒绝启动：MCP HTTP 只允许监听本机；确需对外请放在公司网关（带鉴权）之后，并设 XUZHI_MCP_ALLOW_EXPOSED=1")
        mcp.settings.host, mcp.settings.port = args.host, args.port
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
