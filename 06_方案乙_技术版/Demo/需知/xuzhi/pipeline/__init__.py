"""流水线：隐盾 → 问清 → 写单 → 估量 → 定架 → 出样。一次 analyze() 跑完整条链，业务答复后可 rerun。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

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
) -> Analysis:
    """drafts：HTML 底稿；repos：git/前后端代码包。有 API 时模型会读代码与 HTML。"""
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
    arch = build_architecture(card, red.text, drafts=drafts, repos=repos)
    spec = build_spec(card, questions, arch.coverage_line, llm)
    est = estimate(card, red.text, questions)
    msg = confirm_message(card, questions, red.text)
    source = render_source(card, drafts=drafts, repos=repos, llm=llm, demand_text=red.text)
    proto = apply_prototype_view(source, mobile=mobile, client_view=client_view)
    a = Analysis(
        text, red.text, red.total, red.mapping, card, questions, msg, spec, est, arch, proto,
        layers_html(arch.layers), drafts=drafts, repos=repos, prototype_source_html=source,
    )
    a.seconds = round(time.time() - t0, 2)
    a.engine = "规则 + 模型" if (card.engine != "规则" or spec.engine != "规则") else "规则"
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
