"""需知 · MCP 服务：把问清 / 写单 / 估量 / 定架 暴露成 MCP 工具，任何支持 MCP 的智能体或 IDE 都能调。

启动：
  python mcp_server.py                         # stdio（给 Claude Desktop / Cursor / Claude Code 等本机客户端）
  python mcp_server.py --transport streamable-http --port 8771   # HTTP（给公司 AI 平台里的智能体，只听 127.0.0.1）
  放到公司网关后对外：XUZHI_MCP_HOST=0.0.0.0 XUZHI_MCP_ALLOW_EXPOSED=1，
  并在 XUZHI_MCP_ALLOWED_HOSTS 写网关转发时带的 Host（逗号分隔，如 mcp.corp.example,10.0.0.5:8771）

客户端配置示例（Claude Code）：
  claude mcp add xuzhi -- python /path/to/需知/mcp_server.py
Cursor / Claude Desktop 的 mcpServers：{"xuzhi": {"command": "python", "args": ["/path/to/需知/mcp_server.py"]}}

约定：工具默认走规则引擎（毫秒级、可复现、不调模型）；polish=true 才用服务端 .env 里的模型，
且只做该工具要返回的部分（问清只润色追问，写单再润色需求单，整条链再加模型估时）。
每次调用同样进一本账（渠道 MCP）；带 req_id 时编号必须在一本账里。
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any

try:
    from mcp.server.fastmcp import FastMCP
except ModuleNotFoundError as e:   # 没装 mcp，或装成了 2.x（FastMCP 已改名、接口也变了）
    raise SystemExit("需知 MCP 服务需要 mcp 1.x：pip install -r requirements-api.txt（已锁 mcp<2）") from e

from xuzhi import config, sandbox, service
from xuzhi.ledger import Ledger
from xuzhi.memory import Memory

mcp = FastMCP(
    "xuzhi",
    instructions=(
        f"{config.PRODUCT_NAME}（XuZhi）：期货公司技术部的需求分析智能体。把业务的一堆话交给 xuzhi_clarify 先问清，"
        "拿到业务答复后用 answers 重算；xuzhi_estimate 给规则模板与历史记录两路参考（只跑规则）；"
        "要模型逐项估时用 xuzhi_analyze(polish=true)；xuzhi_architect 给数据地图与 IT 决策；"
        "xuzhi_spec 出双版需求单；一段会议纪要里有几条需求时先用 xuzhi_split 拆开、逐条受理；"
        "需求改了就带同一 req_id 重发，xuzhi_diff 看两版差异与需重新确认项。"
        "默认只跑规则：出初稿与规则模板、历史两路参考；polish=true 时模型只做该工具返回的部分（xuzhi_clarify 润色追问，"
        "xuzhi_spec 再润色需求单，xuzhi_analyze 再加模型独立估时：程序校验汇总，与规则参考并列不混算）。"
        "原话进入前已本地脱敏。"
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


def _run(text: str, answers: dict[str, str] | None, req_id: int, polish: bool, record: bool = True,
         stages: set[str] | None = None) -> service.RunResult:
    """stages：polish 时只让这几步调模型，工具不返回的部分不白等模型。"""
    ledger, memory = _deps()
    return service.run(text, answers=answers, polish=polish, req_id=req_id, channel="MCP", record=record,
                       ledger=ledger if record else None, memory=memory, stages=stages)


@mcp.tool()
def xuzhi_clarify(text: str, answers: dict[str, str] | None = None, req_id: int = 0, polish: bool = False) -> str:
    """问清：从一堆话里抽出需求卡片，按「期货 IT 需求追问知识库」生成待确认清单（影响高/中/低，附"不问会怎样"与默认假设）
    和一段可直接发给业务的确认消息。业务答复后把 answers（问题 id → 答复）和 req_id 传回来重算。
    同一提出方答过的题会自动沿用（recalled 非空），不再问。polish=true 时模型只润色问清。"""
    res = _run(text, answers, req_id, polish, stages={"intake"})
    d = service.brief(res.analysis, res.req_id)
    return _dump({k: d[k] for k in ("req_id", "engine", "redacted", "recalled", "card", "questions", "confirm_message")})


@mcp.tool()
def xuzhi_estimate(text: str, answers: dict[str, str] | None = None, req_id: int = 0) -> str:
    """估量参考：规则模板（六维复杂度 × 手写权重 → 以前 / 现在 小时，附公式、模板工作项与前提）与历史记录
    （相似需求当时的估算、实际工时与开发方式），两路各自说明依据、不加权混合。本工具只跑规则、不调模型，
    estimate.status 为 unavailable（没有模型估时）；要模型独立估时用 xuzhi_analyze(polish=true)。单位小时，8 小时 = 1 人天。"""
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
    if version not in ("business", "tech", "both"):
        raise ValueError("version 只能是 business / tech / both")
    res = _run(text, answers, req_id, polish, stages={"intake", "spec"})
    a = res.analysis
    out: dict[str, Any] = {"req_id": res.req_id, "title": a.card.title}
    if version in ("business", "both"):
        out["business_md"] = a.spec.business_md
    if version in ("tech", "both"):
        out["tech_md"] = a.spec.tech_md
    return _dump(out)


@mcp.tool()
def xuzhi_analyze(text: str, answers: dict[str, str] | None = None, req_id: int = 0, polish: bool = False) -> str:
    """整条链一次跑完（问清 + 写单 + 估量 + 定架），返回完整 JSON（不含原型 HTML）。
    polish=true 时模型润色问清与需求单并逐项估时（不改原型）。"""
    res = _run(text, answers, req_id, polish, stages={"intake", "spec", "estimate"})
    return _dump(service.to_dict(res.analysis, req_id=res.req_id))


@mcp.tool()
def xuzhi_split(text: str, record: bool = False) -> str:
    """纪要直通车：一段会议纪要 / 长消息里有几条互不相干的需求时按规则拆开（编号条目、提出方切换、「还有一件事」等连接词），
    返回每条的提出方、预览与拆分依据；参会名单、「下周再议」这类决议不算需求。
    record=true 时逐条跑问清 / 估量 / 定架并各记一行账，results 里带各自的 req_id，之后可带 req_id 调 xuzhi_clarify 等继续。
    segments 为空表示只有一条需求，直接用 xuzhi_analyze。"""
    ledger, memory = _deps()
    segments = service.split(text)
    out: dict[str, Any] = {"count": len(segments), "segments": segments}
    if record and segments:
        results = service.run_split(text, record=True, channel="MCP", ledger=ledger, memory=memory)
        out["results"] = [service.brief(r.analysis, r.req_id) for r in results]
    return _dump(out)


@mcp.tool()
def xuzhi_ledger(n: int = 20) -> str:
    """一本账：最近 n 条需求（编号、标题、部门、状态、问题数/已答复、传统与 AI 协同人天、实际人天）与经营看板汇总。"""
    ledger, memory = _deps()
    return _dump({"recent": ledger.recent(n), "dashboard": ledger.dashboard(), "memory": memory.stats()})


@mcp.tool()
def xuzhi_diff(req_id: int, from_version: int = 0, to_version: int = 0) -> str:
    """需求版本对比：同一条需求（req_id）两版之间改了什么——功能点、要素、待确认与答复、工时（模型估时与规则参考分开比，不混算）、
    数据地图、IT 决策，以及需重新确认的事项，附可直接发给业务 / 开发的变更说明 Markdown。
    默认对比最近两版（0 = 自动）；受理、业务答复重算、模型增强、需求变更（带同一 req_id 发来改过的原话）时自动存版本。"""
    ledger, _ = _deps()
    return _dump(service.version_diff(ledger, int(req_id), int(from_version or 0), int(to_version or 0)))


@mcp.tool()
def xuzhi_reconcile(req_id: int, actual_days: float, ai_assisted: bool = False, ai_share: float = 0.0, note: str = "") -> str:
    """对账：回写实际人天；有有效两轨估算时计算偏差并追加历史参考（不自动校准）。状态记为「已对账」，返回估算偏差。"""
    ledger, _ = _deps()
    return _dump(ledger.reconcile(int(req_id), float(actual_days), bool(ai_assisted), float(ai_share), note))


@mcp.resource("xuzhi://sandbox")
def sandbox_resource() -> str:
    """需知沙箱策略（sandbox.yaml）：数据去哪了、谁能用、能碰什么。"""
    return sandbox.raw_text()


@mcp.resource("xuzhi://info")
def info_resource() -> str:
    return _dump(service.info())


def _exposed_security(host: str):
    """对外监听时的 Host / Origin 白名单：FastMCP 构造时已按 127.0.0.1 定死，放到网关后不改就全部 421。
    XUZHI_MCP_ALLOWED_HOSTS 写网关转发时带的 Host（逗号分隔，不写端口则任意端口）；没配就用监听地址；本机始终可连。"""
    from mcp.server.transport_security import TransportSecuritySettings

    given = [h.strip() for h in os.getenv("XUZHI_MCP_ALLOWED_HOSTS", "").split(",") if h.strip()] or [f"{host}:*"]
    hosts: list[str] = []
    for h in given + ["127.0.0.1:*", "localhost:*"]:
        base = h[:-2] if h.endswith(":*") else h
        items = [h] if ":" in base.rsplit("]", 1)[-1] else [base, f"{base}:*"]   # 没写端口：带不带端口都放行
        hosts += [x for x in items if x not in hosts]
    origins: list[str] = []
    for h in hosts:
        origins += [o for o in (f"http://{h}", f"https://{h}") if o not in origins]
    return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=origins)


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
        if exposed:
            mcp.settings.transport_security = _exposed_security(args.host)
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
