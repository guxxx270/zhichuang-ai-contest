"""流水线：隐盾 → 问清 → 写单 → 估量 → 定架 → 出样。一次 analyze() 跑完整条链，业务答复后可 rerun。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..llm import LLM
from ..privacy import Redactor
from .architect import Architecture, build_architecture
from .estimate import Estimate, estimate
from .intake import Card, Question, build_questions, confirm_message, extract_card, refine_with_llm
from .prototype import layers_html, render
from .spec import Spec, build_spec


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


class _Mock:
    mode = "mock"
    note = ""


def analyze(text: str, source_hint: str = "", llm: LLM | None = None, answers: dict[str, str] | None = None,
            mobile: bool = False, client_view: bool = False, llm_polish: bool = True) -> Analysis:
    """llm_polish=False：只跑规则引擎（毫秒级），模型润色留到用户点按钮时再做。"""
    t0 = time.time()
    llm = (llm or LLM()) if llm_polish else _Mock()
    red = Redactor().redact(text)
    card = extract_card(red.text, source_hint)
    questions = build_questions(red.text, card)
    card, questions = refine_with_llm(red.text, card, questions, llm)
    for q in questions:
        if answers and q.id in answers:
            q.answer = answers[q.id]
    arch = build_architecture(card, red.text)
    spec = build_spec(card, questions, arch.coverage_line, llm)
    est = estimate(card, red.text, questions)
    msg = confirm_message(card, questions)
    proto = render(card, mobile=mobile, client_view=client_view)
    a = Analysis(text, red.text, red.total, red.mapping, card, questions, msg, spec, est, arch, proto, layers_html(arch.layers))
    a.seconds = round(time.time() - t0, 2)
    a.engine = "规则 + 模型" if (card.engine != "规则" or spec.engine != "规则") else "规则"
    return a
