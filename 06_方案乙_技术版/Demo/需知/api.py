"""需知 · REST API（FastAPI）。启动：python api.py  或  双击 启动接口.command

给工单系统、公司 AI 平台、脚本用的接口；页面与企微之外的第三个入口。
- 只用服务端 .env 里的模型网关；请求体不接受 Key（sandbox.yaml api.accept_keys_in_request=false）
- 默认只监听 127.0.0.1（sandbox.yaml api.bind）；要对外必须显式设 XUZHI_API_HOST，且必须配 XUZHI_API_TOKEN
- 每次分析同样进一本账（渠道 API），模型调用同样进审计
接口文档：启动后打开 http://127.0.0.1:8770/docs
"""
from __future__ import annotations

import os
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from xuzhi import config, sandbox, service
from xuzhi.audit import Audit
from xuzhi.ledger import STATUSES, Ledger
from xuzhi.memory import Memory

API_TOKEN = os.getenv("XUZHI_API_TOKEN", "").strip()
DEFAULT_PORT = int(os.getenv("XUZHI_API_PORT", "8770") or 8770)

app = FastAPI(
    title=f"{config.PRODUCT_NAME} API",
    version="0.4",
    description="一堆话进来，一份能开工的需求出去。问清 / 写单 / 估量（双轨）/ 定架（数据地图 + IT 决策）/ 一本账。",
)

_ledger: Ledger | None = None
_memory: Memory | None = None
_audit: Audit | None = None


def ledger() -> Ledger:
    global _ledger
    if _ledger is None:
        _ledger = Ledger()
    return _ledger


def memory() -> Memory:
    global _memory
    if _memory is None:
        _memory = Memory()
    return _memory


def audit() -> Audit:
    global _audit
    if _audit is None:
        _audit = Audit()
    return _audit


def require_token(authorization: Optional[str] = Header(default=None), x_api_token: Optional[str] = Header(default=None)) -> None:
    """配了 XUZHI_API_TOKEN 就必须带 Authorization: Bearer <token> 或 X-API-Token。"""
    if not API_TOKEN:
        return
    given = (x_api_token or "").strip()
    if not given and authorization and authorization.lower().startswith("bearer "):
        given = authorization[7:].strip()
    if given != API_TOKEN:
        raise HTTPException(status_code=401, detail="未授权：请带 Authorization: Bearer <XUZHI_API_TOKEN>")


# ---------------- 模型 ----------------
class AnalyzeIn(BaseModel):
    text: str = Field(..., description="需求原话：微信、纪要、邮件、Word 里的一堆话", min_length=1)
    source: str = Field("", description="来源提示（企业微信 / 会议纪要 / 邮件 / 需求单…），空则自动识别")
    answers: dict[str, str] = Field(default_factory=dict, description="业务答复：问题 id → 答复")
    req_id: int = Field(0, description="答复重算时传上一次返回的 req_id，一本账在同一条上更新；0 = 新需求")
    polish: bool = Field(False, description="true = 调用服务端 .env 配置的模型润色（没配则自动规则）；false = 纯规则引擎（毫秒级、可复现）")
    record: bool = Field(True, description="是否进一本账")
    include_spec: bool = Field(True, description="返回需求单（业务版 / 技术版 Markdown）")
    include_prototype: bool = Field(False, description="返回可点原型 HTML（较大）")
    mobile: bool = False
    client_view: bool = False


class StatusIn(BaseModel):
    status: str = Field(..., description="受理 / 已确认 / 开发中 / 已交付")


class ReconcileIn(BaseModel):
    actual_days: float = Field(..., ge=0)
    ai_assisted: bool = False
    ai_share: float = Field(0.0, ge=0, le=1)
    note: str = ""


# ---------------- 接口 ----------------
@app.get("/api/v1/health")
def health() -> dict[str, Any]:
    return {"ok": True, **service.info()}


@app.post("/api/v1/analyze", dependencies=[Depends(require_token)])
def analyze_endpoint(body: AnalyzeIn) -> dict[str, Any]:
    """整条链：隐盾 → 问清 → 写单 → 估量 → 定架 → 出样。答复重算时带 answers + req_id。"""
    try:
        res = service.run(body.text, source=body.source, answers=body.answers, polish=body.polish, req_id=body.req_id,
                          channel="API", record=body.record, ledger=ledger() if body.record else None, memory=memory(),
                          mobile=body.mobile, client_view=body.client_view)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    out = service.to_dict(res.analysis, req_id=res.req_id, include_spec=body.include_spec, include_prototype=body.include_prototype)
    out["learned"] = res.learned
    return out


