"""把需知流水线的结果渲染成企微聊天里能看的一段 markdown。

企微流式消息支持常见 markdown（加粗、列表、行内代码），不支持表格与 HTML；
单条 content 上限 20480 字节，所以这里刻意只给"当场要用的结论"，完整需求单/原型留给 Web 端。
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, List, Optional

__all__ = ["render_analysis", "render_guide", "HELP_TEXT", "GUIDE_TEXT"]

_LABEL = re.compile(r"^<([^_>]+)_\d+>$")

HELP_TEXT = (
    "**需知 · 企微入口**\n"
    "把一堆话（需求、诉求、会议里的原话）直接发给我，我当场给你：\n"
    "- 🛡 隐盾：先脱敏，再进模型\n"
    "- ❓ 要和业务确认什么（可直接在聊天里回答）\n"
    "- ⏱ 复杂度与工时（传统人天 ｜ AI 协同人天）\n"
    "- 🧭 需要 IT 拍板的技术选择\n\n"
    "**回答问题**：直接发「1 按合约」这样的编号行，可以一次发多行。\n"
    "**命令**：/help 帮助 · /whoami 查看你的 userid · /reset 换一个需求 · /ping 存活检查"
)

GUIDE_TEXT = (
    "需求太短了，我不好下判断。把业务的原话整段发过来就行——"
    "谁提的、想解决什么、涉及哪些品种/系统/报表、什么时候要，有多少写多少，缺的我会问你。\n\n"
    "发 /help 看用法。"
)


def _clip(s: str, n: int) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _join(items: Optional[List[Any]], limit: int = 4, sep: str = "、") -> str:
    vals = [str(x).strip() for x in (items or []) if str(x).strip()]
    if not vals:
        return ""
    head = sep.join(vals[:limit])
    return head + (f" 等 {len(vals)} 项" if len(vals) > limit else "")


def _redact_summary(analysis: Any) -> str:
    total = int(getattr(analysis, "redacted", 0) or 0)
    if total <= 0:
        return ""
    kinds = Counter()
    for label in (getattr(analysis, "mapping", None) or {}):
        m = _LABEL.match(str(label))
        if m:
            kinds[m.group(1)] += 1
    detail = "、".join(f"{k} {v}" for k, v in kinds.most_common(4))
    return f"🛡 隐盾：进模型前脱敏 {total} 处" + (f"（{detail}）" if detail else "")


def _num(v: Any) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(f)) if abs(f - round(f)) < 0.05 else f"{f:.1f}"


def render_analysis(analysis: Any, *, answered: int = 0, max_questions: int = 4) -> str:
    """analysis 为 xuzhi.pipeline.Analysis。用 getattr 取值，字段缺失时降级而不是报错。"""
    card = getattr(analysis, "card", None)
    est = getattr(analysis, "estimate", None)
    arch = getattr(analysis, "architecture", None)
    questions = list(getattr(analysis, "questions", None) or [])

    out: List[str] = []
    title = _clip(getattr(card, "title", "") or "未命名需求", 40)
    out.append(f"**{title}**")

    meta = []
    if getattr(card, "req_type", ""):
        meta.append(str(card.req_type))
    users = _join(getattr(card, "users", None), 3)
    if users:
        meta.append("用户 " + users)
    if getattr(card, "deadline", ""):
        meta.append("期望 " + str(card.deadline))
    if meta:
        out.append(" · ".join(meta))
    goal = _clip(getattr(card, "goal", ""), 90)
    if goal:
        out.append(f"目标：{goal}")
    red = _redact_summary(analysis)
    if red:
        out.append(red)
    out.append("")

    # ---- 问清 ----
    unanswered = [q for q in questions if not (getattr(q, "answer", "") or "").strip()]
    shown = sorted(unanswered, key=lambda q: {"高": 0, "中": 1, "低": 2}.get(getattr(q, "impact", ""), 3))[:max_questions]
    if shown:
        out.append(f"**先和业务确认这 {len(shown)} 件事**（回复「1 你的答复」，可多行）")
        index = {q.id: i + 1 for i, q in enumerate(questions)}
        for q in shown:
            n = index.get(getattr(q, "id", ""), 0)
            impact = getattr(q, "impact", "") or ""
            out.append(f"{n}. [{impact}] {_clip(getattr(q, 'question', ''), 80)}")
            default = _clip(getattr(q, "default", ""), 46)
            if default:
                out.append(f"   未答就按：{default}")
        if len(unanswered) > len(shown):
            out.append(f"（还有 {len(unanswered) - len(shown)} 个次要问题，Web 端可看全）")
    elif questions:
        out.append("**问清**：该问的都答了，假设已收敛 ✔")
    if answered:
        out.append(f"_已收到 {answered} 条答复，以下结论已按答复重算_")
    out.append("")

    # ---- 估量（双轨）----
    if est is not None:
        mid, low, high = _num(getattr(est, "mid", 0)), _num(getattr(est, "low", 0)), _num(getattr(est, "high", 0))
        line = f"**工时**：传统 {mid} 人天（{low}~{high}）"
        ai_mid = getattr(est, "ai_mid", 0) or 0
        if ai_mid:
            saving = getattr(est, "saving_pct", 0) or 0
            line += f" ｜ AI 协同 **{_num(ai_mid)} 人天**"
            if saving:
                line += f"，省 {_num(saving)}%"
        out.append(line)
        conf = getattr(est, "confidence", "")
        if conf:
            bits = [f"把握度 {conf}"]
            unhigh = getattr(est, "unanswered_high", 0) or 0
            if unhigh:
                bits.append(f"{unhigh} 个高影响问题未答")
            delivery = getattr(est, "delivery", "")
            if delivery:
                bits.append(str(delivery))
            out.append("　" + "；".join(bits))
        out.append("")

    # ---- 定架：需要 IT 拍板 ----
    decisions = list(getattr(arch, "decisions", None) or [])
    if decisions:
        out.append("**需要 IT 拍板**")
        for d in decisions[:2]:
            rec = _clip(getattr(d, "recommended", ""), 30)
            out.append(f"- {_clip(getattr(d, 'topic', ''), 24)}：建议 {rec}" if rec
                       else f"- {_clip(getattr(d, 'topic', ''), 24)}")
        if len(decisions) > 2:
            out.append(f"（共 {len(decisions)} 项，Web 端有选项对比与理由）")
        out.append("")

    reuse = list(getattr(arch, "reuse", None) or [])
    if reuse:
        names = _join([getattr(r, "system", "") for r in reuse], 3)
        if names:
            out.append(f"**可复用**：{names}（别重复造）")
            out.append("")

    out.append("_完整需求单 / 可点原型 / 数据地图在需知 Web 端。换需求发 /reset_")
    engine = getattr(analysis, "engine", "") or ""
    seconds = getattr(analysis, "seconds", 0) or 0
    out.append(f"— {engine} · {_num(seconds)}s")
    return "\n".join(out).strip()


def render_guide() -> str:
    return GUIDE_TEXT
