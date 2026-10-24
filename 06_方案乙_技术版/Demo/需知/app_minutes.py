"""需知 · 纪要直通车：一段纪要里识别到几条需求时，在结果页签上方提示，可一键拆成几条分别受理。

拆分只用规则（xuzhi.pipeline.split）；每条走规则流水线（不调模型）、在一本账里各记一行，原来整段那行记一条「已拆分为 #…」，
看板与催办不再把整段那行算成一条需求。拆完在这里切换查看任意一条（或回到整段），下方各页签跟着换成那一条。"""
from __future__ import annotations

from typing import Callable

import pandas as pd
import streamlit as st

from xuzhi.ledger import Ledger
from xuzhi.memory import Memory
from xuzhi.pipeline import Analysis, analyze
from xuzhi.pipeline.split import MAX_SEGMENTS, Segment, source_hint, split_requirements

STATE = "minutes_split"   # {"parent": 整段那行编号, "children": [编号…], "rows": {编号: Analysis}, "drafts": {编号: 未重算的答复}}
PICK = "minutes_pick"


@st.cache_data(max_entries=64, show_spinner=False)
def _segments(text: str) -> list[Segment]:
    # 每次交互都会重跑页面：同一段原话只切一次；多切一条只为知道有没有超出上限
    return split_requirements(text, max_segments=MAX_SEGMENTS + 1)


def _rule_hours(a: Analysis) -> str:
    rule = (getattr(a.estimate, "references", {}) or {}).get("rule") or {}
    before, after = (rule.get("before") or {}).get("mid"), (rule.get("after") or {}).get("mid")
    if rule.get("status") != "ready" or not all(isinstance(v, (int, float)) for v in (before, after)):
        return "—"
    return f"{before:g} → {after:g}"


def _split(a: Analysis, parent: int, *, ledger: Ledger, memory: Memory, source: str) -> dict:
    """逐条按规则受理（不调模型）；材料与原型视图沿用整段那次，答复重算时口径一致。"""
    segments = split_requirements(a.raw_text)
    hint = source or source_hint(a.raw_text)
    children: list[int] = []
    rows: dict[int, Analysis] = {parent: a}
    for seg in segments:
        child = analyze(seg.text, hint, None, mobile=a.mobile, client_view=a.client_view, llm_polish=False,
                        drafts=list(a.drafts or []), repos=list(a.repos or []), memory=memory)
        rid = ledger.log_analysis(child)
        ledger.event(rid, "纪要拆分受理", f"同一段纪要拆出 {len(segments)} 条，第 {seg.index} 条（原纪要 #{parent}）")
        children.append(rid)
        rows[rid] = child
    ledger.mark_split(parent, children)   # 整段那行留原文与流水，看板、催办不再重复算它
    return {"parent": parent, "children": children, "rows": rows, "drafts": {}}


def _open(on_switch: Callable[[], None] | None) -> None:
    """切到另一条：先收起当前这条的最新结果，再把会话里的需求、编号与答复换成目标那条。"""
    ctx = st.session_state.get(STATE) or {}
    rows = ctx.get("rows") or {}
    target = st.session_state.get(PICK)
    current = int(st.session_state.get("req_id", 0) or 0)
    if target == current or target not in rows:
        return
    if on_switch is not None:
        on_switch()   # 当前这条若还有后台模型任务，换条前先结束，免得结果写错行
    drafts = ctx.setdefault("drafts", {})
    if current in rows:
        if st.session_state.get("analysis") is not None:
            rows[current] = st.session_state["analysis"]   # 答复重算、模型增强后的结果也带上
        # 填了还没点重算的答复也按条留着，切回来接着填（答复框的值比 answers 新：失焦和切换可能在同一轮里到）
        drafts[current] = {**(st.session_state.get("answers") or {}),
                           **{str(k)[4:]: v for k, v in st.session_state.items() if str(k).startswith("ans_")}}
    nxt = rows[target]
    st.session_state["analysis"] = nxt
    st.session_state["req_id"] = target
    st.session_state["answers"] = {**{q.id: q.answer for q in nxt.questions if q.answer.strip()}, **drafts.get(target, {})}
    for key in [k for k in st.session_state if str(k).startswith("ans_")]:
        del st.session_state[key]   # 答复框按目标那条重新填


def _scroll_here() -> None:
    """刚开工就识别到多条：把提示滚到眼前（顶替 app.py 原本滚到页签的那一下）。"""
    if st.session_state.pop("scroll_to_results", False):
        st.html("<script>setTimeout(function(){var t=document.querySelector('.st-key-minutes_banner');"
                "if(t)t.scrollIntoView({behavior:'smooth',block:'start'});},300);</script>",
                unsafe_allow_javascript=True)


