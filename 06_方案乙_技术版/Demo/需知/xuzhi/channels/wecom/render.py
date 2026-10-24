"""把需知流水线的结果渲染成企微聊天里能看的一段 markdown。

企微流式消息支持常见 markdown（加粗、列表、行内代码），不支持表格与 HTML；
单条 content 上限 20480 字节，所以这里刻意只给"当场要用的结论"，完整需求单/原型留给 Web 端。
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, List, Optional, Sequence, Tuple

from ...privacy import Redactor

__all__ = ["render_analysis", "render_guide", "HELP_TEXT", "GUIDE_TEXT"]

_LABEL = re.compile(r"^<([^_>]+)_\d+>$")

HELP_TEXT = (
    "**需知 · 企微入口**（两种用法）\n\n"
    "**① 需求分析**（默认）：把一堆话（需求、诉求、会议里的原话）直接发给我 →\n"
    "- 🛡 隐盾：先脱敏，再进模型\n"
    "- ❓ 要和业务确认什么（可直接在聊天里按编号回答）\n"
    "- ⏱ 模型估时（以前 ｜ 现在），并列规则模板与历史参考；未接入模型时只展示参考\n"
    "- 🧭 需要 IT 拍板的技术选择\n\n"
    "**② 问码**：问关于需知这个项目本身的问题，我去翻代码回答 →\n"
    "- 单条：`/问 隐盾的脱敏规则在哪实现的`\n"
    "- 连着问：`/模式 问码` 切过去，之后直接发问题；`/模式 需求` 切回来\n\n"
    "**命令**：/help 帮助 · /模式 看或切模式 · /whoami 查 userid · /reset 换一个需求 · /ping 存活检查\n"
    "/重估 模型估时失败或超时后，按原需求和已给的答复再估一次\n"
    "/摘要 一本账催办摘要（待确认超期 / 待对账 / 本周受理）· /记忆 追问记忆统计与清理"
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


def _failure_reason(error: str) -> str:
    """估时失败原因：去掉「请…重新分析」这类 Web 端操作提示（企微里用 /重估），句末标点交给后面的分号。"""
    s = re.sub(r"[。.]?\s*请[^。]*重新分析[。.]?\s*$", "", (error or "").strip())
    return _clip(s.rstrip("。. "), 120)


def _num(v: Any) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return str(int(f)) if abs(f - round(f)) < 0.05 else f"{f:.1f}"


def render_analysis(analysis: Any, *, answered: int = 0, max_questions: int = 4,
                    receipt: Optional[Sequence[Tuple[int, str]]] = None) -> str:
    """analysis 为 xuzhi.pipeline.Analysis。用 getattr 取值，字段缺失时降级而不是报错。
    receipt：本轮答复 [(题号, 答复)]，逐条回执，方便发现把新需求误当成了答复。"""
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
    recalled = [q for q in questions if (getattr(q, "answer", "") or "").strip() and getattr(q, "recalled", "")]
    if recalled:
        out.append(f"🧠 沿用上次口径 {len(recalled)} 条，本次不再问（有变化再告诉我）")
        for q in recalled[:3]:
            head = _clip(str(getattr(q, "question", "")).split("？")[0], 30)
            out.append(f"   · {head}？→ {_clip(getattr(q, 'answer', ''), 30)}")
    if answered:
        out.append(f"_已收到 {answered} 条答复，以下结论已按答复重算_")
        if receipt:
            red = Redactor()   # 回执不回显明文手机号、账号（sandbox.yaml wecom.reply_plain_sensitive）
            for num, ans in list(receipt)[:8]:
                out.append(f"   · 第 {num} 题 → {_clip(red.redact(str(ans)).text, 30)}")
            out.append("   如果这是一条新需求，请先发 /reset")
    out.append("")

    # ---- 估量（双轨）----
    references = getattr(est, "references", {}) or {}
    if est is not None and getattr(est, "status", "unavailable") != "ready":
        if getattr(est, "status", "") == "failed":
            reason = _failure_reason(getattr(est, "error", "")) or "模型没有返回可用结果"
            out.append(f"**工时**：模型估时未成功：{reason}；发 /重估 再试")
        else:
            out.append("**工时**：暂无模型估时（未接入模型）" + ("，规则与历史参考如下" if references else ""))
        out.append("")
    elif est is not None:
        mid, low, high = (_num(getattr(est, field) * 8) for field in ("mid", "low", "high"))
        line = f"**工时**：模型估算，以前 {mid} 小时（{low}~{high}）"
        ai_mid = getattr(est, "ai_mid", None)
        if ai_mid:
            saving = float(getattr(est, "saving_pct", 0) or 0)
            line += f" ｜ 现在 **{_num(ai_mid * 8)} 小时**"
            if saving > 0:
                line += f"，省 {_num(saving)}%"
            elif saving < 0:   # 现在比以前慢，不写「省 -25%」
                line += f"，多 {_num(-saving)}%"
        if getattr(est, "basis", "") != "code_excerpt":
            line += "（粗估：未核对代码）"
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

    rule = references.get("rule", {})
    if rule.get("status") == "ready":
        before, after = rule["before"], rule["after"]
        out.append(f"**规则模板参考（未校准）**：以前 {_num(before['low'])}～{_num(before['high'])} 小时"
                   f" ｜ 现在 {_num(after['low'])}～{_num(after['high'])} 小时；固定模板、未核对代码，详情见 Web。")
    history = references.get("history", {})
    if history:
        out.append(f"**历史记录参考**：{_clip(history.get('summary', '暂无记录'), 120)}")
    if references:
        out.append("_各来源独立展示，不合并成一个总工时；历史样本来源与匹配范围见 Web。_")
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
