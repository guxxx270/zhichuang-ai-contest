"""需知 · Jira 需求评估页：粘贴单条工单，或上传 Jira 导出的 CSV / Excel，给出技术复杂度、工时与 AI 编码工时。
全部规则计算、不调模型；卡片与徽标样式沿用 app.py 里的 xz-* 类。"""
from __future__ import annotations

import html
import re
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
from streamlit.errors import StreamlitAPIException

from xuzhi import config
from xuzhi.jira import JiraIssue, load_export, parse_export, parse_issue_text
from xuzhi.ledger import Ledger
from xuzhi.pipeline.assess import Assessment, Calibration, assess, assess_many, calibrate, is_epic

JIRA_DIR = config.DATA_DIR / "jira"
SAMPLE_EXPORT = JIRA_DIR / "示例_迭代导出（虚构）.csv"
HISTORY_DIR = JIRA_DIR / "历史"          # 放公司 Jira 导出的历史工单（含耗费时间），校准即用真实数据
LEVEL_COLOR = {"低": "#34D399", "中": "#FBBF24", "高": "#FB923C", "很高": "#F87171"}
RISK_CLS = {"高": "hi", "中": "md", "低": "lo"}
SRC_OPTIONS = ["粘贴单条", "上传导出（批量）"]
BAD_FILE = "读不了这个文件：请用 Jira「导出 CSV（所有字段）」，或另存为 UTF-8 的 CSV / xlsx"


@st.cache_data(ttl=600, show_spinner=False)
def load_history() -> tuple[list[JiraIssue], str]:
    """公司的历史工单：data/jira/历史/ 下的导出（只取已完成的）。没有就是空的——虚构示例不给真实工单当校准样本。"""
    files = sorted([*HISTORY_DIR.glob("*.csv"), *HISTORY_DIR.glob("*.xlsx")]) if HISTORY_DIR.exists() else []
    if files:
        issues = [it for f in files for it in load_export(f)]
        return [it for it in issues if it.done], f"data/jira/历史/ 下 {len(files)} 份导出"
    return [], "无"


@st.cache_data(ttl=600, show_spinner=False)
def load_sample() -> list[JiraIssue]:
    return load_export(SAMPLE_EXPORT) if SAMPLE_EXPORT.exists() else []


def sample_keys() -> set[str]:
    return {it.key for it in load_sample() if it.key}


def calib_sources(ledger: Ledger, include_sample: bool = False) -> dict[str, tuple[JiraIssue, str]]:
    """校准样本：单号 → (工单, 来源)，同一单号以一本账为准。虚构示例和真实工单两边不混——
    评估示例本身（include_sample）只用示例里已完成的工单和一本账里对过账的示例单号，演示数字不随之前的上传漂移；
    评估自己的工单只用 data/jira/历史/ 和一本账里的其他工单，一条示例都不用。"""
    keys = sample_keys()
    if include_sample:
        base, label = [it for it in load_sample() if it.done], "示例（虚构）"
    else:
        base, label = [it for it in load_history()[0] if it.key not in keys], "历史导出"
    pool = {it.key or it.summary: (it, label) for it in base}
    # 一本账里对过账的示例单号也还是虚构的：说明行照样记「示例（虚构）」，不能写成「一本账」让人当成真实工单
    pool.update({it.key or it.summary: (it, label if include_sample else "一本账")
                 for it in ledger.jira_done_issues() if (it.key in keys) == include_sample})
    return pool


def calib_pool(ledger: Ledger, include_sample: bool = False) -> list[JiraIssue]:
    """校准样本 = 历史导出里已完成的工单 + 一本账里对过账的 Jira 工单（同一单号以一本账为准；示例与真实工单不混）。"""
    return [it for it, _ in calib_sources(ledger, include_sample).values()]


