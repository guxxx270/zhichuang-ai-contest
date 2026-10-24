"""模型拆分的工作项。工时来自逐项分析，程序仅作单位换算和汇总。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .intake import Card

AI_DO, AI_REVIEW, HUMAN = "AI 能做", "AI 做人审", "人必须做"
PHASES = ("需求与设计", "开发", "测试与比对", "上线与培训")


@dataclass
class Task:
    id: str
    name: str
    phase: str
    who: str
    share: float
    coef: float  # 两轨结果的比值，仅为兼容旧接口；不用于生成工时
    note: str
    trad_days: float = 0.0
    ai_days: float = 0.0
    trad_low_hours: float = 0.0
    trad_high_hours: float = 0.0
    ai_low_hours: float = 0.0
    ai_high_hours: float = 0.0
    before_reason: str = ""
    after_reason: str = ""
    evidence: list[dict[str, str]] = field(default_factory=list)


def who_split(tasks: list[Task]) -> dict[str, float]:
    total = sum(t.trad_days for t in tasks)
    if not total:
        return {}
    return {who: round(sum(t.trad_days for t in tasks if t.who == who) / total, 3)
            for who in (AI_DO, AI_REVIEW, HUMAN)}


def delivery_mode(card: Card, split: dict[str, float], text: str) -> str:
    """按分工占比（以前工时加权）与原话风险给交付建议；没有工作项（split 为空）时按技术部主做。"""
    risky = "客户" in card.scope_objects or bool(re.search(r"下单|指令|限仓|公告", text))
    ai_do, human = split.get(AI_DO, 0), split.get(HUMAN, 0)
    if ai_do >= 0.35 and human <= 0.35 and card.req_type in ("报表", "页面") and not risky:
        return "业务可在公司 AI 平台自建（Vibe Coding），技术部只审口径、权限与上线——适合「人人都有智能体」的自助路径"
    if human >= 0.45:
        return "技术部主做；AI 协同只省样板代码，重点工时在对接、口径与验证"
    return "技术部主做，AI 协同：AI 出初稿、人审关键环节（资金数字、对客内容、交易链路）"
