"""服务层：给 REST API / MCP / 其它入口共用的"跑一遍流水线 + 记账 + 序列化"。

Web（app.py）和企微入口各有自己的交互逻辑，这里只负责三件事：
1. run()：跑 analyze()，按 record 决定是否进一本账（首轮记一条；带 req_id 的答复轮更新答复数并写追问记忆）；
2. to_dict()：把 Analysis 序列化成纯 JSON（dataclass → dict），供接口返回；
3. 模型：只用服务端 .env 的网关（sandbox.yaml api.accept_keys_in_request=false，请求体不接受 Key）。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any

from . import config
from .ledger import Ledger
from .llm import LLM
from .memory import Memory
from .pipeline import Analysis, analyze


@dataclass
class RunResult:
    analysis: Analysis
    req_id: int = 0
    learned: int = 0


def _plain(obj: Any) -> Any:
    """dataclass / list / tuple / dict → 纯 JSON 结构。"""
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: _plain(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(x) for x in obj]
    return obj


def make_llm(polish: bool, channel: str) -> LLM | None:
    """polish=True 时用服务端 .env 的模型（没配就自动 mock）；False 直接规则引擎。"""
    if not polish:
        return None
    return LLM(channel=channel)


def run(
    text: str,
    *,
    source: str = "",
    answers: dict[str, str] | None = None,
    polish: bool = False,
    req_id: int = 0,
    channel: str = "API",
    record: bool = True,
    ledger: Ledger | None = None,
    memory: Memory | None = None,
    mobile: bool = False,
    client_view: bool = False,
) -> RunResult:
    text = (text or "").strip()
    if not text:
        raise ValueError("原话不能为空")
    ledger = ledger if ledger is not None else (Ledger() if record else None)
    memory = memory if memory is not None else Memory()
    llm = make_llm(polish, channel)
    answers = {k: v for k, v in (answers or {}).items() if str(v or "").strip()}
    a = analyze(text, source or channel, llm, answers=answers or None, mobile=mobile, client_view=client_view,
                llm_polish=llm is not None, memory=memory)
    res = RunResult(a, req_id=int(req_id or 0))
    if not record or ledger is None:
        return res
    if res.req_id and ledger.get(res.req_id):
        n_ans = sum(1 for q in a.questions if q.answer.strip())
        ledger.event(res.req_id, "业务答复重算", f"{len(answers)} 条（{channel}）")
        ledger.update_answers(res.req_id, n_ans, int(getattr(a.estimate, "unanswered_high", 0) or 0))
        if answers:
            res.learned = memory.learn(a.card, a.questions, res.req_id)
            if res.learned:
                ledger.event(res.req_id, "追问记忆", f"记住 {res.learned} 条口径（{a.card.requester or '未注明'}）")
    else:
        res.req_id = ledger.log_analysis(a)
        ledger.event(res.req_id, f"{channel}受理", "")
        if answers:
            ledger.update_answers(res.req_id, sum(1 for q in a.questions if q.answer.strip()), int(getattr(a.estimate, "unanswered_high", 0) or 0))
            res.learned = memory.learn(a.card, a.questions, res.req_id)
    return res


def to_dict(a: Analysis, *, req_id: int = 0, include_spec: bool = True, include_prototype: bool = False,
            include_tasks: bool = True, include_adr: bool = True) -> dict[str, Any]:
    est, arch = a.estimate, a.architecture
    out: dict[str, Any] = {
        "req_id": int(req_id or 0),
        "engine": a.engine,
        "seconds": a.seconds,
        "redacted": a.redacted,
        "recalled": int(getattr(a, "recalled", 0) or 0),
        "notes": list(a.notes),
        "card": json.loads(a.card.to_json()),
        "questions": [
            {"id": q.id, "category": q.category, "tag": q.tag, "impact": q.impact, "question": q.question, "why": q.why,
             "default": q.default, "answer": q.answer, "recalled": getattr(q, "recalled", "")}
            for q in a.questions
        ],
        "confirm_message": a.message,
        "estimate": {
            "trad_days": {"mid": est.mid, "low": est.low, "high": est.high},
            "ai_days": {"mid": est.ai_mid, "low": est.ai_low, "high": est.ai_high},
            "saving_pct": est.saving_pct,
            "confidence": est.confidence,
            "confidence_reason": est.confidence_reason,
            "unanswered_high": est.unanswered_high,
            "delivery": est.delivery,
            "dims": dict(est.dims),
            "who": dict(est.who),
            "phases": dict(est.phases),
            "similar": [{"id": s.id, "title": s.title, "score": s.score, "estimate_days": s.estimate_days, "actual_days": s.actual_days} for s in est.similar],
        },
        "architecture": {
            "coverage_line": arch.coverage_line,
            "stack": [list(x) for x in arch.stack],
            "reuse": [{"system": r.system, "owner": r.owner, "matched": list(r.matched), "integration": r.integration, "maturity": r.maturity} for r in arch.reuse],
            "decisions": [{"id": d.id, "topic": d.topic, "question": d.question, "options": [list(o) for o in d.options],
                           "recommended": d.recommended, "reason": d.reason, "impact": d.impact} for d in arch.decisions],
            "data_map": _plain(list(getattr(arch, "data_map", []) or [])),
            "data_map_line": getattr(arch, "data_map_line", ""),
            "data_map_stats": dict(getattr(arch, "data_map_stats", {}) or {}),
        },
    }
    if include_tasks:
        out["estimate"]["tasks"] = [{"id": t.id, "name": t.name, "phase": t.phase, "who": t.who, "trad_days": t.trad_days, "ai_days": t.ai_days} for t in est.tasks]
    if include_adr:
        out["architecture"]["adr_md"] = arch.adr_md
    if include_spec:
        out["spec"] = {"business_md": a.spec.business_md, "tech_md": a.spec.tech_md}
    if include_prototype:
        out["prototype_html"] = a.prototype_html
    return out


def brief(a: Analysis, req_id: int = 0) -> dict[str, Any]:
    """给 MCP 工具用的紧凑版（问清 + 估量 + 拍板点），不带需求单与原型。"""
    d = to_dict(a, req_id=req_id, include_spec=False, include_prototype=False, include_tasks=False, include_adr=False)
    d["architecture"].pop("stack", None)
    return d


def info() -> dict[str, Any]:
    from . import knowledge, sandbox

    return {
        "product": config.PRODUCT_NAME,
        "slogan": config.PRODUCT_SLOGAN,
        "llm_mode": config.resolved_mode(),
        "probes": len(knowledge.probes()),
        "systems": len(knowledge.systems()),
        "history": len(knowledge.history()),
        "sandbox": sandbox.summary(),
    }
