"""流水线：隐盾 → 问清 → 写单 → 估量 → 定架 → 出样。一次 analyze() 跑完整条链，业务答复后可 rerun。"""
from __future__ import annotations

import time
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Callable

from ..drafts import Draft, RepoBundle
from ..llm import LLM
from ..privacy import Redactor
from .architect import Architecture, build_architecture
from .estimate import Estimate, estimate
from .intake import Card, Question, build_questions, confirm_message, extract_card, refine_with_llm
from .prototype import apply_prototype_view, layers_html, render_source
from .spec import Spec, build_spec


def _draft_table_headers(drafts: list[Draft] | None) -> list[str]:
    headers: list[str] = []
    for d in drafts or []:
        for h in getattr(d, "table_headers", None) or []:
            if h and h not in headers:
                headers.append(h)
    return headers[:16]


@dataclass
class Analysis:
    raw_text: str
    redacted_text: str
    redacted: int
    mapping: dict[str, str]
    card: Card
    questions: list[Question]
    message: str
    spec: Spec
    estimate: Estimate
    architecture: Architecture
    prototype_html: str
    layers_html: str
    seconds: float = 0.0
    engine: str = "规则"
    notes: list[str] = field(default_factory=list)
    drafts: list[Draft] = field(default_factory=list)
    repos: list[RepoBundle] = field(default_factory=list)
    prototype_source_html: str = ""
    recalled: int = 0          # 追问记忆沿用了几条（问过的不再问）
    mobile: bool = False
    client_view: bool = False


class _Mock:
    mode = "mock"
    note = ""


def analyze(
    text: str,
    source_hint: str = "",
    llm: LLM | None = None,
    answers: dict[str, str] | None = None,
    mobile: bool = False,
    client_view: bool = False,
    llm_polish: bool = True,
    drafts: list[Draft] | None = None,
    repos: list[RepoBundle] | None = None,
    memory=None,
) -> Analysis:
    """drafts：HTML 底稿；repos：git/前后端代码包。有 API 时模型会读代码与 HTML。
    memory：xuzhi.memory.Memory；给了就"问过的不再问"——同一提出方上次答过的题直接沿用并标 recalled。
    业务这次的答复（answers）优先于记忆。"""
    t0 = time.time()
    llm = (llm or LLM()) if llm_polish else _Mock()
    drafts = list(drafts or [])
    repos = list(repos or [])
    red = Redactor().redact(text)
    card = extract_card(red.text, source_hint)
    questions = build_questions(
        red.text,
        card,
        has_materials=bool(drafts or repos),
        table_headers=_draft_table_headers(drafts),
    )
    card, questions = refine_with_llm(red.text, card, questions, llm, drafts=drafts, repos=repos)
    for q in questions:
        if answers and q.id in answers:
            q.answer = answers[q.id]
    recalled = 0
    if memory is not None:
        try:
            recalled = int(memory.recall(card, questions) or 0)
        except Exception:   # 记忆只是加分项，坏了不影响出稿
            recalled = 0
    arch = build_architecture(card, red.text, drafts=drafts, repos=repos)
    spec = build_spec(card, questions, arch.coverage_line, llm)
    est = estimate(card, red.text, questions, llm=llm, drafts=drafts, repos=repos)
    msg = confirm_message(card, questions, red.text)
    source = render_source(card, drafts=drafts, repos=repos, llm=llm, demand_text=red.text)
    proto = apply_prototype_view(source, mobile=mobile, client_view=client_view)
    a = Analysis(
        text, red.text, red.total, red.mapping, card, questions, msg, spec, est, arch, proto,
        layers_html(arch.layers), drafts=drafts, repos=repos, prototype_source_html=source,
        mobile=mobile, client_view=client_view,
    )
    a.seconds = round(time.time() - t0, 2)
    a.engine = "规则 + 模型" if (card.engine != "规则" or spec.engine != "规则" or est.status == "ready") else "规则"
    a.recalled = recalled
    if recalled:
        a.notes.append(f"追问记忆：沿用上次口径 {recalled} 条，未再问")
    if drafts or repos:
        bits = []
        if drafts:
            bits.append(f"HTML {len(drafts)} 份")
        if repos:
            nfiles = sum(len(r.files) for r in repos)
            bits.append(f"仓库 {len(repos)} 个 / 源文件 {nfiles}")
        a.notes.append("已加载 " + "，".join(bits))
        if llm.mode == "api" and a.engine != "规则":
            a.notes.append("模型已读底稿 HTML 与仓库代码摘录")
    return a


def enhance_analysis(
    seed: Analysis,
    llm: LLM,
    *,
    onstage: Callable[[str], None] | None = None,
    mobile: bool | None = None,
    client_view: bool | None = None,
) -> Analysis:
    """在已完成的快速结果上做模型增强，不重跑记忆、不记业务台账。

    各模型 helper 自带的失败回退仍保留；未捕获的异常交给任务层处理。
    onstage 也用于阶段之间检查任务是否已被丢弃，因此不能吞掉其异常。
    """
    started = time.monotonic()
    result = deepcopy(seed)
    result.mobile = getattr(seed, "mobile", False) if mobile is None else mobile
    result.client_view = getattr(seed, "client_view", False) if client_view is None else client_view

    def stage(name: str) -> None:
        if onstage is not None:
            onstage(name)

    protected = {question.id: deepcopy(question) for question in result.questions if question.answer.strip() or question.recalled}
    stage("模型问清")
    result.card, refined = refine_with_llm(
        result.redacted_text, result.card, result.questions, llm, drafts=result.drafts, repos=result.repos,
    )
    # 有材料时模型可能移除某些规则题；已答或沿用的口径仍需保留，不能随润色丢失。
    result.questions = []
    seen = set()
    for question in refined:
        if question.id in seen:
            continue
        result.questions.append(protected.get(question.id, question))
        seen.add(question.id)
    result.questions += [question for key, question in protected.items() if key not in seen]
    result.recalled = sum(bool(question.recalled) for question in result.questions)

    stage("模型写单")
    result.architecture = build_architecture(result.card, result.redacted_text, drafts=result.drafts, repos=result.repos)
    result.spec = build_spec(result.card, result.questions, result.architecture.coverage_line, llm)
    result.message = confirm_message(result.card, result.questions, result.redacted_text)

    stage("模型估时")
    result.estimate = estimate(result.card, result.redacted_text, result.questions, llm=llm,
                               drafts=result.drafts, repos=result.repos)

    stage("模型出样")
    source = render_source(result.card, drafts=result.drafts, repos=result.repos, llm=llm, demand_text=result.redacted_text)
    result.prototype_source_html = source
    result.prototype_html = apply_prototype_view(source, mobile=result.mobile, client_view=result.client_view)
    result.layers_html = layers_html(result.architecture.layers)
    result.seconds = round(float(seed.seconds or 0) + time.monotonic() - started, 2)
    result.engine = ("规则 + 模型" if result.card.engine != "规则" or result.spec.engine != "规则"
                     or result.estimate.status == "ready" or source != seed.prototype_source_html else "规则")
    if result.estimate.status == "failed":
        result.notes.append("模型估时未完成，其余结果与规则、历史参考仍可查看。")
    elif result.estimate.status == "unavailable":
        result.notes.append("当前没有可用模型估时；规则与历史参考保留。")
    stage("模型增强完成")
    return result
