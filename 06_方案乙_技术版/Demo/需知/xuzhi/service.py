"""服务层：给 REST API / MCP / 其它入口共用的"跑一遍流水线 + 记账 + 序列化"。

Web（app.py）和企微入口各有自己的交互逻辑，这里只负责四件事：
1. run()：跑 analyze()，按 record 决定是否进一本账（首轮记一条；带 req_id 时：有答复才算一轮答复重算并写追问记忆，
   原话改了记需求变更，没答复只在拿到模型估时后写回；已对账的只读）；
   split() / run_split()：纪要直通车，一段纪要拆成几条，逐条跑规则流水线、各记一行账；
2. to_dict()：把 Analysis 序列化成纯 JSON（dataclass → dict），供接口返回；
3. version_diff()：同一条需求两版对比（默认最近两版），供 REST / MCP；
4. 模型：只用服务端 .env 的网关（sandbox.yaml api.accept_keys_in_request=false，请求体不接受 Key）；
   stages 指定哪几步调模型，接口只要哪部分就只跑哪部分。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass
from typing import Any

from . import config, versions
from .audit import RecordingSink, track
from .ledger import JIRA_SOURCE, Ledger, estimate_snapshot
from .llm import LLM
from .memory import Memory
from .pipeline import Analysis, analyze
from .pipeline.split import source_hint, split_requirements
from .privacy import Redactor


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
    """polish=True 时用服务端 .env 的模型（没配就自动 mock）；False 直接规则引擎。
    审计包一层 RecordingSink：首轮调用时还没有需求编号，记账后把编号补进这几行审计。"""
    if not polish:
        return None
    llm = LLM(channel=channel)
    track(llm)
    return llm


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
    stages: set[str] | None = None,
) -> RunResult:
    """stages：polish 时只让这几步调模型（intake / spec / estimate / prototype），None = 全部。
    带 req_id 记账时：编号不存在抛 LookupError，Jira 工单抛 ValueError（走 Jira 评估页重评）。"""
    text = (text or "").strip()
    if not text:
        raise ValueError("原话不能为空")
    ledger = ledger if ledger is not None else (Ledger() if record else None)
    memory = memory if memory is not None else Memory()
    req_id = int(req_id or 0)
    row = None
    if record and ledger is not None and req_id:
        # 先校验再跑：不认识的编号不再静默新建一行，Jira 工单不被零散需求的流水线改写
        row = ledger.get(req_id)
        if not row:
            raise LookupError(f"一本账里没有编号 {req_id}")
        if row.get("source") == JIRA_SOURCE:
            raise ValueError(f"#{req_id} 是 Jira 工单，请在 Jira 评估页重评")
    llm = make_llm(polish, channel)
    if llm is not None and row:
        llm.req_id = req_id   # 已有编号：模型调用审计直接记到这条需求上
    answers = {k: v for k, v in (answers or {}).items() if str(v or "").strip()}
    # 来源只用调用方给的（空串 = 按原话自动识别）；渠道记在「{channel}受理」事件里
    a = analyze(text, source, llm, answers=answers or None, mobile=mobile, client_view=client_view,
                llm_polish=llm is not None, memory=memory, model_stages=stages, count_memory_hits=not req_id)
    res = RunResult(a, req_id=req_id)
    if not record or ledger is None:
        return res
    if row:
        _write_rerun(ledger, memory, res, row, answers, channel)
        return res
    res.req_id = ledger.log_analysis(a)
    ledger.event(res.req_id, f"{channel}受理", "")
    if llm is not None and isinstance(llm.audit, RecordingSink):
        llm.audit.assign(res.req_id)
    if answers:
        ledger.update_answers(res.req_id, sum(1 for q in a.questions if q.answer.strip()), int(getattr(a.estimate, "unanswered_high", 0) or 0))
        res.learned = memory.learn(a.card, a.questions, res.req_id)
    return res


def _write_rerun(ledger: Ledger, memory: Memory, res: RunResult, row: dict, answers: dict[str, str], channel: str) -> None:
    """带 req_id 重算后写回：有答复才算一轮「业务答复重算」（答复数、追问记忆）；原话改了记需求变更；
    没答复时只在拿到模型估时后写回，不清掉已有估时、不抬高澄清轮次。已对账的需求只读。"""
    a, rid = res.analysis, res.req_id
    if row.get("status") == "已对账":
        a.notes.append("该需求已对账，本次结果未写回一本账")
        return
    if answers:
        ledger.event(rid, "业务答复重算", f"{len(answers)} 条（{channel}）")
        ledger.update_answers(rid, sum(1 for q in a.questions if q.answer.strip()), int(getattr(a.estimate, "unanswered_high", 0) or 0))
    if _text_changed(ledger, rid, a):
        # 带同一 req_id 发来改过的原话 = 需求变更：整条换成新分析，记「需求变更」并存一版
        ledger.record_change(rid, a, channel)
    elif answers:
        ledger.update_estimate(rid, a.estimate)
        ledger.add_version(rid, a, "业务答复重算")
    elif getattr(a.estimate, "status", "") == "ready":
        ledger.update_estimate(rid, a.estimate)
        ledger.add_version(rid, a, "模型估时")
    if answers:
        res.learned = memory.learn(a.card, a.questions, rid)
        if res.learned:
            ledger.event(rid, "追问记忆", f"记住 {res.learned} 条口径（{a.card.requester or '未注明'}）")


def split(text: str) -> list[dict[str, Any]]:
    """纪要直通车预览：识别到 2 条以上需求才返回各条，否则 []。
    返回的原文片段先过隐盾（与其它接口一样不回显明文手机号、账号）；拆分本身只用规则。"""
    red = Redactor()
    return [
        {"index": s.index, "requester": s.requester, "reason": s.reason, "title_hint": red.redact(s.title_hint).text,
         "body": red.redact(s.body).text, "text": red.redact(s.text).text}
        for s in split_requirements(text or "")
    ]


def run_split(
    text: str,
    *,
    record: bool = True,
    channel: str = "API",
    ledger: Ledger | None = None,
    memory: Memory | None = None,
) -> list[RunResult]:
    """纪要直通车：拆出的每条按规则跑一遍流水线（不调模型），record 时各记一行账（事件「纪要拆分受理」）。
    识别不到 2 条返回 []，调用方照常按一条需求走 run()。"""
    text = (text or "").strip()
    if not text:
        raise ValueError("原话不能为空")
    segments = split_requirements(text)
    if not segments:
        return []
    ledger = ledger if ledger is not None else (Ledger() if record else None)
    memory = memory if memory is not None else Memory()
    hint = source_hint(text)
    out: list[RunResult] = []
    for seg in segments:
        a = analyze(seg.text, hint, None, llm_polish=False, memory=memory)
        res = RunResult(a)
        if record and ledger is not None:
            res.req_id = ledger.log_analysis(a)
            ledger.event(res.req_id, "纪要拆分受理", f"同一段纪要拆出 {len(segments)} 条，第 {seg.index} 条（{channel}）")
        out.append(res)
    return out


def _text_changed(ledger: Ledger, req_id: int, a: Analysis) -> bool:
    """和最新一版的原话指纹比（台账不存原话）；还没有版本或旧版没有指纹，就当原话没变。"""
    last = ledger.latest_version(req_id) or {}
    sig = (last.get("snapshot") or {}).get("text_sig") or ""
    return bool(sig) and sig != versions.text_sig(a.redacted_text)


def _version_meta(v: dict) -> dict[str, Any]:
    return {"version": v["version"], "ts": v["ts"], "label": v["label"]}


def version_diff(ledger: Ledger, req_id: int, v_from: int = 0, v_to: int = 0) -> dict[str, Any]:
    """两版对比：结构化差异 + 变更说明 Markdown。默认最近两版；只给 to 时对比它的上一版。
    没有这条需求抛 LookupError；版本不够或版本号不对抛 ValueError。"""
    row = ledger.get(int(req_id))
    if not row:
        raise LookupError(f"一本账里没有编号 {req_id}")
    found = ledger.versions_of(int(req_id))
    n = len(found)
    if n < 2:
        raise ValueError(f"#{req_id} 只有 {n} 版，至少两版才能对比（业务答复重算、模型增强或需求变更后会存新版）")
    to = int(v_to or n)
    if not v_from and to == 1:
        raise ValueError("v1 是第一版，前面没有可比的版本；请同时给 from")
    frm = int(v_from or to - 1)
    for v in (frm, to):
        if not 1 <= v <= n:
            raise ValueError(f"#{req_id} 只有 v1～v{n}，没有 v{v}")
    if frm == to:
        raise ValueError("from 与 to 是同一版，请选两个不同的版本")
    old, new = found[frm - 1], found[to - 1]
    d = versions.diff(old["snapshot"], new["snapshot"], v_old=frm, v_new=to)
    title = new["snapshot"].get("title") or row.get("title") or ""
    return {"req_id": int(req_id), "title": title, "from": _version_meta(old), "to": _version_meta(new),
            "versions": [_version_meta(v) for v in found], "summary": d.summary, "diff": d.to_dict(),
            "markdown": versions.diff_markdown(d, title, old, new)}


def _similar(item: dict) -> dict[str, Any]:
    """旧字段 similar：改取历史参考原记录的精简字段（以小时为准，人天按 8 小时换算，兼容旧调用方）。"""
    est_h, act_h = item.get("estimated_hours"), item.get("actual_hours")
    return {"id": item.get("id"), "title": item.get("title"), "score": item.get("score"), "source": item.get("source", ""),
            "estimated_hours": est_h, "actual_hours": act_h,
            "estimate_days": None if est_h is None else round(est_h / 8, 3),
            "actual_days": None if act_h is None else round(act_h / 8, 3)}


def _model_task(task: dict) -> dict:
    """模型任务沿用 Task 字段存值：share 实为以前参考小时、coef 为两轨比值、note 为具体改动，接口按实际含义命名。"""
    out = dict(task)
    out["before_mid_hours"] = out.pop("share", None)
    out["ratio"] = out.pop("coef", None)
    out["change"] = out.pop("note", "")
    return out


def to_dict(a: Analysis, *, req_id: int = 0, include_spec: bool = True, include_prototype: bool = False,
            include_tasks: bool = True, include_adr: bool = True) -> dict[str, Any]:
    est, arch = a.estimate, a.architecture
    snapshot = estimate_snapshot(est)
    references = getattr(est, "references", None) or {}
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
            **{key: snapshot[key] for key in ("method", "status", "basis", "model", "code_files", "summary", "error", "assumptions", "before", "after", "references")},
            "hours_per_day": 8,
            "trad_days": {key: snapshot.get(key) for key in ("mid", "low", "high")},
            "ai_days": {key: snapshot.get("ai_" + key) for key in ("mid", "low", "high")},
            "saving_pct": snapshot.get("saving_pct"),
            "confidence": est.confidence,
            "confidence_reason": est.confidence_reason,
            "unanswered_high": est.unanswered_high,
            "delivery": est.delivery,
            # dims / similar 保留键名兼容旧调用方，内容改取规则参考的六维评分与历史参考原记录
            "dims": dict((references.get("rule") or {}).get("dims") or est.dims or {}),
            "who": dict(snapshot.get("who") or {}),
            "phases": dict(snapshot.get("phases") or {}),
            "ai_phases": dict(snapshot.get("ai_phases") or {}),
            "similar": [_similar(item) for item in (references.get("history") or {}).get("items") or []],
            "comparison": dict(getattr(est, "comparison", None) or {}),   # 本次模型估时采用的以前 / 现在口径
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
        tasks = snapshot["tasks"]
        out["estimate"]["tasks"] = [_model_task(t) for t in tasks] if snapshot.get("method") == "llm" else tasks
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