def _render_offer(a: Analysis, segments: list[Segment], *, ledger: Ledger, memory: Memory, source: str) -> None:
    more = len(segments) > MAX_SEGMENTS
    segments = segments[:MAX_SEGMENTS]
    n = len(segments)
    with st.container(border=True, key="minutes_banner"):
        st.markdown(f"**:material/call_split: 纪要直通车：这段话里识别到 {n} 条需求"
                    + (f"（只列前 {MAX_SEGMENTS} 条）" if more else "") + "**")
        st.caption("按编号条目、提出方切换和「还有一件事」这类连接词识别，不调模型；参会名单、背景和「下周再议」这类决议不算需求。"
                   "拆开后每条单独走问清、写单、估量、定架、出样，在一本账里各记一行。"
                   + (f"单次最多拆 {MAX_SEGMENTS} 条，后面的请另贴一次。" if more else ""))
        st.dataframe(pd.DataFrame([{"序号": s.index, "提出方": s.requester or "未注明", "需求预览": s.title_hint,
                                    "拆分依据": s.reason} for s in segments]),
                     hide_index=True, width="stretch",
                     column_config={"序号": st.column_config.NumberColumn(width="small", alignment="center"),
                                    "提出方": st.column_config.TextColumn(width="small"),
                                    "需求预览": st.column_config.TextColumn(width="large"),
                                    "拆分依据": st.column_config.TextColumn(width="small")})
        if st.button(f"拆成 {n} 条分别受理", type="primary", icon=":material/call_split:", key="minutes_split_go"):
            parent = int(st.session_state.get("req_id", 0) or 0)
            with st.spinner(f"正在逐条受理 {n} 条需求……"):
                st.session_state[STATE] = _split(a, parent, ledger=ledger, memory=memory, source=source)
            st.rerun()
        _scroll_here()


def _render_done(ctx: dict, a: Analysis, current: int, *, busy: bool, on_switch: Callable[[], None] | None) -> None:
    parent, children, rows = ctx["parent"], ctx["children"], ctx["rows"]
    with st.container(border=True, key="minutes_banner"):
        st.markdown(f"**:material/call_split: 纪要直通车：已拆成 {len(children)} 条，一本账里各记一行**")
        table = []
        for rid in children:
            child = a if rid == current else rows[rid]   # 当前这条用会话里的最新结果
            table.append({"编号": f"#{rid}", "提出方": child.card.requester or "未注明", "标题": child.card.title,
                          "类型": child.card.req_type, "待确认": sum(1 for q in child.questions if not q.answer.strip()),
                          "规则参考（小时，以前 → 现在）": _rule_hours(child)})
        st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch",
                     column_config={"编号": st.column_config.TextColumn(width="small"),
                                    "提出方": st.column_config.TextColumn(width="small"),
                                    "标题": st.column_config.TextColumn(width="large"),
                                    "类型": st.column_config.TextColumn(width="small"),
                                    "待确认": st.column_config.NumberColumn(width="small", alignment="center",
                                                                          help="还没答复的追问条数"),
                                    "规则参考（小时，以前 → 现在）": st.column_config.TextColumn(
                                        width="medium", help="固定规则模板参考，未校准；模型估时在「估量」页单独看")})
        labels = {parent: f"整段纪要 #{parent}", **{rid: f"第 {i} 条 #{rid}" for i, rid in enumerate(children, 1)}}
        st.session_state[PICK] = current   # 跟着当前查看的那条走（开工、重算都不会改它）
        st.segmented_control("查看哪一条", list(labels), format_func=labels.get, key=PICK, required=True,
                             on_change=_open, args=(on_switch,), wrap=True)
        tip = (f"正在看第 {children.index(current) + 1} 条（共 {len(children)} 条）。" if current in children
               else "正在看整段纪要。")
        st.caption(tip + f"切换后下方各页签换成那一条，答复与重算记在各自那行账上；整段那行 #{parent} 保留原文，"
                   "流水里记了拆分去向，看板和催办不再重复算它。"
                   + ("后台模型还在分析当前这条，切换会结束这次分析。" if busy else ""))


def render_minutes_split(a: Analysis, *, ledger: Ledger, memory: Memory, source: str = "", busy: bool = False,
                         on_switch: Callable[[], None] | None = None) -> None:
    """放在结果页签上方：识别到 2 条以上需求时提示拆分；拆过的显示各条并可切换。单条需求不显示任何东西。
    source：页面上手选的来源（空 = 自动识别；纪要类自动给「会议纪要」）。on_switch：换条前调用，用来结束后台模型任务。"""
    current = int(st.session_state.get("req_id", 0) or 0)
    ctx = st.session_state.get(STATE)
    if ctx and current not in ctx["rows"]:
        st.session_state.pop(STATE, None)   # 已开工新需求，上一次的拆分不再挂在页面上
        ctx = None
    if ctx:
        _render_done(ctx, a, current, busy=busy, on_switch=on_switch)
        return
    segments = _segments(a.raw_text or "")
    if segments and current:
        _render_offer(a, segments, ledger=ledger, memory=memory, source=source)
