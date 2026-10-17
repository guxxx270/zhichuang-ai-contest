"""需知 · Jira 需求评估页：粘贴单条工单，或上传 Jira 导出的 CSV / Excel，给出技术复杂度、工时与 AI 编码工时。
全部规则计算、不调模型；卡片与徽标样式沿用 app.py 里的 xz-* 类。"""
from __future__ import annotations

import html
import re
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from xuzhi import config, knowledge
from xuzhi.jira import JiraIssue, load_export, parse_export, parse_issue_text
from xuzhi.pipeline.assess import Assessment, Calibration, assess, assess_many, calibrate

JIRA_DIR = config.DATA_DIR / "jira"
SAMPLE_EXPORT = JIRA_DIR / "示例_迭代导出（虚构）.csv"
HISTORY_DIR = JIRA_DIR / "历史"          # 放公司 Jira 导出的历史工单（含耗费时间），校准即用真实数据
LEVEL_COLOR = {"低": "#34D399", "中": "#FBBF24", "高": "#FB923C", "很高": "#F87171"}
RISK_CLS = {"高": "hi", "中": "md", "低": "lo"}
SRC_OPTIONS = ["粘贴单条", "上传导出（批量）"]


@st.cache_data(ttl=600, show_spinner=False)
def load_history() -> tuple[list[JiraIssue], str]:
    """校准样本：data/jira/历史/ 下的导出；没有就用示例导出里已完成的工单（虚构）。"""
    files = sorted([*HISTORY_DIR.glob("*.csv"), *HISTORY_DIR.glob("*.xlsx")]) if HISTORY_DIR.exists() else []
    if files:
        issues = [it for f in files for it in load_export(f)]
        return [it for it in issues if it.done], f"data/jira/历史/ 下 {len(files)} 份导出"
    if SAMPLE_EXPORT.exists():
        return [it for it in load_export(SAMPLE_EXPORT) if it.done], "示例导出里已完成的工单（虚构）"
    return [], "无"


def issue_as_text(it: JiraIssue) -> str:
    """把一条工单排成「从 Jira 详情页复制」的样子，给粘贴框当示例。"""
    head = [f"类型：{it.issue_type}" if it.issue_type else "", f"优先级：{it.priority}" if it.priority else "",
            f"模块：{' / '.join(it.components)}" if it.components else "",
            f"Story Points：{it.story_points:g}" if it.story_points else "",
            f"原始预估：{it.original_h:g}h" if it.original_h else ""]
    lines = [f"【JIRA】{it.key} {it.summary}".strip(), "　".join(x for x in head if x)]
    if it.description:
        lines.append(f"描述：{it.description}")
    if it.acceptance:
        lines += ["验收标准："] + [f"{i}. {a}" for i, a in enumerate(it.acceptance, 1)]
    for label, v in (("复现步骤", it.repro), ("期望结果", it.expected), ("技术说明", it.tech_notes)):
        if v:
            lines.append(f"{label}：{v}")
    return "\n".join(x for x in lines if x)


