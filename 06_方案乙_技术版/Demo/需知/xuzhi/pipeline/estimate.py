"""大模型逐项分析以前/现在的工时；程序校验证据和数字，只做汇总。"""
from __future__ import annotations

import itertools
import json
import math
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal

from ..drafts import Draft, RepoBundle, draft_context_for_llm, redact_material
from ..llm import OutputTruncated, gateway_unreachable, load_prompt
from .intake import Card, Question
from .estimate_references import build_references
from .tasks import AI_DO, AI_REVIEW, HUMAN, PHASES, Task, delivery_mode, who_split


ESTIMATION_COMPARISON = {
    "version": "agent-workflow-v2",
    "before": "以前：熟悉技术栈的开发人员使用 IDE、搜索、调试器、SQL 和自动化测试完成同一变更，不使用生成式 AI。",
    "after": "现在：同等人员使用可检索仓库、修改多文件、运行测试、读取报错并迭代修复的编程代理；人负责目标、业务口径、定向审查和最终验收。",
    "unit": "有效人时：参与人员的实际投入之和；无人值守的模型生成、构建、测试运行及排期等待不计入人时，同一人的重叠时间不重复计算。",
    "environment": "待验证的实施前提：开发时双方均可访问项目代码、已有开发环境和必要测试样本，代理获得相应工具权限；材料明确限制能力时，以限制为准。估算时未提供源码不等于开发时无法取得源码。",
}

_TASK_HOURS_MAX = 400     # 单项上界：再大多半是单位写错（分钟当小时）或该拆成几项
_TOTAL_HOURS_MAX = 4000   # 以前的上界合计：超过就不是一条需求的量级
_QUOTE_MARKS = "\"'“”„‟‘’‚‛「」『』"
_TO_STRAIGHT = str.maketrans({mark: '"' for mark in _QUOTE_MARKS})
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


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
    comparison: dict = field(default_factory=dict)

    @property
    def total_points(self) -> int:
        return sum(self.dims.values())


def _text(value, label: str, limit: int = 3000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{label}缺失或过长")
    return value.strip()


def _label(value) -> str:
    """阶段 / 分工 / 置信度 / 来源：NFKC、去掉全部空白和首尾引号再比，「AI做人审」「"开发"」照样认。"""
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).strip(_QUOTE_MARKS)


def _choice(value, options) -> str | None:
    """宽松匹配到标准写法；对不上返回 None。"""
    key = _label(value)
    return next((option for option in options if _label(option) == key), None) if key else None


