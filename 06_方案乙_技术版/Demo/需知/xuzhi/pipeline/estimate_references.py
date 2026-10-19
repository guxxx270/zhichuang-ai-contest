"""与模型估时并列的固定规则及历史参考；两者都不进入模型提示词。"""
from __future__ import annotations

import math
from decimal import Decimal, ROUND_HALF_UP

from .. import knowledge
from .incremental import build_change_work, calculation_change_scope
from .intake import Card, Question
from .rule_reference import WEIGHTS, build_tasks, score_dims


_RULE_LABEL = "固定规则参考（未校准）"
_RULE_LIMITS = [
    "这是固定任务、参数和系数生成的规则参考，不是模型分析或历史实测，也不代表交付承诺。",
    "未读取或验证现有代码；字段、复用能力与实施范围仍需人工核对。",
    "8 小时按 1 人天换算；数字表示投入工时，不含排期、审批和反馈等待。",
]
_HISTORY_LIMITS = [
    "只按需求类型和关键词查找原记录；关键词重合不等于同规模、同实现或相同人员能力。",
    "历史估算和实际投入属于同一个历史项目，不能分别充当本需求的以前/现在，也不据此生成当前需求工时。",
    "虚构演示、导入记录、台账对账及来源未确认的记录分别标注；导入或对账不代表已核验真实性。",
    "估算字段沿用库内原值；旧导入流程可能用实际值补缺失估算，未核验原始填写，不能直接用于估算偏差或效率结论。",
]


def _round(value) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _track(low=None, mid=None, high=None) -> dict:
    return {"low": low, "mid": mid, "high": high}


def _unavailable_rule(summary: str) -> dict:
    return {
        "status": "unavailable", "label": _RULE_LABEL, "method": "unavailable", "summary": summary,
        "before": _track(), "after": _track(), "assumptions": list(_RULE_LIMITS), "formula": "", "tasks": [],
    }


def _task(task, before: dict, after: dict) -> dict:
    return {
        "id": task.id, "name": task.name, "phase": task.phase, "who": task.who,
        "before": before, "after": after, "coefficient": task.coef, "note": task.note,
    }


def _sum_tasks(tasks: list[dict], track: str) -> dict:
    return {key: _round(sum(Decimal(str(task[track][key])) for task in tasks)) for key in ("low", "mid", "high")}