def jira_text_from_analysis(a) -> str:
    """功能一 → 功能二：把「零散需求」整理出的需求卡与需求单排成一条 Jira 故事，拿去评估。"""
    card, spec, arch = a.card, a.spec, a.architecture
    blob = " ".join([*card.data_sources, arch.coverage_line])
    systems = [s.name for s in knowledge.systems() if s.name in blob][:3]
    acc: list[str] = []
    for s in spec.stories:
        for x in s.acceptance:
            if x not in acc:
                acc.append(x)
    head = ["类型：故事", "优先级：中"] + ([f"模块：{' / '.join(systems)}"] if systems else [])
    lines = [f"【JIRA】{card.title}", "　".join(head), f"描述：{card.goal}。" + "；".join(card.features[:4])]
    if acc:
        lines += ["验收标准："] + [f"{i}. {x}" for i, x in enumerate(acc[:8], 1)]
    lines.append(f"技术说明：{arch.coverage_line}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 结果展示
def _kpi(col, n: str, label: str, color: str) -> None:
    col.markdown(f'<div class="xz-kpi" style="--c:{color}"><div class="n">{n}</div><div class="l">{label}</div></div>', unsafe_allow_html=True)


def render_assessment(a: Assessment) -> None:
    it = a.issue
    st.markdown(f"### {html.escape(it.key)} {html.escape(it.summary)}")
    chips = [a.kind, it.issue_type, it.priority and f"优先级 {it.priority}", it.status, *it.components[:3],
             it.story_points and f"{it.story_points:g} 个故事点"]
    st.markdown("".join(f'<span class="xz-chip">{html.escape(str(c))}</span>' for c in chips if c), unsafe_allow_html=True)
    k = st.columns(4)
    _kpi(k[0], f"{a.level} · {a.total}/30", "技术复杂度（六维）", LEVEL_COLOR[a.level])
    _kpi(k[1], f"{a.mid} 人天", f"传统工时 · 区间 {a.low}～{a.high}", "#60A5FA")
    _kpi(k[2], f"{a.coding_ai} 人天", f"AI 编码工时 · 编码阶段 {a.coding_trad} → {a.coding_ai}（省 {a.coding_saving_pct}%）", "#22D3EE")
    _kpi(k[3], f"{a.full_ai} 人天", f"全流程 AI 协同 · {a.mid} → {a.full_ai}（省 {a.full_saving_pct}%）", "#A78BFA")
    notes = []
    if it.done and it.spent_days:
        notes.append(f":material/history: 已完成，实际耗时 **{it.spent_days:g} 人天**（需知估 {a.mid}，差 {(a.mid - it.spent_days) / it.spent_days:+.0%}）")
    elif a.jira_days:
        notes.append(f":material/compare_arrows: 对比 Jira：{a.jira_basis} ≈ {a.jira_days} 人天 → **{a.deviation_note}**")
    notes.append(f":material/verified: 置信度 **{a.confidence}**：{a.confidence_reason}")
    st.caption("　·　".join(notes))

    left, right = st.columns([1.25, 1], gap="medium")
    with left:
        st.markdown("**技术六维**（1～5 分，依据取自工单原文）")
        st.dataframe(pd.DataFrame({"维度": list(a.dims), "分": list(a.dims.values()), "依据": [a.reasons[d] for d in a.dims]}),
                     hide_index=True, width="stretch",
                     column_config={"分": st.column_config.ProgressColumn("分", min_value=0, max_value=5, format="%d")})
    with right:
        st.markdown("**质量检查**（代替追问：工单哪里写得不够）")
        if a.risks:
            for lv, msg in a.risks:
                st.markdown(f'<div class="xz-q {RISK_CLS[lv]}"><span class="xz-chip {RISK_CLS[lv]}">{lv}</span> {html.escape(msg)}</div>',
                            unsafe_allow_html=True)
        else:
            st.markdown('<div class="xz-q lo"><span class="xz-chip lo">通过</span> 工单写得完整，没有发现明显缺口</div>', unsafe_allow_html=True)
        if a.similar:
            st.markdown("**相似的已完成工单**（类比用）")
            for s, sc in a.similar:
                st.caption(f"{s.key} {s.summary} · 相似度 {sc:.0%} · 实际 {s.spent_days:g} 人天")
    st.markdown("**拆到任务：人工 vs AI**（编码阶段 = 主数「AI 编码工时」）")
    st.dataframe(pd.DataFrame([{"任务": t.name, "阶段": t.phase, "分工": t.who, "人工(人天)": t.trad_days, "AI(人天)": t.ai_days,
                                "系数": t.coef, "为什么": t.note} for t in a.tasks]),
                 hide_index=True, width="stretch")
    st.markdown(f'<div class="xz-foot">工时 = 类型基线 + 技术六维加权（规则模型 {a.model_days} 人天）→ 团队校准 → 同类型已完成工单类比；'
                f'{html.escape(a.calib_note)}。AI 系数为示例值，按公司 AI 编码工具的实际使用情况校准。</div>', unsafe_allow_html=True)


def _batch_frame(results: list[Assessment]) -> pd.DataFrame:
    rows = []
    for a in results:
        it = a.issue
        rows.append({
            "单号": it.key, "摘要": it.summary, "类型": a.kind, "状态": it.status or "",
            "复杂度": f"{a.level} {a.total}", "需知估(人天)": a.mid, "区间": f"{a.low}～{a.high}",
            "AI 编码(人天)": a.coding_ai, "编码阶段人工(人天)": a.coding_trad, "全流程 AI(人天)": a.full_ai,
            "Jira 原估(人天)": f"{a.jira_days:g}" if a.jira_days else "",   # 空值显示空白，不显示 None
            "对比原估": (f"{a.deviation:+.0%}" if a.deviation is not None and not it.done else ""),
            "实际(人天)": f"{it.spent_days:g}" if it.done else "", "置信度": a.confidence, "风险": len(a.risks),
        })
    return pd.DataFrame(rows)


def render_batch(results: list[Assessment], calib: Calibration, source: str) -> None:
    todo = [a for a in results if not a.issue.done] or results
    kinds = {}
    for a in todo:
        kinds[a.kind] = kinds.get(a.kind, 0) + 1
    trad = round(sum(a.mid for a in todo), 1)
    c_trad, c_ai = round(sum(a.coding_trad for a in todo), 1), round(sum(a.coding_ai for a in todo), 1)
    full_ai = round(sum(a.full_ai for a in todo), 1)
    flagged = sum(1 for a in todo if a.deviation is not None and abs(a.deviation) >= 0.4)
    k = st.columns(5)
    _kpi(k[0], f"{len(todo)} 条", "待评估 · " + " / ".join(f"{n} {v}" for n, v in kinds.items()), "#22D3EE")
    _kpi(k[1], f"{trad} 人天", "传统工时合计", "#60A5FA")
    _kpi(k[2], f"{c_ai} 人天", f"AI 编码工时 · 编码阶段 {c_trad} → {c_ai}（省 {round(100 * (1 - c_ai / c_trad)) if c_trad else 0}%）", "#22D3EE")
    _kpi(k[3], f"{full_ai} 人天", f"全流程 AI 协同（省 {round(100 * (1 - full_ai / trad)) if trad else 0}%）", "#A78BFA")
    _kpi(k[4], f"{flagged} 条", "与 Jira 原估偏差 ≥ 40%", "#FBBF24")
    st.caption(f":material/tune: {calib.note}。来源：{source}；已完成的工单同时列在表里，可对照需知估与实际。")

    cdf = pd.DataFrame([{"单号": a.issue.key, "口径": lab, "人天": v} for a in todo
                        for lab, v in (("编码阶段 · 人工", a.coding_trad), ("编码阶段 · AI", a.coding_ai))])
    if not cdf.empty:
        st.altair_chart(
            alt.Chart(cdf).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
            .encode(x=alt.X("单号:N", sort=None, title=None, axis=alt.Axis(labelAngle=0)),
                    xOffset=alt.XOffset("口径:N", sort=["编码阶段 · 人工", "编码阶段 · AI"]),
                    y=alt.Y("人天:Q", title=None),
                    color=alt.Color("口径:N", scale=alt.Scale(domain=["编码阶段 · 人工", "编码阶段 · AI"], range=["#60A5FA", "#22D3EE"]),
                                    legend=alt.Legend(orient="top", title=None)),
                    tooltip=["单号", "口径", "人天"])
            .properties(height=200),
            width="stretch",
        )
    ordered = [a for a in results if not a.issue.done] + [a for a in results if a.issue.done]   # 待评估的排前面
    df = _batch_frame(ordered)
    st.markdown("**逐条结果**（点一行看详情）")
    ev = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="jira_table",
                      height=min(38 + 35 * len(df), 640),
                      column_config={"摘要": st.column_config.TextColumn(width="large")})
    st.download_button("导出评估结果 .csv", df.to_csv(index=False).encode("utf-8-sig"), file_name="需知_Jira评估.csv",
                       mime="text/csv", icon=":material/download:")
    rows = list(getattr(getattr(ev, "selection", None), "rows", []) or [])
    pick = ordered[rows[0]] if rows else todo[0]
    st.write("")
    with st.container(border=True):
        render_assessment(pick)