@app.post("/api/v1/clarify", dependencies=[Depends(require_token)])
def clarify_endpoint(body: AnalyzeIn) -> dict[str, Any]:
    """只要问清：需求卡 + 待确认清单 + 发给业务的确认消息（其余字段省略）。"""
    res = service.run(body.text, source=body.source, answers=body.answers, polish=body.polish, req_id=body.req_id,
                      channel="API", record=body.record, ledger=ledger() if body.record else None, memory=memory())
    d = service.to_dict(res.analysis, req_id=res.req_id, include_spec=False, include_tasks=False, include_adr=False)
    return {k: d[k] for k in ("req_id", "engine", "seconds", "redacted", "recalled", "card", "questions", "confirm_message")}


@app.get("/api/v1/requirements", dependencies=[Depends(require_token)])
def list_requirements(n: int = Query(20, ge=1, le=200)) -> list[dict[str, Any]]:
    return ledger().recent(n)


@app.get("/api/v1/requirements/{req_id}", dependencies=[Depends(require_token)])
def get_requirement(req_id: int) -> dict[str, Any]:
    row = ledger().get(req_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"一本账里没有编号 {req_id}")
    row["events"] = ledger().events_of(req_id)
    return row


@app.post("/api/v1/requirements/{req_id}/status", dependencies=[Depends(require_token)])
def set_status(req_id: int, body: StatusIn) -> dict[str, Any]:
    if body.status not in STATUSES or body.status == "已对账":
        raise HTTPException(status_code=422, detail=f"状态只能是 {' / '.join(s for s in STATUSES if s != '已对账')}；已对账请走 reconcile")
    if not ledger().get(req_id):
        raise HTTPException(status_code=404, detail=f"一本账里没有编号 {req_id}")
    ledger().set_status(req_id, body.status)
    return {"ok": True, "req_id": req_id, "status": body.status}


@app.post("/api/v1/requirements/{req_id}/reconcile", dependencies=[Depends(require_token)])
def reconcile(req_id: int, body: ReconcileIn) -> dict[str, Any]:
    try:
        return {"ok": True, "req_id": req_id, **ledger().reconcile(req_id, body.actual_days, body.ai_assisted, body.ai_share, body.note)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@app.get("/api/v1/dashboard", dependencies=[Depends(require_token)])
def dashboard() -> dict[str, Any]:
    d = ledger().dashboard()
    d["memory"] = memory().stats()
    d["llm_audit"] = audit().stats()
    return d


@app.get("/api/v1/digest", dependencies=[Depends(require_token)])
def digest(fmt: str = Query("json", pattern="^(json|markdown)$")) -> Any:
    """催办摘要（附加功能）：待确认超期 / 已交付未对账 / 本周受理。只有数量、标题、天数。"""
    from xuzhi import reminders

    d = reminders.build_digest(ledger())
    if fmt == "markdown":
        return {"markdown": reminders.render_digest(d)}
    return reminders.digest_to_dict(d)


@app.get("/api/v1/audit/llm", dependencies=[Depends(require_token)])
def audit_llm(n: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    return {"stats": audit().stats(), "recent": audit().recent(n)}


@app.get("/api/v1/sandbox")
def sandbox_policy() -> dict[str, Any]:
    return {"summary": sandbox.summary(), "policy": sandbox.policy(), "note": sandbox.note(),
            "enforced_by": sandbox.ENFORCED_BY}


# ---------------- 启动 ----------------
def main() -> None:
    import argparse

    import uvicorn

    ap = argparse.ArgumentParser(description=f"{config.PRODUCT_NAME} · REST API")
    ap.add_argument("--host", default=os.getenv("XUZHI_API_HOST", "").strip() or str(sandbox.get("api.bind") or "127.0.0.1"))
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()
    exposed = args.host not in ("127.0.0.1", "localhost", "::1")
    if exposed and not API_TOKEN and sandbox.get("api.token_required_if_exposed", True):
        raise SystemExit("拒绝启动：监听非本机地址必须配置 XUZHI_API_TOKEN（sandbox.yaml api.token_required_if_exposed）")
    print(f"{config.PRODUCT_NAME} API · http://{args.host}:{args.port}/docs · 模型 {config.resolved_mode()} · "
          f"{'Token 已启用' if API_TOKEN else '未配 Token（仅本机）'}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
