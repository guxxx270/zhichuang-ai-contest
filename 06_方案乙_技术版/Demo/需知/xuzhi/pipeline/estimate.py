"""估量：六维复杂度 → 基础人天；类比估算（历史需求库实际人天校准）→ 区间 + 置信度 + 阶段拆分。数字全由程序算。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .. import config, knowledge
from .intake import Card, Question
from .tasks import Task, allocate, build_tasks, delivery_mode, who_split

DIMS = ["功能点", "数据接入", "界面", "权限", "性能", "合规"]
WEIGHTS = {"功能点": 1.2, "数据接入": 1.5, "界面": 1.2, "权限": 0.8, "性能": 1.5, "合规": 0.8}   # 每提高 1 分增加的人天


@dataclass
class Similar:
    id: str
    title: str
    type: str
    dept: str
    year: int
    score: float
    estimate_days: float
    actual_days: float
    note: str


@dataclass
class Estimate:
    dims: dict[str, int]
    reasons: dict[str, str]
    base_days: float
    history_days: float | None
    mid: float
    low: float
    high: float
    confidence: str
    confidence_reason: str
    similar: list[Similar]
    phases: dict[str, float] = field(default_factory=dict)
    unanswered_high: int = 0
    # ---- AI 协同轨 ----
    tasks: list[Task] = field(default_factory=list)
    ai_mid: float = 0.0
    ai_low: float = 0.0
    ai_high: float = 0.0
    saving_pct: float = 0.0
    who: dict[str, float] = field(default_factory=dict)
    ai_phases: dict[str, float] = field(default_factory=dict)
    delivery: str = ""

    @property
    def total_points(self) -> int:
        return sum(self.dims.values())


def _clip(v: int) -> int:
    return max(1, min(5, v))


def score_dims(card: Card, text: str) -> tuple[dict[str, int], dict[str, str]]:
    n = len(card.features)
    fp = 1 if n <= 2 else 2 if n <= 4 else 3 if n <= 6 else 4 if n <= 9 else 5
    ds = len(card.data_sources)
    external = bool(re.search(r"现货|第三方|公告|交易所日报|新闻", text))
    realtime = card.frequency in ("实时", "分钟级") or bool(re.search(r"实时|盘中|下单前", text))
    di = _clip(ds + (1 if external else 0) + (1 if realtime else 0))
    ui = {"页面": 3, "报表": 2, "提醒": 1, "接口": 1, "数据": 1, "流程": 2}.get(card.req_type, 2) + (1 if "手机端可看" in card.nonfunctional else 0)
    pm = 1 + (2 if "客户" in card.scope_objects else 0) + (1 if re.search(r"权限|只能看|按人|投资经理", text) else 0) + (1 if "对外" in text else 0)
    perf = 4 if realtime else 3 if card.frequency == "分钟级" else 2 if re.search(r"\d+\s*分钟内", text) else 1
    perf += 1 if re.search(r"全品种|所有品种|全部客户|逐客户|五家交易所", text) else 0
    comp = 1 + (2 if "客户" in card.scope_objects else 0) + (1 if re.search(r"合规|适当性|监管|报送", text) else 0) + (1 if re.search(r"留痕|审计", text) else 0) + (1 if re.search(r"下单|指令|阻断", text) else 0)
    dims = {"功能点": _clip(fp), "数据接入": di, "界面": _clip(ui), "权限": _clip(pm), "性能": _clip(perf), "合规": _clip(comp)}
    reasons = {
        "功能点": f"{n} 个功能点",
        "数据接入": f"{ds} 类数据源" + ("，含外部 / 非结构化来源" if external else "") + ("，需实时接入" if realtime else ""),
        "界面": f"{card.req_type}类" + ("，含手机端" if "手机端可看" in card.nonfunctional else ""),
        "权限": ("涉及客户数据，" if "客户" in card.scope_objects else "") + ("需按人 / 产品授权" if pm >= 2 else "沿用现有角色权限"),
        "性能": "实时 / 盘中" if realtime else ("有限时要求" if perf >= 2 else "日终批处理"),
        "合规": ("客户信息脱敏与审阅；" if "客户" in card.scope_objects else "") + ("触及交易链路；" if re.search(r"下单|指令|阻断", text) else "") + ("留痕 / 审计" if re.search(r"留痕|审计", text) else "常规"),
    }
    return dims, reasons


def find_similar(card: Card, text: str, k: int = 3) -> list[Similar]:
    out = []
    for h in knowledge.history():
        hit = sum(1 for kw in h.keywords if kw in text or kw in card.title)
        score = hit / max(len(h.keywords), 1) + (0.2 if h.type == card.req_type else 0.0)
        if score >= 0.3:
            out.append(Similar(h.id, h.title, h.type, h.dept, h.year, round(min(score, 1.0), 2), h.estimate_days, h.actual_days, h.note))
    out.sort(key=lambda s: -s.score)
    return out[:k]


def estimate(card: Card, text: str, questions: list[Question]) -> Estimate:
    dims, reasons = score_dims(card, text)
    base = 1.5 + sum(WEIGHTS[d] * (dims[d] - 1) for d in DIMS)
    sims = find_similar(card, text)
    history_days = None
    if sims:
        wsum = sum(s.score for s in sims)
        history_days = sum(s.actual_days * s.score for s in sims) / wsum
        # 大需求（功能点多）按功能点比例放大历史值
        scale = max(1.0, len(card.features) / 4)
        history_days *= min(scale, 2.0)
        mid = config.ESTIMATE_BLEND_HISTORY * history_days + (1 - config.ESTIMATE_BLEND_HISTORY) * base
    else:
        mid = base
    unanswered_high = sum(1 for q in questions if q.impact == "高" and not q.answer.strip())
    high_factor = 1.4 + 0.05 * unanswered_high
    low, high = mid * 0.8, mid * high_factor
    top = sims[0].score if sims else 0.0
    if top >= 0.6 and unanswered_high <= 2:
        conf, why = "高", f"有高度相似的历史需求（{sims[0].title}，相似度 {top:.0%}），且高影响问题基本答清"
    elif top >= 0.35:
        conf, why = "中", f"有相似历史需求可参照（相似度 {top:.0%}），但仍有 {unanswered_high} 个高影响问题未答复"
    else:
        conf, why = "低", f"无相似历史需求，且 {unanswered_high} 个高影响问题未答复；建议先问清再报数"
    tasks = allocate(build_tasks(card, text), mid)
    phases: dict[str, float] = {}
    ai_phases: dict[str, float] = {}
    for t in tasks:
        phases[t.phase] = round(phases.get(t.phase, 0.0) + t.trad_days, 1)
        ai_phases[t.phase] = round(ai_phases.get(t.phase, 0.0) + t.ai_days, 1)
    ai_mid = round(sum(t.ai_days for t in tasks), 1)
    ratio = ai_mid / mid if mid else 1.0
    split = who_split(tasks)
    return Estimate(dims, reasons, round(base, 1), round(history_days, 1) if history_days else None, round(mid, 1), round(low, 1), round(high, 1),
                    conf, why, sims, phases, unanswered_high, tasks, ai_mid, round(low * ratio, 1), round(high * ratio, 1),
                    round((1 - ratio) * 100), split, ai_phases, delivery_mode(card, split, text))