# ---------------------------------------------------------------- 入口
def render_jira_mode() -> None:
    history, hist_src = load_history()
    src = st.segmented_control("工单来源", SRC_OPTIONS, default=SRC_OPTIONS[0], required=True, key="jira_src",
                               label_visibility="collapsed")
    if src == SRC_OPTIONS[0]:
        samples = [it for it in (load_export(SAMPLE_EXPORT) if SAMPLE_EXPORT.exists() else []) if not it.done]
        labels = ["（粘贴自己的）"] + [it.title for it in samples]
        c1, c2 = st.columns([2, 3])
        pick = c1.selectbox("选一个示例工单（或在下面粘贴）", labels, key="jira_sample")
        c2.caption(":material/content_paste: 从 Jira 详情页全选复制过来即可：单号、类型、模块、故事点、原始预估、描述、验收标准能认就认，缺的按缺失处理。")
        if st.session_state.get("jira_sample_shown") != pick:
            st.session_state["jira_sample_shown"] = pick
            st.session_state["jira_rev"] = st.session_state.get("jira_rev", 0) + 1
            if pick != labels[0]:
                st.session_state["jira_text"] = issue_as_text(samples[labels.index(pick) - 1])
        with st.form("jira_go"):
            text = st.text_area("工单内容", value=st.session_state.get("jira_text", ""), height=230,
                                key=f"jira_text_{st.session_state.get('jira_rev', 0)}",
                                placeholder="【JIRA】XZ-1024 持仓查询接口增加交易日归属字段\n类型：故事　模块：交易系统　Story Points：5　原始预估：2d\n描述：……\n验收标准：\n1. ……")
            go = st.form_submit_button("评估这条工单", type="primary", icon=":material/fact_check:")
        if go:
            issue = parse_issue_text(text)
            if not (issue.summary or issue.description):
                st.warning("没读到工单内容，请粘贴 Jira 工单或选一个示例。")
            else:
                st.session_state["jira_text"] = text
                st.session_state["jira_single"] = assess(issue, calibrate(history))
        a = st.session_state.get("jira_single")
        if a:
            with st.container(border=True):
                render_assessment(a)
    else:
        c1, c2 = st.columns([3, 1.2], vertical_alignment="bottom")
        up = c1.file_uploader("上传 Jira 导出（CSV / Excel）", type=["csv", "xlsx"], key="jira_upload",
                              help="Jira 筛选结果 → 导出 → CSV（所有字段）。时间列按秒识别；同名「模块」列会合并。")
        use_sample = c2.button("用示例导出（虚构 14 条）", icon=":material/dataset:", width="stretch")
        if up is not None and st.session_state.get("jira_upload_name") != (up.name, up.size):
            issues = parse_export(up.getvalue(), up.name)
            st.session_state["jira_upload_name"] = (up.name, up.size)
            if not issues:
                st.warning("没从文件里读到工单：请确认是 Jira 导出的 CSV / Excel，表头含「摘要」或「Summary」。")
            else:
                st.session_state["jira_batch"] = (*assess_many(issues, history), f"上传的 {up.name}（{len(issues)} 条）")
        if use_sample and SAMPLE_EXPORT.exists():
            issues = load_export(SAMPLE_EXPORT)
            st.session_state["jira_batch"] = (*assess_many(issues, history), f"示例导出（虚构，{len(issues)} 条）")
        batch = st.session_state.get("jira_batch")
        if batch:
            render_batch(*batch)
        else:
            st.caption(f"校准样本：{hist_src}。把公司 Jira 导出的历史工单（含耗费时间）放进 data/jira/历史/，估算就按你们团队的实际耗时校准。")