def _loose(text: str) -> str:
    """依据比对用：NFKC、各种引号统一成直引号、去掉空白（标点保留）——少个空格、弯引号不等于伪造。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text).translate(_TO_STRAIGHT))


def _solid_code_quote(quote: str) -> bool:
    """够格把结果升级为「已参考代码」的摘录：至少 12 个非空白字符且含标识符；一个字母也「出现在源码里」。"""
    return len(re.sub(r"\s+", "", quote)) >= 12 and bool(_IDENTIFIER.search(quote))


def _hours(track, label: str) -> tuple[Decimal, Decimal, Decimal, str]:
    if not isinstance(track, dict):
        raise ValueError(f"{label}缺少工时区间")
    values = []
    for key in ("low", "mid", "high"):
        value = track.get(key)
        # 先比大小再判有限：超大整数直接 isfinite 会溢出
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100000 or not math.isfinite(value):
            raise ValueError(f"{label}须为有限的非负小时数")
        values.append(Decimal(str(value)))
    low, mid, high = values
    if not low <= mid <= high or mid <= 0:
        raise ValueError(f"{label}区间应满足下界 ≤ 参考值 ≤ 上界，参考值须大于零")
    if high > _TASK_HOURS_MAX:
        raise ValueError(f"{label}单项上界超过 {_TASK_HOURS_MAX} 小时，请拆小工作项或核对单位")
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
        "comparison": dict(ESTIMATION_COMPARISON),
    }


def _parse(data, payload: dict, result: Estimate) -> Estimate:
    if not isinstance(data, dict):
        raise ValueError("模型未返回估时对象")
    summary = _text(data.get("summary"), "分析摘要")
    why = _text(data.get("confidence_reason"), "置信度说明")
    confidence = _label(data.get("confidence"))[:1]   # 「低（粗估）」「中等」只看首字，回填标准写法
    if confidence not in ("低", "中", "高"):
        raise ValueError("置信度格式错误")
    assumptions = data.get("assumptions")
    if not isinstance(assumptions, list) or not 1 <= len(assumptions) <= 30:
        raise ValueError("需要明确估算前提与待核对项")
    assumptions = [_text(a, "估算前提") for a in assumptions]
    raw_tasks = data.get("tasks")
    if not isinstance(raw_tasks, list) or not 1 <= len(raw_tasks) <= 40:
        raise ValueError("需要1至40项具体工作")
    code = {f["id"]: _loose(f["excerpt"]) for f in payload["code_files"]}
    requirement = _loose(payload["requirement"])
    answers = [_loose(a["answer"]) for a in payload["answers"]]
    tasks, totals, phases, ai_phases = [], [Decimal(0) for _ in range(6)], {}, {}
    has_code_evidence = False
    names = set()
    for index, raw in enumerate(raw_tasks, 1):
        if not isinstance(raw, dict):
            raise ValueError("工作项格式错误")
        name = _text(raw.get("name"), "工作项名称", 160)
        if name in names:   # 重名不作废整份：加序号区分，内容照常逐项校验
            name = next(f"{name}（{n}）" for n in itertools.count(2) if f"{name}（{n}）" not in names)
        names.add(name)
        phase, who = _choice(raw.get("phase"), PHASES), _choice(raw.get("who"), (AI_DO, AI_REVIEW, HUMAN))
        if phase is None or who is None:
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
            source = _label(ref.get("source")).lower()
            quote = _text(ref.get("quote"), "依据摘录", 1500)
            key = _loose(quote)
            file = ref.get("file", "")
            if source == "code":
                file = file.strip() if isinstance(file, str) else file
                if not isinstance(file, str) or file not in code or key not in code[file]:
                    raise ValueError("代码依据未出现在本次提供的源码摘录中")
                # 只有开发项里像样的代码摘录才把结果升级为「已参考代码」，否则粗估警告会被一个字母抹掉
                has_code_evidence = has_code_evidence or (phase == "开发" and _solid_code_quote(quote))
            elif source == "requirement":
                if key not in requirement:
                    raise ValueError("需求依据未出现在原话中")
                file = ""
            elif source == "answer":
                if not any(key in a for a in answers):
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
    if totals[2] > _TOTAL_HOURS_MAX:
        raise ValueError(f"以前的上界合计超过 {_TOTAL_HOURS_MAX} 小时，不像一条需求的量级，请拆分需求或核对单位")
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
    # 保存本次采用的比较口径；旧结果不随新提示词被重新解释。
    result.comparison = dict(payload["comparison"])
    result.method, result.status = "llm", "ready"
    return result


def _ask(llm, system: str, payload: dict):
    llm.current_purpose = "估时分析"
    return llm.chat_json(system, json.dumps(payload, ensure_ascii=False))


def _call_failed(result: Estimate, exc: Exception) -> Estimate:
    """调用失败按原因给能照着做的提示；不带网关返回的原文（可能含账号或密钥信息）。"""
    result.status = "failed"
    body = getattr(exc, "body", None)
    detail = body.get("error", body) if isinstance(body, dict) else {}
    error_code = getattr(exc, "code", None) or (detail.get("code") if isinstance(detail, dict) else None)
    message = str(exc)
    if isinstance(exc, OutputTruncated):
        result.error = "模型输出被截断（max_tokens 不足或材料过多），尚未生成工时。请减少材料或换支持长输出的模型后重试。"
    elif error_code == "model_not_found":
        result.error = "当前网关没有为所选模型提供可用渠道，尚未生成工时。请读取可用模型目录，并核对账号分组支持的模型后重试。"
    elif "沙箱策略拒发" in message:
        result.error = "出站内容含明文敏感项，已按沙箱策略拒发，尚未生成工时；请检查材料与答复的脱敏。"
    elif "网关超时或连不上" in message or gateway_unreachable(exc):
        result.error = "模型网关超时或连不上，尚未生成工时；稍后再试。"
    else:
        result.error = "模型估时调用失败，尚未生成工时。请检查模型连接后重新分析。"
    return result


def _ready(result: Estimate, card: Card, text: str) -> Estimate:
    # 交付建议按模型给的分工占比与原话风险算：AI 能做的多、人工少、无对客 / 交易风险的报表与页面可业务自建
    result.delivery = delivery_mode(card, result.who, text)
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
        system = load_prompt("estimate_analysis")
        payload = _payload(text, questions, card, list(repos or []), list(drafts or []))
        result.code_files = [f["id"] for f in payload["code_files"]]
        data = _ask(llm, system, payload)
        result.model = getattr(llm, "last_model", "") or result.model
    except Exception as exc:
        return _call_failed(result, exc)
    try:
        return _ready(_parse(data, payload, result), card, text)
    except ValueError as exc:
        problem = str(exc)
    # 格式不合格只重问一次，带上校验错误让模型照着改；连接、截断等调用异常不重问。
    try:
        data = _ask(llm, system, {**payload, "上次校验错误": problem})
        result.model = getattr(llm, "last_model", "") or result.model
    except Exception as exc:
        return _call_failed(result, exc)
    try:
        return _ready(_parse(data, payload, result), card, text)
    except ValueError as exc:
        result.status = "failed"
        result.error = f"模型输出格式不合格：{exc}；可直接重试。"
        return result