def _rule_reference(card: Card, text: str, questions: list[Question]) -> dict:
    if not text.strip():
        return _unavailable_rule("没有需求原话，暂不生成规则参考。")
    scope = calculation_change_scope(text)
    assumptions = list(_RULE_LIMITS)
    if scope is not None:
        tasks = [
            _task(work.task, _track(work.trad_low, work.task.trad_days * 8, work.trad_high),
                  _track(work.ai_low, work.task.ai_days * 8, work.ai_high))
            for work in build_change_work(scope)
        ]
        assumptions.append("匹配已有单项指标计算修改模板，假定既有数据、权限和发布链路可复用；新增建设或历史回补不适用。")
        if scope.parent_rollup:
            assumptions.append("母子关系、筛选标记和本母层明细市值按可复用假设计入，尚未核对真实字段。")
        if scope.price_ratio_in_field:
            assumptions.append("原话明确价格比已包含在现有字段，模板不重复计入价格调整；具体映射仍待核对。")
        return {
            "status": "ready", "label": _RULE_LABEL, "method": "incremental",
            "summary": "已有计算口径修改：将匹配的固定工作项逐项相加，作为未校准参考。",
            "before": _sum_tasks(tasks, "before"), "after": _sum_tasks(tasks, "after"),
            "assumptions": assumptions,
            "formula": "以前/现在的下界、参考值、上界分别 = 对应固定任务小时之和；任务系数 = 现在参考小时 ÷ 以前参考小时，仅解释预设比值。",
            "tasks": tasks,
        }

    dims, reasons = score_dims(card, text)
    base_hours = (Decimal("1.5") + sum(Decimal(str(WEIGHTS[key])) * (dims[key] - 1) for key in WEIGHTS)) * 8
    unanswered = sum(question.impact == "高" and not str(question.answer or "").strip() for question in questions)
    high_factor = Decimal("1.4") + Decimal("0.05") * unanswered
    totals = {"low": base_hours * Decimal("0.8"), "mid": base_hours, "high": base_hours * high_factor}
    templates = build_tasks(card, text)
    total_share = sum(Decimal(str(task.share)) for task in templates)
    if not templates or total_share <= 0:
        return _unavailable_rule("没有匹配的固定任务，暂不生成规则参考。")
    allocated = {key: Decimal("0") for key in totals}
    tasks = []
    for index, task in enumerate(templates):
        before = {}
        for key, total in totals.items():
            # 末项收齐舍入余数，展示的任务工时加总等于规则总数。
            amount = (total - allocated[key] if index == len(templates) - 1
                      else (total * Decimal(str(task.share)) / total_share).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            before[key] = float(amount)
            allocated[key] += amount
        after = {key: _round(Decimal(str(value)) * Decimal(str(task.coef))) for key, value in before.items()}
        entry = _task(task, before, after)
        entry["share"] = task.share
        tasks.append(entry)
    assumptions += [
        "匹配一般需求复杂度模板；功能点、数据源与界面等来自文本抽取，不能据此确认真实新增工作量。",
        "接入、权限和页面等为模板预留项，不表示本次必须新建；实际能力可复用时，应人工删减相应工作后重新评估。",
        "历史参考完全独立，未对这些规则数字做加权、平均或校准。",
        "任务占比和 AI 协同系数为旧模板固定配置，尚未用本项目实测数据校准。",
    ]
    return {
        "status": "ready", "label": _RULE_LABEL, "method": "complexity",
        "summary": "一般需求：六维固定加权后按任务占比分配，再用各任务固定系数计算现在的参考工时。",
        "before": _sum_tasks(tasks, "before"), "after": _sum_tasks(tasks, "after"),
        "assumptions": assumptions, "dims": dims, "reasons": reasons, "weights": dict(WEIGHTS),
        "formula": (
            "以前参考小时 = 8 × [1.5 + Σ(维度权重 × (评分 - 1))]；"
            f"下界 = 参考值 × 0.8，上界 = 参考值 × (1.4 + 0.05 × 未答高影响问题数 {unanswered})；"
            "任务以前小时按固定 share / Σshare 分配；现在各区间 = Σ(任务对应以前小时 × 固定系数)。"
            "按小时保留两位小数，末项补齐分配余数；无历史加权。"
        ),
        "tasks": tasks,
    }


def _history_hours(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return _round(number * 8) if math.isfinite(number) and number > 0 else None


def _history_reference(card: Card, text: str) -> dict:
    types = {card.req_type, *card.secondary_types}
    haystack = (text + " " + card.title).casefold()
    matches = []
    for item in knowledge.history():
        keywords = list(dict.fromkeys(keyword.casefold().strip() for keyword in item.keywords if isinstance(keyword, str) and keyword.strip()))
        hits = [keyword for keyword in keywords if keyword in haystack]
        if item.type not in types or not hits:
            continue
        before, actual = _history_hours(item.estimate_days), _history_hours(item.actual_days)
        if before is None and actual is None:
            continue
        matches.append({
            "id": item.id, "title": item.title, "type": item.type,
            "score": round(len(hits) / len(keywords), 3), "matched_keywords": hits,
            "estimated_hours": before, "actual_hours": actual, "ai_assisted": item.ai_assisted,
            "source": getattr(item, "source", "") or "来源未确认",
            "provenance": getattr(item, "provenance", "unknown"),
            "source_ref": getattr(item, "source_ref", ""), "note": item.note,
            "year": item.year, "dept": item.dept,
        })
    matches.sort(key=lambda item: (-item["score"], str(item["id"])))
    items = matches[:3]
    return {
        "status": "ready" if items else "unavailable",
        "summary": (f"找到 {len(items)} 条同类关键词重合的历史原记录；仅并列展示，不据此推算本次工时。"
                    if items else "没有同类且关键词重合的可展示历史记录；不生成历史工时推算。"),
        "limitations": list(_HISTORY_LIMITS), "items": items,
    }


def build_references(card: Card, text: str, questions: list[Question]) -> dict:
    """来源彼此隔离：任一参考不可用都不应阻止另一参考或模型估时。"""
    questions = questions or []
    scope_text = (text or "") + "\n" + "\n".join(str(question.answer) for question in questions if question.answer and str(question.answer).strip())
    try:
        rule = _rule_reference(card, scope_text, questions)
    except Exception:
        rule = _unavailable_rule("规则参考生成失败；不影响独立模型分析，请检查需求结构与规则配置。")
    try:
        history = _history_reference(card, scope_text)
    except Exception:
        history = {"status": "unavailable", "summary": "历史参考读取失败；不影响规则参考或独立模型分析。",
                   "limitations": list(_HISTORY_LIMITS), "items": []}
    return {"rule": rule, "history": history}
