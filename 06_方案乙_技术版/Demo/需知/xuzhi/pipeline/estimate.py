"""大模型逐项分析以前/现在的工时；程序校验证据和数字，只做汇总。"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from decimal import Decimal

from ..drafts import Draft, RepoBundle, draft_context_for_llm, redact_material
from ..llm import load_prompt
from .intake import Card, Question
from .estimate_references import build_references
from .tasks import AI_DO, AI_REVIEW, HUMAN, PHASES, Task, who_split


@dataclass
class Estimate:
    # 沿用人天字段兼容台账/API。None 表示未估时，不能按零工时处理。
    dims: dict[str, int] = field(default_factory=dict)
    reasons: dict[str, str] = field(default_factory=dict)
    base_days: float | None = None
    history_days: float | None = None
    mid: float | None = None
    low: float | None = None
    high: float | None = None
    confidence: str = "未估时"
    confidence_reason: str = ""
    similar: list = field(default_factory=list)
    phases: dict[str, float] = field(default_factory=dict)
    unanswered_high: int = 0
    tasks: list[Task] = field(default_factory=list)
    ai_mid: float | None = None
    ai_low: float | None = None
    ai_high: float | None = None
    saving_pct: float | None = None
    who: dict[str, float] = field(default_factory=dict)
    ai_phases: dict[str, float] = field(default_factory=dict)
    delivery: str = ""
    method: str = "unavailable"
    assumptions: list[str] = field(default_factory=list)
    status: str = "unavailable"
    basis: str = "requirements_only"
    model: str = ""
    code_files: list[str] = field(default_factory=list)
    summary: str = ""
    error: str = ""
    references: dict = field(default_factory=dict)

    @property
    def total_points(self) -> int:
        return sum(self.dims.values())


def _text(value, label: str, limit: int = 3000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{label}缺失或过长")
    return value.strip()


def _hours(track, label: str) -> tuple[Decimal, Decimal, Decimal, str]:
    if not isinstance(track, dict):
        raise ValueError(f"{label}缺少工时区间")
    values = []
    for key in ("low", "mid", "high"):
        value = track.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100000 or not math.isfinite(value):
            raise ValueError(f"{label}须为有限的非负小时数")
        values.append(Decimal(str(value)))
    low, mid, high = values
    if not low <= mid <= high or mid <= 0:
        raise ValueError(f"{label}区间应满足下界 ≤ 参考值 ≤ 上界，参考值须大于零")
    return low, mid, high, _text(track.get("reason"), f"{label}依据")


def _finite_number(value: Decimal) -> float:
    number = float(value)
    if not math.isfinite(number) or (value != 0 and number == 0):
        raise ValueError("工时换算或比较结果超出有效数值范围")
    return number


def _payload(text: str, questions: list[Question], card: Card, repos: list[RepoBundle], drafts: list[Draft]) -> dict:
    # 仅使用调用方提供的材料；不扫描磁盘，不连接业务数据库。
    context = draft_context_for_llm([], repos, code_limit=28000,
                                    keywords=card.keywords() | set(card.indicators))
    files = []
    for index, repo in enumerate(context["repos"], 1):
        for file_index, item in enumerate(repo["files"], 1):
            if item["excerpt"].strip():
                path = redact_material(item["path"])
                # 脱敏可能把不同原路径变成同一标签；证据 ID 另带序号，避免字典覆盖。
                files.append({"id": f"R{index}/F{file_index}/{path}", "path": path, "excerpt": item["excerpt"]})
    # HTML 底稿可辅助理解页面，但不能据此声称已核对业务后端代码。
    reference = draft_context_for_llm(drafts, [], html_limit=6000)["html_drafts"] if drafts else []
    return {
        "requirement": redact_material(text),
        "answers": [{"id": q.id, "question": redact_material(q.question), "answer": redact_material(q.answer)}
                    for q in questions if q.answer.strip()],
        "code_files": files,
        "reference_pages": [{"name": redact_material(d["name"]), "html": d["html"]} for d in reference],
        "code_scope": "仅提供的源码摘录，未运行或验证完整系统" if files else "未提供有效源码，仅依据需求粗估",
        "comparison": {"before": "以前：同一范围、同一现有系统，人工开发，不使用生成式AI",
                       "after": "现在：同一范围，使用AI辅助分析、编码与测试，保留必要人工核对"},
    }


def _parse(data, payload: dict, result: Estimate) -> Estimate:
    if not isinstance(data, dict):
        raise ValueError("模型未返回估时对象")
    summary = _text(data.get("summary"), "分析摘要")
    why = _text(data.get("confidence_reason"), "置信度说明")
    confidence = data.get("confidence")
    if confidence not in ("低", "中", "高"):
        raise ValueError("置信度格式错误")
    assumptions = data.get("assumptions")
    if not isinstance(assumptions, list) or not 1 <= len(assumptions) <= 30:
        raise ValueError("需要明确估算前提与待核对项")
    assumptions = [_text(a, "估算前提") for a in assumptions]
    raw_tasks = data.get("tasks")
    if not isinstance(raw_tasks, list) or not 1 <= len(raw_tasks) <= 40:
        raise ValueError("需要1至40项具体工作")
    code = {f["id"]: f["excerpt"] for f in payload["code_files"]}
    answers = [a["answer"] for a in payload["answers"]]
    tasks, totals, phases, ai_phases = [], [Decimal(0) for _ in range(6)], {}, {}
    has_code_evidence = False
    names = set()
    for index, raw in enumerate(raw_tasks, 1):
        if not isinstance(raw, dict):
            raise ValueError("工作项格式错误")
        name = _text(raw.get("name"), "工作项名称", 160)
        if name in names:
            raise ValueError("工作项名称重复，请合并后重估")
        names.add(name)
        phase, who = raw.get("phase"), raw.get("who")
        if phase not in PHASES or who not in (AI_DO, AI_REVIEW, HUMAN):
            raise ValueError("工作阶段或分工格式错误")
        change = _text(raw.get("change"), "具体改动")
        b_low, b_mid, b_high, b_reason = _hours(raw.get("before"), "以前")
        a_low, a_mid, a_high, a_reason = _hours(raw.get("after"), "现在")
        evidence = raw.get("evidence")
        if not isinstance(evidence, list) or not 1 <= len(evidence) <= 12:
            raise ValueError("每项工作都需要需求或代码依据")
        checked = []
        for ref in evidence:
            if not isinstance(ref, dict):
                raise ValueError("依据格式错误")
            source = ref.get("source")
            quote = _text(ref.get("quote"), "依据摘录", 1500)
            file = ref.get("file", "")
            if source == "code":
                if not isinstance(file, str) or file not in code or quote not in code[file]:
                    raise ValueError("代码依据未出现在本次提供的源码摘录中")
                has_code_evidence = True
            elif source == "requirement":
                if quote not in payload["requirement"]:
                    raise ValueError("需求依据未出现在原话中")
                file = ""
            elif source == "answer":
                if not any(quote in a for a in answers):
                    raise ValueError("答复依据未出现在业务答复中")
                file = ""
            else:
                raise ValueError("依据来源类型错误")
            checked.append({"source": source, "file": file, "quote": quote})
        tasks.append(Task(f"E{index}", name, phase, who, _finite_number(b_mid), _finite_number(a_mid / b_mid), change,
                          trad_days=_finite_number(b_mid / 8), ai_days=_finite_number(a_mid / 8),
                          trad_low_hours=float(b_low), trad_high_hours=float(b_high),
                          ai_low_hours=float(a_low), ai_high_hours=float(a_high),
                          before_reason=b_reason, after_reason=a_reason, evidence=checked))
        totals = [x + y for x, y in zip(totals, (b_low, b_mid, b_high, a_low, a_mid, a_high))]
        phases[phase] = phases.get(phase, Decimal(0)) + b_mid
        ai_phases[phase] = ai_phases.get(phase, Decimal(0)) + a_mid
    # 所有检查通过后才公开整份结果；模型给的总计、系数、节省比例一律不采用。
    converted_totals = [_finite_number(n / 8) for n in totals]
    converted_phases = {k: _finite_number(v / 8) for k, v in phases.items()}
    converted_ai_phases = {k: _finite_number(v / 8) for k, v in ai_phases.items()}
    saving_pct = round(_finite_number((1 - totals[4] / totals[1]) * 100), 1)
    result.low, result.mid, result.high, result.ai_low, result.ai_mid, result.ai_high = converted_totals
    result.tasks = tasks
    result.phases = converted_phases
    result.ai_phases = converted_ai_phases
    result.saving_pct = saving_pct
    result.who = who_split(tasks)
    result.basis = "code_excerpt" if has_code_evidence else "requirements_only"
    result.confidence = ("中" if confidence == "高" else confidence) if has_code_evidence else "低"
    result.confidence_reason = why
    result.assumptions = assumptions
    result.assumptions.insert(0, "仅依据本次提供的源码摘录，未运行验证，未覆盖代码不作已确认处理。" if has_code_evidence
                              else "粗估：缺少可核对的相关代码依据，只根据需求与答复推断，实际改动范围待核实。")
    result.summary = summary
    result.delivery = "以前与现在比较相同交付范围；两边均为预计投入工时，不含排期和等待时间。"
    result.method, result.status = "llm", "ready"
    return result


def estimate(card: Card, text: str, questions: list[Question], *, llm=None,
             repos: list[RepoBundle] | None = None, drafts: list[Draft] | None = None) -> Estimate:
    result = Estimate(unanswered_high=sum(q.impact == "高" and not q.answer.strip() for q in questions))
    # 独立保留规则与历史参考，不参与模型提示、模型结果或模型汇总。
    result.references = build_references(card, text, questions)
    if llm is None or getattr(llm, "mode", "mock") != "api":
        result.error = "未接入大模型，尚无模型估时。规则模板与历史记录可独立参考；配置模型后可补充分析。"
        return result
    result.model = getattr(llm, "model", "")
    try:
        payload = _payload(text, questions, card, list(repos or []), list(drafts or []))
        result.code_files = [f["id"] for f in payload["code_files"]]
        llm.current_purpose = "估时分析"
        data = llm.chat_json(load_prompt("estimate_analysis"), json.dumps(payload, ensure_ascii=False))
        result.model = getattr(llm, "last_model", "") or result.model
    except Exception:
        result.status = "failed"
        result.error = "模型估时调用失败，尚未生成工时。请检查模型连接后重新分析。"
        return result
    try:
        return _parse(data, payload, result)
    except ValueError as exc:
        result.status = "failed"
        result.error = "模型估时结果未通过校验：" + str(exc) + "。请重新分析。"
        return result