def pool_note(sources: dict[str, tuple[JiraIssue, str]], batch: list[JiraIssue] | tuple = ()) -> str:
    """说明行里的样本构成：「校准样本：示例（虚构）8 条 · 一本账 1 条 · 本次 3 条」。
    同一单号按 本次 > 一本账 > 历史 只算一次；史诗不进校准样本。一条都没有时返回空串。"""
    now = {it.key or it.summary for it in batch if it.done and not is_epic(it)}
    counts: dict[str, int] = {}
    for k, (it, src) in sources.items():
        if k not in now and it.done and not is_epic(it):
            counts[src] = counts.get(src, 0) + 1
    if now:
        counts["本次"] = len(now)
    return ("校准样本：" + " · ".join(f"{s} {n} 条" for s, n in counts.items())) if counts else ""


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
    """功能一 → 功能二：把「零散需求」整理出的需求卡与需求单排成一条 Jira 故事，拿去评估。
    模块只写主承载平台；数据来源系统和目录命中词（「命中：结算、品种」）不写进工单——那是需知自己的分析，
    写进去会被当成工单原文，再算一遍涉及系统和业务口径。优先级没人定过，不写。"""
    card, spec, arch = a.card, a.spec, a.architecture
    host = arch.reuse[0].system if arch.reuse else ""
    acc: list[str] = []
    for s in spec.stories:
        for x in s.acceptance:
            if x not in acc:
                acc.append(x)
    head = ["类型：故事"] + ([f"模块：{host}"] if host else [])
    lines = [f"【JIRA】{card.title}", "　".join(head), f"描述：{card.goal}。" + "；".join(card.features[:4])]
    if acc:
        lines += ["验收标准："] + [f"{i}. {x}" for i, x in enumerate(acc[:8], 1)]
    # 「数据与渠道复用 …」也是需知按目录关键词做的复用分析，写进工单会被当成原文多算几个系统，只留主承载平台那句
    tech = re.sub(r"（命中：[^）]*）", "", arch.coverage_line)
    tech = re.sub(r"；数据与渠道复用 [^。；]*", "", tech)
    lines.append(f"技术说明：{tech}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 结果展示
def _kpi(col, n: str, label: str, color: str) -> None:
    col.markdown(f'<div class="xz-kpi" style="--c:{color}"><div class="n">{n}</div><div class="l">{label}</div></div>', unsafe_allow_html=True)


def render_assessment(a: Assessment, rid: int | None = None) -> None:
    it = a.issue
    st.markdown(f"### {html.escape(it.key)} {html.escape(it.summary)}")
    chips = [rid and f"一本账 #{rid}", a.kind, it.issue_type, it.priority and f"优先级 {it.priority}", it.status, *it.components[:3],
             it.story_points and f"{it.story_points:g} 个故事点"]
    st.markdown("".join(f'<span class="xz-chip">{html.escape(str(c))}</span>' for c in chips if c), unsafe_allow_html=True)
    k = st.columns(4)
    _kpi(k[0], f"{a.level} · {a.total}/30", "技术复杂度（六维）", LEVEL_COLOR[a.level])
    _kpi(k[1], f"{a.mid} 人天（{a.mid * 8:g} 小时）", f"传统工时 · 区间 {a.low}～{a.high}", "#60A5FA")   # 估量页按小时，这里同时写小时
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


def _batch_frame(results: list[Assessment], rids: dict[str, int]) -> pd.DataFrame:
    rows = []
    for a in results:
        it = a.issue
        jira = f"{a.jira_days:g}" if a.jira_days else ""   # 空值显示空白，不显示 None
        rows.append({
            "一本账": f"#{rids[it.key or it.summary]}" if (it.key or it.summary) in rids else "",
            "单号": it.key, "摘要": it.summary, "类型": "史诗" if is_epic(it) else a.kind, "状态": it.status or "",
            "复杂度": f"{a.level} {a.total}", "需知估(人天)": a.mid, "区间": f"{a.low}～{a.high}",
            "AI 编码(人天)": a.coding_ai, "编码阶段人工(人天)": a.coding_trad, "全流程 AI(人天)": a.full_ai,
            # 没填原始预估时的数是「故事点 × 团队每点人天」，需知自己折算的，不能写成 Jira 原估
            "Jira 原估(人天)": jira if it.original_h else "", "故事点折算(人天)": "" if it.original_h else jira,
            "对比原估": (f"{a.deviation:+.0%}" if a.deviation is not None and not it.status_done else ""),
            "实际(人天)": f"{it.spent_days:g}" if it.done else "", "置信度": a.confidence, "风险": len(a.risks),
        })
    return pd.DataFrame(rows)


_FORMULA_START = re.compile(r"[=+\-@\t\r]")
_PLAIN_NUMBER = re.compile(r"[+-]?\d+(?:\.\d+)?%?")


def _csv_cell(v):
    """导出 CSV 防公式注入：摘要等来自上传的文件，Excel 会把 = + - @ 开头的单元格当公式执行；「+75%」这类纯数字不动。"""
    if isinstance(v, str) and _FORMULA_START.match(v) and not _PLAIN_NUMBER.fullmatch(v):
        return "'" + v
    return v


def csv_safe(df: pd.DataFrame) -> pd.DataFrame:
    return df.apply(lambda col: col.map(_csv_cell))


def _raw(a: Assessment) -> tuple[float, float, float]:
    """(编码阶段人工, 编码阶段 AI, 全流程 AI)，取整前的值；没有就用取整后的。"""
    return tuple(getattr(a, f"{n}_raw", None) or getattr(a, n) for n in ("coding_trad", "coding_ai", "full_ai"))


def _pick(rows: list[int], ordered: list[Assessment], todo: list[Assessment], cells=()) -> Assessment:
    """表里选中的那条。换批次后旧的选中行号可能超出新表的行数，越界的不算，回到第一条待评估的。"""
    ok = [r for r in rows if 0 <= r < len(ordered)] or [c[0] for c in cells if 0 <= c[0] < len(ordered)]
    return ordered[ok[0]] if ok else todo[0]


def _table(df: pd.DataFrame, key: str):
    """逐条结果表：点行首方框或任一单元格都能选中一条（老版本 Streamlit 不支持选单元格，退回只能选行）。
    行高写死 35px：默认行高随主题字号变（baseFontSize 15 时约 33px），按 35px 算高度底部会留空。"""
    kw = dict(hide_index=True, width="stretch", on_select="rerun", key=key, row_height=35,
              height=min(35 * (len(df) + 1) + 3, 640), column_config={"摘要": st.column_config.TextColumn(width="large")})
    try:
        return st.dataframe(df, selection_mode=["single-row", "single-cell"], **kw)
    except StreamlitAPIException:
        return st.dataframe(df, selection_mode="single-row", **kw)


def render_batch(results: list[Assessment], calib: Calibration, source: str, rids: dict[str, int] | None = None,
                 pool_text: str = "") -> None:
    rids = rids or {}
    # 按「状态已完成」划分待评估（完成但没登记工时的也不算待评估）；能不能进校准另看有没有工时
    todo = [a for a in results if not a.issue.status_done] or results
    epics = [a for a in todo if is_epic(a.issue)]
    counted = [a for a in todo if not is_epic(a.issue)]   # 史诗粒度太大，单列、不计入合计
    kinds = {}
    for a in counted:
        kinds[a.kind] = kinds.get(a.kind, 0) + 1
    trad = round(sum(a.mid for a in counted), 1)
    c_trad, c_ai = round(sum(a.coding_trad for a in counted), 1), round(sum(a.coding_ai for a in counted), 1)
    full_ai = round(sum(a.full_ai for a in counted), 1)
    # 省时百分比按取整前的数算，和单条一致
    raw_trad, raw_ai, raw_full = (sum(_raw(a)[i] for a in counted) for i in range(3))
    flagged = sum(1 for a in counted if a.deviation is not None and abs(a.deviation) >= 0.4)
    k = st.columns(5)
    _kpi(k[0], f"{len(counted)} 条", "待评估 · " + " / ".join(f"{n} {v}" for n, v in kinds.items()), "#22D3EE")
    _kpi(k[1], f"{trad} 人天", "传统工时合计", "#60A5FA")
    _kpi(k[2], f"{c_ai} 人天", f"AI 编码工时 · 编码阶段 {c_trad} → {c_ai}（省 {round(100 * (1 - raw_ai / raw_trad)) if raw_trad else 0}%）", "#22D3EE")
    _kpi(k[3], f"{full_ai} 人天", f"全流程 AI 协同（省 {round(100 * (1 - raw_full / trad)) if trad else 0}%）", "#A78BFA")
    _kpi(k[4], f"{flagged} 条", "与原估 / 点数折算偏差 ≥ 40%", "#FBBF24")
    notes = [calib.note, pool_text, calib.backtest_line]
    st.caption(":material/tune: " + "。".join(x for x in notes if x)
               + f"。来源：{source}；已完成的工单也列在表里，按留一法估算（不拿自己校准自己），可对照需知估与实际。")
    if epics:
        st.caption(f":material/call_split: 另有史诗 {len(epics)} 条（{'、'.join(a.issue.key or a.issue.summary for a in epics)}）"
                   "粒度太大，单列在表里、不计入合计，建议先拆成故事再估。")

    cdf = pd.DataFrame([{"单号": a.issue.key, "口径": lab, "人天": v} for a in counted
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
    ordered = [a for a in results if not a.issue.status_done] + [a for a in results if a.issue.status_done]   # 待评估的排前面
    df = _batch_frame(ordered, rids)
    st.markdown("**逐条结果**（点一行看详情）")
    # key 随批次变：换一批工单后，前端不会把上一批的选中行带过来
    ev = _table(df, f"jira_table_{st.session_state.get('jira_batch_rev', 0)}")
    st.download_button("导出评估结果 .csv", csv_safe(df).to_csv(index=False).encode("utf-8-sig"), file_name="需知_Jira评估.csv",
                       mime="text/csv", icon=":material/download:")
    sel = getattr(ev, "selection", None)
    pick = _pick(list(getattr(sel, "rows", []) or []), ordered, todo, list(getattr(sel, "cells", []) or []))
    st.write("")
    with st.container(border=True):
        render_assessment(pick, rids.get(pick.issue.key or pick.issue.summary))


def _log_batch(ledger: Ledger, results: list[Assessment]) -> tuple[dict[str, int], str]:
    """整批记进一本账：同一单号只更新不重复；Jira 里已完成的按耗费时间对账。返回 (单号 → 编号, 提示语)。"""
    rids, new, upd = {}, 0, 0
    for a in results:
        rid, created = ledger.log_jira(a, "导出")
        rids[a.issue.key or a.issue.summary] = rid
        new, upd = new + created, upd + (not created)
    done = sum(1 for a in results if a.issue.done)
    note = f"已记进一本账：新增 {new} 条、更新 {upd} 条" + (f"；其中 {done} 条 Jira 里已完成的已按耗费时间对账，作为下次评估的校准样本" if done else "")
    return rids, note


def _missing_columns(issues: list[JiraIssue]) -> str:
    """读到了工单，但没认出摘要列 / 工时列：说清楚表头怎么写，别静默按缺失处理。"""
    miss = []
    if not any(it.summary for it in issues):
        miss.append("摘要（表头写「摘要」「概要」「主题」或「Summary」）")
    if not any(it.original_h or it.spent_h for it in issues):
        miss.append("工时（「原始预估 / 预估工时」「耗费时间 / 实际工时」；Jira 导出按秒，自己整理的表请在表头注明「小时」或「人天」），"
                    "没法和原估对比，也不能对账校准")
    return ("没读到" + "；也没读到".join(miss) + "。") if miss else ""


def _run_batch(ledger: Ledger, issues: list[JiraIssue], source: str, include_sample: bool, warn: str = "") -> None:
    sources = calib_sources(ledger, include_sample)
    results, calib = assess_many(issues, [it for it, _ in sources.values()])
    rids, note = _log_batch(ledger, results)
    st.session_state["jira_batch"] = (results, calib, source, rids, pool_note(sources, issues))
    st.session_state["jira_batch_note"] = note
    st.session_state["jira_batch_warn"] = warn
    st.session_state["jira_batch_rev"] = st.session_state.get("jira_batch_rev", 0) + 1   # 逐条表换 key，旧选中行不带过来


def _bridge_note() -> str:
    """写单页转过来的结果：说明和估量页规则模板的工时口径不同（ui 写入 st.session_state['jira_bridge']）。"""
    bridge = st.session_state.get("jira_bridge") or {}
    try:
        h = float(bridge.get("rule_before_h") or 0)
    except (TypeError, ValueError):
        h = 0.0
    if not h:
        return ""
    return (f"同一需求在估量页：规则模板 以前 {h:g} 小时（≈{h / 8:.1f} 人天），含需求澄清、数据接入、培训等全流程；"
            "本页是单条故事的技术工时（类型基线 + 六维），口径不同")


def _assess_pasted(ledger: Ledger, text: str, issue: JiraIssue) -> None:
    """评估粘贴的一条并记账。写单页转过来、文本没改过的，挂回原来那行账，不另开一行。"""
    include_sample = bool(issue.key) and issue.key in sample_keys()   # 评的就是示例工单：只用示例自己校准
    sources = calib_sources(ledger, include_sample)
    calib = calibrate([it for it, _ in sources.values()])
    made_of = pool_note(sources)
    if made_of:
        calib.note += "；" + made_of
    a = assess(issue, calib)
    st.session_state["jira_text"] = text
    st.session_state["jira_single"] = a
    bridge = st.session_state.get("jira_bridge") or {}
    rid = int(bridge.get("rid") or 0)
    if bridge and text.strip() == str(bridge.get("text") or "").strip() and rid and ledger.get(rid):
        ledger.attach_jira(rid, a)
        st.session_state["jira_single_rid"] = rid
        st.toast(f"已挂回一本账 #{rid}", icon=":material/menu_book:")
        return
    st.session_state.pop("jira_bridge", None)   # 文本改过：按一条新的 Jira 工单记账，以后也不再挂回写单那行
    rid, created = ledger.log_jira(a, "粘贴")
    st.session_state["jira_single_rid"] = rid
    st.toast(f"已记进一本账 #{rid}" + ("" if created else "（同一单号，已更新）"), icon=":material/menu_book:")


# ---------------------------------------------------------------- 入口
def render_jira_mode(ledger: Ledger, show_ledger=None) -> None:
    """评估结果都记进一本账（来源「Jira」）；一本账里对过账的 Jira 工单反过来当校准样本。"""
    src = st.segmented_control("工单来源", SRC_OPTIONS, default=SRC_OPTIONS[0], required=True, key="jira_src",
                               label_visibility="collapsed")
    if src == SRC_OPTIONS[0]:
        samples = [it for it in load_sample() if not it.done]
        labels = ["（粘贴自己的）"] + [it.title for it in samples]
        c1, c2 = st.columns([2, 3])
        pick = c1.selectbox("选一个示例工单（或在下面粘贴）", labels, key="jira_sample")
        c2.caption(":material/content_paste: 从 Jira 详情页全选复制过来即可：单号、类型、模块、故事点、原始预估、描述、验收标准能认就认，缺的按缺失处理。")
        if st.session_state.get("jira_sample_shown") != pick:
            st.session_state["jira_sample_shown"] = pick
            st.session_state["jira_rev"] = st.session_state.get("jira_rev", 0) + 1
            st.session_state.pop("jira_bridge", None)   # 换了示例：之后的评估不再挂回写单那行账
            # 切回「粘贴自己的」清空文本框（写单转过来时 _to_jira 已把两者都设成「粘贴自己的」，不会走到这里）
            st.session_state["jira_text"] = issue_as_text(samples[labels.index(pick) - 1]) if pick != labels[0] else ""
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
                _assess_pasted(ledger, text, issue)
        a = st.session_state.get("jira_single")
        if a:
            with st.container(border=True):
                render_assessment(a, st.session_state.get("jira_single_rid"))
                bridge_note = _bridge_note()
                if bridge_note:
                    st.caption(":material/info: " + bridge_note)
    else:
        c1, c2 = st.columns([3, 1.2], vertical_alignment="bottom")
        up = c1.file_uploader("上传 Jira 导出（CSV / Excel）", type=["csv", "xlsx"], key="jira_upload",
                              help="Jira 筛选结果 → 导出 → CSV（所有字段）。时间列按秒识别；同名「模块」列会合并。")
        use_sample = c2.button("用示例导出（虚构 14 条）", icon=":material/dataset:", width="stretch")
        # 按这次上传的 file_id 去重：看过示例、移除后再传同一个文件，也会重新评估
        upload_id = (getattr(up, "file_id", None) or (up.name, up.size)) if up is not None else None
        if up is None:
            st.session_state.pop("jira_upload_name", None)
        elif st.session_state.get("jira_upload_name") != upload_id:
            issues = None
            try:
                issues = parse_export(up.getvalue(), up.name)
            except Exception:   # 另存的网页、UTF-16、损坏的 xlsx：给句人话，不红屏
                st.error(BAD_FILE)
            finally:
                st.session_state["jira_upload_name"] = upload_id   # 解析失败也记下，免得每次刷新都重新解析、重复报错
            if issues == []:
                st.warning("没从文件里读到工单：请确认是 Jira 导出的 CSV / Excel，表头含「摘要」或「Summary」。")
            elif issues:
                _run_batch(ledger, issues, f"上传的 {up.name}（{len(issues)} 条）", include_sample=False,
                           warn=_missing_columns(issues))
        if use_sample and SAMPLE_EXPORT.exists():
            issues = load_export(SAMPLE_EXPORT)
            _run_batch(ledger, issues, f"示例导出（虚构，{len(issues)} 条）", include_sample=True)
        batch = st.session_state.get("jira_batch")
        if batch:
            if st.session_state.get("jira_batch_warn"):
                st.warning(st.session_state["jira_batch_warn"], icon=":material/table_view:")
            if st.session_state.get("jira_batch_note"):
                st.caption(":material/menu_book: " + st.session_state["jira_batch_note"] + "。页面底部「查看一本账」可看。")
            render_batch(*batch)
        else:
            st.caption(f"校准样本：自己的工单只用 data/jira/历史/ 下的导出（现有：{load_history()[1]}）和一本账里对过账的工单，"
                       "不混入虚构示例；评估示例时只用示例自己的。把公司 Jira 导出的历史工单（含耗费时间）放进 data/jira/历史/，"
                       "估算就按你们团队的实际耗时校准。")
    if show_ledger is not None:
        st.write("")
        dash = ledger.dashboard()
        if st.toggle(f"查看一本账 · {dash['count']} 条（其中 Jira {dash['jira_count']} 条）", key="jira_show_ledger"):
            show_ledger()
