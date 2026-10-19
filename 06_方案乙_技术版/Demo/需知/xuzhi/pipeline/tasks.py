"""模型拆分的工作项。工时来自逐项分析，程序仅作单位换算和汇总。"""
from __future__ import annotations

from dataclasses import dataclass, field

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
