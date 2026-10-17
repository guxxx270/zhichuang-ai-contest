"""需知 · Demo 界面（Streamlit）。启动：streamlit run app.py"""
from __future__ import annotations

import html
import re

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from xuzhi import config, knowledge, sandbox
from xuzhi.asr import correct_domain_speech
from xuzhi.audit import Audit
from xuzhi.drafts import (
    Draft,
    RepoBundle,
    draft_from_bytes,
    draft_usability_hints,
    load_urls,
    materials_report,
    visual_drafts,
)
from xuzhi.ledger import STATUSES, Ledger
from xuzhi.llm import LLM
from xuzhi.memory import Memory
from xuzhi.pipeline import analyze
from xuzhi.pipeline.prototype import apply_prototype_view, render as render_proto
from xuzhi.speech import append_dictation, dictation_bar

B = config.BRAND


def _sandbox_preview_html(html_src: str) -> str:
    """预览沙箱：外链不跳走。若出样已带 data-xz-inert 交互脚本，则不再重复注入。"""
    page = html_src or ""
    page = re.sub(r"""\s(srcdoc)\s*=\s*(['"])(.*?)\2""", "", page, flags=re.I)
    page = re.sub(r"<form\b", '<form onsubmit="return false;"', page, flags=re.I)
    if "data-xz-inert" in page or "xz-inert-toast" in page:
        # 出样页已处理：界外可点不可进；此处只挡表单
        return page
    page = re.sub(r"""\shref\s*=\s*(['"])(.*?)\1""", ' href="#"', page, flags=re.I)
    guard = (
        "<script>(function(){document.addEventListener('click',function(e){"
        "var a=e.target&&e.target.closest&&e.target.closest('a');"
        "if(a){e.preventDefault();e.stopPropagation();}},true);"
        "document.addEventListener('submit',function(e){e.preventDefault();},true);"
        "})();</script>"
    )
    if re.search(r"</body\s*>", page, flags=re.I):
        return re.sub(r"</body\s*>", guard + "</body>", page, count=1, flags=re.I)
    return page + guard


def _iframe(html_src: str, height: int, bg: str = "#fff") -> None:
    """预览 HTML：外层仍用 components.html（勿用 st.iframe——它把参数当 URL），
    但内容再包一层 sandbox="allow-scripts" 的 iframe（不给 allow-same-origin）：
    仓库 / 模型产出的 HTML 里的脚本跑在独立源上，读不到需知页面、Key 输入框和 cookie。"""
    inner = _sandbox_preview_html(html_src or "")
    wrapper = (
        '<iframe sandbox="allow-scripts" '
        f'srcdoc="{html.escape(inner, quote=True)}" '
        f'style="width:100%;height:{height}px;border:1px solid #22304F;border-radius:12px;background:{bg}" title="xuzhi_preview"></iframe>'
    )
    components.html(wrapper, height=height + 8, scrolling=False)

st.set_page_config(page_title=f"{config.PRODUCT_NAME} · 需求分析智能体", page_icon="📋", layout="wide")
# 暗色科技风：主题色在 .streamlit/config.toml；这里只管自绘的卡片、徽标与背景。七项技能各一色，投屏时一眼分得清。
SKILL_COLORS = {"问清": "#22D3EE", "写单": "#60A5FA", "估量": "#A78BFA", "定架": "#2DD4BF", "出样": "#F472B6", "对账": "#FBBF24", "隐盾": "#34D399"}
st.markdown("""
<style>
:root { --xz-bg:#070B16; --xz-panel:rgba(17,26,46,.72); --xz-line:#22304F; --xz-text:#E6ECF8; --xz-mute:#8A97B4;
        --xz-cyan:#22D3EE; --xz-blue:#4361EE; --xz-violet:#A78BFA; --xz-pink:#F472B6; --xz-amber:#FBBF24; --xz-green:#34D399; --xz-red:#F87171; }
.stApp { background:
    radial-gradient(1100px 520px at 8% -8%, rgba(34,211,238,.13), transparent 62%),
    radial-gradient(900px 520px at 96% -4%, rgba(167,139,250,.15), transparent 60%),
    linear-gradient(rgba(96,165,250,.045) 1px, transparent 1px) 0 0 / 34px 34px,
    linear-gradient(90deg, rgba(96,165,250,.045) 1px, transparent 1px) 0 0 / 34px 34px,
    var(--xz-bg); }
header[data-testid="stHeader"] { background: transparent; }
.block-container { padding-top: 2.2rem; }
/* 顶部品牌区：玻璃卡片 + 青紫渐变描边 */
.xz-hero { position:relative; border-radius:20px; padding:22px 28px 18px; margin-bottom:16px; color:var(--xz-text);
  background: linear-gradient(135deg, rgba(20,31,56,.92), rgba(12,19,36,.92)) padding-box,
              linear-gradient(120deg, #22D3EE, #4361EE 45%, #A78BFA 80%, #F472B6) border-box;
  border:1px solid transparent; box-shadow:0 0 0 1px rgba(34,211,238,.05), 0 18px 48px rgba(3,7,18,.55), 0 0 60px rgba(67,97,238,.12); }
.xz-hero-top { display:flex; justify-content:space-between; align-items:flex-start; gap:16px; flex-wrap:wrap; }
.xz-brand { display:flex; align-items:center; gap:14px; }
.xz-logo { width:46px; height:46px; border-radius:13px; display:flex; align-items:center; justify-content:center; font-size:22px; font-weight:800;
  color:#fff; background:linear-gradient(135deg,#22D3EE,#4361EE 55%,#A78BFA); box-shadow:0 0 22px rgba(34,211,238,.35); }
.xz-name { font-size:30px; font-weight:800; letter-spacing:3px; line-height:1.1;
  background:linear-gradient(90deg,#E6ECF8,#9BE7F7 55%,#C4B5FD); -webkit-background-clip:text; background-clip:text; color:transparent; }
.xz-en { font-size:12px; color:var(--xz-mute); letter-spacing:1.5px; margin-top:3px; }
.xz-hero .slogan { font-size:15px; color:#C7D2E8; margin:12px 0 2px; }
.xz-status { display:inline-flex; align-items:center; gap:8px; padding:6px 12px; border-radius:999px; font-size:12.5px;
  background:rgba(15,23,42,.7); border:1px solid var(--xz-line); color:#C7D2E8; white-space:nowrap; }
.xz-status .dot { width:8px; height:8px; border-radius:50%; background:var(--xz-cyan); box-shadow:0 0 10px var(--xz-cyan); animation:xzpulse 1.8s infinite; }
.xz-status.model .dot { background:var(--xz-green); box-shadow:0 0 10px var(--xz-green); }
@keyframes xzpulse { 0%,100% { opacity:1; } 50% { opacity:.35; } }
.xz-skills { display:flex; flex-wrap:wrap; gap:8px; margin-top:12px; }
.xz-skill { --c:#22D3EE; display:inline-flex; align-items:center; gap:6px; padding:4px 11px; border-radius:999px; font-size:12.5px;
  color:#DCE4F5; background:color-mix(in srgb, var(--c) 12%, transparent); border:1px solid color-mix(in srgb, var(--c) 45%, transparent); }
.xz-skill b { color:var(--c); font-weight:700; }
/* 指标卡：每张一种强调色 */
.xz-kpi { --c:#22D3EE; position:relative; overflow:hidden; background:var(--xz-panel); border:1px solid var(--xz-line); border-radius:14px; padding:13px 16px;
  backdrop-filter: blur(6px); }
.xz-kpi::before { content:""; position:absolute; left:0; top:0; right:0; height:2px; background:linear-gradient(90deg, var(--c), transparent); }
.xz-kpi::after { content:""; position:absolute; right:-30px; top:-30px; width:90px; height:90px; border-radius:50%;
  background:radial-gradient(circle, color-mix(in srgb, var(--c) 28%, transparent), transparent 70%); }
.xz-kpi .n { font-size:24px; font-weight:750; color:var(--c); font-variant-numeric:tabular-nums; letter-spacing:.5px; }
.xz-kpi .l { font-size:12px; color:var(--xz-mute); margin-top:2px; }
/* 小节标题 */
.xz-sec { display:flex; align-items:baseline; gap:10px; margin:6px 0 8px; }
.xz-sec .no { font-size:12px; font-weight:700; color:#0B1222; background:linear-gradient(135deg,#22D3EE,#A78BFA); border-radius:6px; padding:1px 7px; }
.xz-sec .t { font-size:17px; font-weight:700; color:var(--xz-text); }
.xz-sec .s { font-size:13px; color:var(--xz-mute); }
/* 徽标与追问卡 */
.xz-chip { display:inline-block; background:rgba(96,165,250,.12); color:#BFD4FF; border:1px solid rgba(96,165,250,.28); border-radius:999px;
  padding:1px 9px; font-size:12px; margin:2px 4px 2px 0; }
.xz-chip.hi { background:rgba(248,113,113,.14); color:#FCA5A5; border-color:rgba(248,113,113,.4); }
.xz-chip.md { background:rgba(251,191,36,.13); color:#FCD34D; border-color:rgba(251,191,36,.38); }
.xz-chip.lo { background:rgba(52,211,153,.13); color:#6EE7B7; border-color:rgba(52,211,153,.38); }
.xz-chip.fut { background:rgba(34,211,238,.12); color:#67E8F9; border-color:rgba(34,211,238,.42); }
.xz-chip.mem { background:rgba(167,139,250,.14); color:#C4B5FD; border-color:rgba(167,139,250,.42); }
.xz-q { background:var(--xz-panel); border:1px solid var(--xz-line); border-left:3px solid var(--xz-amber); border-radius:12px; padding:10px 14px; margin-bottom:6px; }
.xz-q.hi { border-left-color:var(--xz-red); } .xz-q.md { border-left-color:var(--xz-amber); } .xz-q.lo { border-left-color:var(--xz-green); }
.xz-q b { color:var(--xz-text); }
.xz-q .why { color:var(--xz-mute); font-size:12px; margin-top:3px; }
.xz-card { background:var(--xz-panel); border:1px solid var(--xz-line); border-radius:12px; padding:14px 16px; margin-bottom:10px; }
.xz-foot { color:var(--xz-mute); font-size:12px; margin-top:6px; }
/* 主按钮：蓝→青渐变 + 发光 */
[data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primaryFormSubmit"] {
  background:linear-gradient(120deg,#4361EE,#2563EB 45%,#0891B2) !important; border:0 !important;
  box-shadow:0 6px 22px rgba(67,97,238,.35); font-weight:650; letter-spacing:.5px; }
[data-testid="stBaseButton-primary"]:hover, [data-testid="stBaseButton-primaryFormSubmit"]:hover { box-shadow:0 8px 28px rgba(34,211,238,.45); }
/* 结果页签 */
div[data-testid="stTabs"] [role="tab"] p { font-size:15.5px; font-weight:600; }
div[data-testid="stTabs"] [role="tab"][aria-selected="true"] p { color:#9BE7F7; }
[data-testid="stForm"], [data-testid="stExpander"] details { background:rgba(13,20,38,.55); }
[data-testid="stMetric"] { background:var(--xz-panel); border:1px solid var(--xz-line); border-radius:12px; padding:10px 14px; }
[data-testid="stMetricValue"] { color:#9BE7F7; }
iframe[title="xuzhi_dictation"] { border: none !important; }
</style>""", unsafe_allow_html=True)


@st.cache_resource
def get_ledger() -> Ledger:
    return Ledger()


@st.cache_resource
def get_memory() -> Memory:
    """追问记忆：与一本账同一个 SQLite。"""
    return Memory()


@st.cache_resource
def get_audit() -> Audit:
    """模型调用审计：与一本账同一个 SQLite。"""
    return Audit()


def _has_actual_days(value) -> bool:
    if value is None or value == "":
        return False
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _refresh_ledger_grid() -> None:
    """表格里的状态下拉按库里的新值重画。"""
    st.session_state["ledger_grid_rev"] = st.session_state.get("ledger_grid_rev", 0) + 1


CHART_COLORS = ["#22D3EE", "#A78BFA", "#34D399", "#FBBF24", "#F472B6", "#FB923C", "#60A5FA", "#2DD4BF"]


def _color_bars(data: dict, value_name: str, height: int = 220, colors: list[str] | None = None) -> None:
    """一类一色的柱状图：暗底上比同色柱子好认；类别名就在横轴上，不再出图例。"""
    df = pd.DataFrame({"类别": list(data.keys()), value_name: list(data.values())})
    chart = (
        alt.Chart(df)
        .mark_bar(cornerRadiusTopLeft=5, cornerRadiusTopRight=5)
        .encode(
            x=alt.X("类别:N", sort=None, title=None, axis=alt.Axis(labelAngle=-30)),
            y=alt.Y(f"{value_name}:Q", title=None),
            color=alt.Color("类别:N", sort=None, legend=None, scale=alt.Scale(range=colors or CHART_COLORS)),
            tooltip=["类别", f"{value_name}:Q"],
        )
        .properties(height=height)
    )
    st.altair_chart(chart, width="stretch")


def render_ledger(ledger: Ledger, memory: Memory, audit: Audit, *, req_id: int = 0, mid: float | None = None) -> None:
    """一本账：按编号补任意历史需求的实际人天。刚开工的那条默认选中。"""
    catalog = list(reversed(ledger.export_rows()))
    st.markdown("#### 对账")
    st.caption("从台账里选一条需求，回写交付后的实际人天。状态在下面的表格里改。刚开工的那条会默认选中；不必先点开工。")
    if not catalog:
        st.info("台账里还没有需求。开工之后会出现在这里，可以直接补实际人天。")
    else:
        labels = {
            int(r["id"]): f"#{int(r['id'])} {r.get('title') or '未命名'} · {r.get('status') or '受理'}"
            for r in catalog
        }
        ids = list(labels)
        bound = int(req_id or 0)
        if bound in labels and st.session_state.get("ledger_bound_req") != bound:
            st.session_state["ledger_pick"] = bound
            st.session_state["ledger_bound_req"] = bound
        if st.session_state.get("ledger_pick") not in labels:
            st.session_state["ledger_pick"] = ids[0]
        picked = int(st.selectbox("要录的需求", ids, format_func=lambda i: labels[i], key="ledger_pick"))
        row_now = ledger.get(picked)
        if not row_now:
            st.warning("这条台账已经不在库里，请改选另一条。")
        else:
            shown_mid = mid if picked == bound and mid is not None else row_now.get("mid_days")
            try:
                share = float(row_now.get("ai_share") if row_now.get("ai_share") is not None else 0.5)
            except (TypeError, ValueError):
                share = 0.5
            share = min(1.0, max(0.0, round(share, 1)))
            fb1, fb2, _fbpad = st.columns([1, 1, 4])
            if fb1.button("👍 有用", key=f"fb_up_{picked}"):
                ledger.feedback(picked, "整体", 1)
                st.toast("已记录")
            if fb2.button("👎 没用", key=f"fb_down_{picked}"):
                ledger.feedback(picked, "整体", -1)
                st.toast("已记录")
            c2, c3 = st.columns([2.2, 1.2])
            with c2:
                st.markdown("**对账：交付后回写实际人天**（写进历史需求库，估量飞轮）")
                r1, r2, r3 = st.columns([1, 1, 1])
                actual_days = r1.number_input(
                    "实际人天", min_value=0.0, step=0.5,
                    value=float(row_now.get("actual_days") or shown_mid or 0.0),
                    key=f"rec_actual_{picked}",
                )
                ai_used = r2.toggle("用了 AI 协同", value=bool(row_now.get("ai_assisted")), key=f"rec_ai_{picked}")
                ai_share = r3.slider(
                    "AI 参与度", 0.0, 1.0, share, 0.1,
                    key=f"rec_share_{picked}",
                    disabled=not ai_used,
                )
                rec_note = st.text_input(
                    "备注（可空）", value=row_now.get("close_note") or "",
                    key=f"rec_note_{picked}", placeholder="例：范围比估时多了客户版",
                )
                if st.button("✅ 对账并回写历史需求库", key=f"rec_submit_{picked}"):
                    res = ledger.reconcile(picked, actual_days, ai_used, ai_share if ai_used else 0.0, rec_note)
                    _refresh_ledger_grid()
                    st.success(f"#{picked} 已对账：估 {res['estimate']} 人天，实际 {res['actual']} 人天，偏差 {res['deviation']:+.0%}；已写入历史需求库（{res['history_id']}），下次估量即用。")
                    st.rerun()
            with c3:
                st.markdown("**本条流水**")
                events = ledger.events_of(picked)[-8:]
                if events:
                    for ev in events:
                        st.caption(f"{ev['ts'][5:16]}　{ev['action']}　{ev['detail']}")
                else:
                    st.caption("还没有流水。")

    st.markdown("#### 需求经营看板")
    dash = ledger.dashboard()
    if not dash["count"]:
        st.info("台账里还没有需求。")
    else:
        _mem = memory.stats()
        k1, k2, k3, k4, k5, k6, k7 = st.columns(7)
        k1.metric("需求数", f"{dash['count']}", help=f"来自 {dash['depts']} 个部门")
        k2.metric("平均追问", f"{dash['avg_questions']} 条", help=f"业务答复率 {dash['answer_rate']:.0%}")
        k7.metric("记忆省问", f"{_mem['hits']} 条", help=f"追问记忆：记住 {_mem['requesters']} 个提出方的 {_mem['entries']} 条口径；命中即不再问")
        k3.metric("平均澄清轮次", f"{dash['avg_rounds']}", help="开工一轮 + 每次按业务答复重算算一轮；人工通常三四轮")
        k4.metric("AI 协同省时", f"{dash['saved_days']} 人天", delta=f"-{dash['saving_pct']}%", delta_color="inverse",
                  help=f"传统估 {dash['trad_days']} → AI 协同估 {dash['ai_days']}")
        k5.metric("估算偏差", "—" if dash["mape"] is None else f"{dash['mape']}%",
                  help=("已对账 " + str(dash["closed"]) + " 条；平均绝对偏差（估 vs 实际；用了 AI 协同的比 AI 轨估算）"
                        + "".join(f"，{n} {v}%" for n, v in (("传统轨", dash["mape_trad"]), ("AI 轨", dash["mape_ai"])) if v is not None))
                  if dash["closed"] else "还没有对账记录：交付后在上方回写实际人天")
        k6.metric("出稿用时", ("<1 s" if dash["avg_seconds"] < 1 else f"{dash['avg_seconds']} s"), help=f"规则引擎毫秒级，模型润色另计；👍 {dash['thumbs_up']} / 👎 {dash['thumbs_down']}")
        g1, g2, g3 = st.columns(3)
        with g1:
            st.markdown("**各部门需求量**")
            _color_bars(dash["by_dept"], "需求数")
        with g2:
            st.markdown("**按类型**")
            _color_bars(dash["by_type"], "需求数", colors=CHART_COLORS[3:] + CHART_COLORS[:3])
        with g3:
            st.markdown("**状态漏斗**")
            _ord = "①②③④⑤"
            _color_bars({f"{_ord[i]} {k}": v for i, (k, v) in enumerate(dash["by_status"].items())}, "条数",
                        colors=["#22D3EE", "#60A5FA", "#A78BFA", "#FBBF24", "#34D399"])   # 受理 → 已对账，由冷到暖再到完成
        if len(dash["by_month"]) >= 2:
            st.markdown("**按月受理**")
            _m = pd.DataFrame({"月份": list(dash["by_month"].keys()), "需求数": list(dash["by_month"].values())})
            _fade = alt.Gradient(gradient="linear", x1=1, x2=1, y1=0, y2=1,   # 上浓下淡，不是一整块实色
                                 stops=[alt.GradientStop(color="rgba(34,211,238,0.38)", offset=0),
                                        alt.GradientStop(color="rgba(34,211,238,0.02)", offset=1)])
            st.altair_chart(
                alt.Chart(_m).mark_area(line={"color": "#22D3EE", "strokeWidth": 2}, color=_fade,
                                        point={"color": "#22D3EE", "filled": True, "size": 60})
                .encode(x=alt.X("月份:N", title=None, axis=alt.Axis(labelAngle=0)), y=alt.Y("需求数:Q", title=None),
                        tooltip=["月份", "需求数"])
                .properties(height=180),
                width="stretch",
            )

    with st.expander(f"🧠 追问记忆（问过的不再问）· {memory.stats()['entries']} 条口径", expanded=False):
        st.caption("业务答复过的题按「提出方（部门·角色）」与「部门」两级记住；下次同一提出方遇到同一道题直接沿用，清单里标 🧠。"
                   "只记答复口径，不记原话与客户信息；与一本账同库，可导出、可清空。")
        _mrows = [r for r in memory.rows() if r["scope"] == "requester"]
        if _mrows:
            st.dataframe(pd.DataFrame(_mrows)[["scope_key", "qid", "question", "answer", "req_type", "ts", "hits"]].rename(
                columns={"scope_key": "提出方", "qid": "题号", "question": "问题", "answer": "沿用口径", "req_type": "需求类型", "ts": "记住时间", "hits": "沿用次数"}),
                hide_index=True, width="stretch")
            mc1, mc2 = st.columns([1, 3])
            if mc1.button("清空全部记忆", key="mem_clear"):
                memory.forget()
                st.toast("已清空追问记忆")
                st.rerun()
            mc2.caption("要清某一个提出方：在企微发 /记忆 清 <提出方>，或直接删 data/ledger.sqlite3 里 answer_memory 表对应行。")
        else:
            st.info("还没有记忆：在「问清」里填业务答复并重算，口径就会被记住。")

    st.markdown("#### 台账明细")
    st.caption("放不下时可以左右滑动。状态只在这里改：下拉选好，改多行后点「保存状态」。对账写上实际人天后，这一列会变成「已对账」。备注、AI 参与度是当时填的；没存过参与度的行是空的。改成「已对账」时，这一行须已有实际人天，否则请用上方对账。")
    _skipped = st.session_state.pop("ledger_status_skipped", None)
    if _skipped:
        st.warning("这些编号改成「已对账」但没有实际人天，未保存：" + "、".join(f"#{i}" for i in _skipped) + "。请用上方对账表单填写。")
    rows = ledger.recent(500)
    if rows:
        grid_rev = st.session_state.get("ledger_grid_rev", 0)
        # 编号 时间 需求 部门 来源 类型 提出方 引擎 状态 问题数 已答复 高影响未答 传统 AI协同 AI参与度 实际 备注 置信度 脱敏处
        col_spec = [
            ("编号", 0.55, "id"), ("时间", 1.55, "ts"), ("需求", 2.2, "title"), ("部门", 0.8, "dept"),
            ("来源", 0.85, "source"), ("类型", 0.65, "req_type"), ("提出方", 0.9, "requester"), ("引擎", 0.95, "engine"),
            ("状态", 1.35, None),
            ("问题数", 0.7, "n_questions"), ("已答复", 0.7, "n_answered"), ("高影响未答", 0.9, "n_high_open"),
            ("传统(人天)", 0.85, "mid_days"), ("AI 协同(人天)", 1.05, "ai_days"), ("AI 参与度", 0.85, "ai_share"),
            ("实际(人天)", 0.85, "actual_days"),
            ("备注", 1.8, "close_note"),
            ("置信度", 0.65, "confidence"), ("脱敏处", 0.65, "redacted"),
        ]
        widths = [w for _, w, _ in col_spec]

        def _txt(value) -> str:
            return "" if value is None or value == "" else str(value)

        def _cell(row: dict, field: str | None) -> str:
            if field == "title":
                return _txt(row.get("title")) or "未命名"
            if field == "ai_share":
                value = row.get("ai_share")
                if value is None or value == "":
                    return ""
                try:
                    return f"{float(value):.1f}"
                except (TypeError, ValueError):
                    return _txt(value)
            if field is None:
                return ""
            return _txt(row.get(field))

        with st.form("ledger_status_batch"):
            save_top = st.form_submit_button("保存状态", type="primary", key="ledger_save_top")
            head = st.columns(widths, vertical_alignment="center", gap="xsmall")
            for col, (name, _, _) in zip(head, col_spec):
                col.markdown(f"**{name}**")
            chosen: dict[int, str] = {}
            for r in rows:
                rid = int(r["id"])
                cur = r.get("status") or "受理"
                if cur not in STATUSES:
                    cur = "受理"
                line = st.columns(widths, vertical_alignment="center", gap="xsmall")
                for col, (_, _, field) in zip(line, col_spec):
                    if field is None:
                        chosen[rid] = col.selectbox(
                            f"#{rid} 状态",
                            STATUSES,
                            index=STATUSES.index(cur),
                            label_visibility="collapsed",
                            key=f"ledger_grid_status_{rid}_{grid_rev}",
                        )
                    else:
                        col.write(_cell(r, field))
            save_bottom = st.form_submit_button("保存状态", key="ledger_save_bottom")
            if save_top or save_bottom:
                saved, skipped = [], []
                for r in rows:
                    rid = int(r["id"])
                    cur = r.get("status") or "受理"
                    new = chosen.get(rid) or cur
                    if new == cur or new not in STATUSES:
                        continue
                    if new == "已对账":
                        if not _has_actual_days(r.get("actual_days")):
                            skipped.append(rid)
                            continue
                        row = ledger.get(rid) or {}
                        ledger.reconcile(rid, float(r["actual_days"]), bool(row.get("ai_assisted")), float(row.get("ai_share") or 0), row.get("close_note") or "")
                    else:
                        ledger.set_status(rid, new)
                    saved.append(rid)
                if skipped:
                    st.session_state["ledger_status_skipped"] = skipped
                if saved:
                    _refresh_ledger_grid()
                    st.toast(f"已保存 {len(saved)} 条状态")
                    st.rerun()
                elif skipped:
                    st.rerun()
                else:
                    st.info("状态没有变化。")
        export = pd.DataFrame(ledger.export_rows())
        st.download_button("导出审计台账 .csv", export.to_csv(index=False).encode("utf-8-sig"), file_name="需知_需求一本账.csv", mime="text/csv")
    else:
        st.caption("还没有分析记录。开工后会记在这里。")
    with st.expander("⏰ 催办摘要（附加功能：待确认超期 / 已交付未对账 / 本周受理；默认不推送）", expanded=False):
        from xuzhi import reminders

        _dg = reminders.build_digest(ledger)
        st.code(reminders.render_digest(_dg), language=None)
        _pe, _why = reminders.push_enabled()
        st.caption(("推送已开启（企微群机器人 webhook）。" if _pe else f"推送关闭：{_why}。")
                   + " 拉取方式：企微里发 /摘要、命令行 `python -m xuzhi.reminders --print`、接口 GET /api/v1/digest。内容只有数量、标题、天数。")

    st.markdown("#### 模型调用审计")
    _au = audit.stats()
    st.caption("每一次大模型调用都落一行：用途、渠道、网关主机、模型、耗时、出站提示词里的脱敏标签数、检出的明文敏感项数（应为 0）、成败。不记提示词与回复正文。")
    if not _au["calls"]:
        st.info("还没有模型调用记录（规则引擎不调模型）。选用模型并填 Key 开工或润色后，这里会出现记录。")
    else:
        a1, a2, a3, a4, a5 = st.columns(5)
        a1.metric("调用次数", f"{_au['calls']}", help="成功 " + str(_au["ok"]) + " / 失败 " + str(_au["failed"]))
        a2.metric("平均耗时", f"{_au['avg_ms'] / 1000:.1f} s")
        a3.metric("出站脱敏标签", f"{_au['redacted_tags']} 个", help="提示词里 <手机_1> 这类隐盾标签的总数——证明进模型前过了隐盾")
        a4.metric("出站明文敏感项", f"{_au['plain_hits']} 处", delta=None if not _au["plain_hits"] else "需排查", delta_color="inverse",
                  help="出站提示词里仍能被隐盾规则命中的手机 / 账号 / 证件 / 内网地址 / 密钥；应为 0")
        a5.metric("网关 / 模型", f"{len(_au['hosts'])} / {len(_au['models'])}", help="网关：" + "、".join(_au["hosts"]) + "；模型：" + "、".join(_au["models"]))
        _arows = audit.recent(30)
        st.dataframe(pd.DataFrame(_arows).rename(columns={"id": "编号", "ts": "时间", "purpose": "用途", "channel": "渠道", "backend": "后端", "host": "网关主机",
                                                          "model": "模型", "duration_ms": "耗时(ms)", "prompt_chars": "提示词字数", "completion_chars": "回复字数",
                                                          "redacted_tags": "脱敏标签", "plain_hits": "明文敏感", "ok": "成功", "error": "错误", "req_id": "需求编号"}),
                     hide_index=True, width="stretch")
        _aexp = pd.DataFrame(audit.export_rows())
        st.download_button("导出模型调用审计 .csv", _aexp.to_csv(index=False).encode("utf-8-sig"), file_name="需知_模型调用审计.csv", mime="text/csv")
    st.markdown('<div class="xz-foot">一本账 = 审计留痕 + 飞轮数据：谁提、AI 说了什么、业务怎么答、估了多少、实际多少；对账回写后估算越来越准。看板全部由台账实时算出，无人工填报。</div>', unsafe_allow_html=True)


def build_llm(mode: str, model: str, api_base: str, api_key: str, backend: str = "openai", extra: dict | None = None) -> LLM:
    """按页面选择构造客户端；失败时退回 mock，避免整页崩掉。"""
    try:
        return LLM(
            mode=mode,
            model=model if mode == "api" else "",
            api_base=api_base,
            api_key=api_key,
            backend=backend,
            extra=extra,
            audit=get_audit(),
            channel="Web",
        )
    except Exception as e:
        st.warning(f"模型初始化失败，已回退规则引擎：{e}")
        return LLM(mode="mock")


def _preset_row(p: tuple) -> tuple[str, str, str, str, str]:
    if len(p) >= 5:
        return p[0], p[1], p[2], p[3], p[4]
    label = p[0]
    mode = p[1] if len(p) > 1 else "mock"
    model = p[2] if len(p) > 2 else ""
    return label, mode, "legacy", "", model


def _render_model_preset() -> tuple[str, str, str, str, str]:
    """选用模型（不含 Key）。页面不展示 mock；未填 Key 时开工自动回退规则。"""
    all_presets = [_preset_row(tuple(p)) for p in getattr(config, "LLM_PRESETS", [])]
    api_presets = [p for p in all_presets if p[1] == "api"]
    if not api_presets:
        st.warning("未配置可用模型预设，开工将只跑规则引擎。")
        return "规则引擎", "mock", "mock", "", ""
    labels = [p[0] for p in api_presets]
    prev = st.session_state.get("model_preset")
    if prev not in labels:
        env_pick = next(
            (
                p[0]
                for p in api_presets
                if config.LLM_API_BASE and p[3] == config.LLM_API_BASE
            ),
            None,
        )
        st.session_state.model_preset = env_pick or labels[0]
    preset_label = st.selectbox("选用模型", labels, key="model_preset")
    return next(p for p in api_presets if p[0] == preset_label)


def _render_live_model_pick(
    *,
    provider_id: str,
    api_base: str,
    api_key: str,
    fetch_fn,
    filter_fn,
    sentinel: str,
    select_label: str,
    search_help: str,
) -> str:
    """填 Key 后实时拉 /models，搜索过滤；目录为空时可手填。"""
    f1, f2 = st.columns([3, 1])
    with f1:
        q = st.text_input(
            "搜索模型",
            key=f"{provider_id}_model_filter",
            placeholder="在实时目录中过滤",
            help=search_help,
        )
    with f2:
        st.write("")
        st.write("")
        refresh = st.button("重新拉取", key=f"{provider_id}_refresh_models")

    cache_key = (provider_id, api_base, api_key)
    cat_k, note_k, ck_k, pick_k = (
        f"{provider_id}_models_catalog",
        f"{provider_id}_models_note",
        f"{provider_id}_models_cache_key",
        f"{provider_id}_model_pick",
    )
    force = refresh or st.session_state.pop(f"{provider_id}_models_refresh", False)
    if force or st.session_state.get(ck_k) != cache_key or cat_k not in st.session_state:
        with st.spinner("正在拉取实时模型目录…"):
            catalog, note = fetch_fn(api_base, api_key)
        st.session_state[cat_k] = catalog
        st.session_state[note_k] = note
        st.session_state[ck_k] = cache_key
    catalog = list(st.session_state.get(cat_k) or [])
    note = st.session_state.get(note_k) or ""
    filtered = filter_fn(catalog, q)
    if not catalog:
        st.warning(note or "实时目录为空。")
        return st.text_input(
            "模型 ID（目录为空时手填）",
            key=f"{provider_id}_model_custom_empty",
            placeholder="仅当接口暂不可用时手填",
        ).strip()
    label_map = dict(filtered)
    options = [mid for mid, _ in filtered]
    if not options:
        st.info(f"实时目录里没有匹配「{q.strip()}」的项。清空搜索或点「重新拉取」。")
        options = [sentinel]
        label_map[sentinel] = "（无匹配）可手填模型 ID"
    else:
        options = options + [sentinel]
        label_map[sentinel] = "手填其它模型 ID"
    prev = st.session_state.get(pick_k)
    if prev in options:
        idx = options.index(prev)
    elif config.LLM_MODEL in options:
        idx = options.index(config.LLM_MODEL)
    else:
        idx = 0
    chosen = st.selectbox(
        select_label,
        options,
        index=min(idx, max(0, len(options) - 1)),
        key=pick_k,
        format_func=lambda mid: label_map.get(mid, mid),
    )
    if note:
        st.caption(note + f"　·　过滤后 {len(filtered)}/{len(catalog)}")
    if chosen == sentinel:
        return st.text_input(
            "模型 ID",
            key=f"{provider_id}_model_custom",
            placeholder="与网关 /models 返回的 id 一致",
        ).strip()
    return chosen


def _render_model_secrets(
    mode_sel: str, provider_id: str, api_base_sel: str, model_sel: str
) -> tuple[str, str, str, str, dict[str, str]]:
    """Key / 自定义网关 / Qoder。须放在 form 外，填 PAT 才能立刻拉模型目录。"""
    extra: dict[str, str] = {}
    backend_sel = "openai"
    api_key_sel = ""
    if mode_sel != "api":
        return api_key_sel, api_base_sel, model_sel, backend_sel, extra

    key_state = f"api_key_input_{provider_id}"
    if key_state not in st.session_state and config.LLM_API_KEY and api_base_sel and api_base_sel == config.LLM_API_BASE:
        st.session_state[key_state] = config.LLM_API_KEY
    api_key_sel = st.text_input(
        f"API Key（{provider_id}）",
        type="password",
        key=f"api_key_input_{provider_id}",
        placeholder="Qoder 请填 PAT（pt-…）；其它网关填 sk-…",
        help="不会写入仓库。填 Key 后会向网关 GET /models 拉取当前可用模型，不使用本地写死列表。",
    ).strip()
    st.session_state.api_keys[provider_id] = api_key_sel

    live_compat = getattr(config, "LIVE_OPENAI_PROVIDERS", {}) or {}
    live_openai = provider_id in live_compat
    live_qoder = provider_id in ("qoder-cloud", "qoder-cloud-intl")
    provider_label = live_compat.get(provider_id, provider_id)

    if provider_id == "custom" or model_sel == "__custom__":
        c_base, c_model = st.columns(2)
        if provider_id == "custom":
            default_base = "https://api.siliconflow.cn/v1"
            api_base_sel = c_base.text_input(
                "API Base URL",
                key="custom_api_base_custom",
                placeholder=default_base,
                help="须为 OpenAI 兼容的 /v1 地址。",
            ).strip() or (api_base_sel or default_base)
        else:
            c_base.caption(f"网关：`{api_base_sel}`")
        if model_sel == "__custom__" or provider_id == "custom":
            model_sel = c_model.text_input(
                "模型 ID",
                key=f"custom_model_{provider_id}",
                placeholder="可先填 Key，在上方网关用 /models 核对 id",
            ).strip()
    elif not live_openai and not live_qoder:
        st.caption(f"网关：`{api_base_sel}`　·　模型：`{model_sel}`")

    if live_openai:
        from xuzhi.llm import list_openai_models
        from xuzhi.qoder_cloud import CUSTOM_MODEL_SENTINEL, filter_model_catalog

        # Fable 等网关地址可能随环境变化：允许改 Base，仍实时拉 /models
        if provider_id == "fable":
            base_key = f"live_api_base_{provider_id}"
            if base_key not in st.session_state:
                st.session_state[base_key] = api_base_sel or ""
            api_base_sel = st.text_input(
                "API Base URL（OpenAI 兼容）",
                key=base_key,
                placeholder="https://your-fable-host/v1",
                help="须含 /v1；填 Key 后请求 GET /models。可用环境变量 FABLE_API_BASE 作默认。",
            ).strip()
        else:
            st.caption(f"网关：`{api_base_sel}`　·　模型列表来自实时 GET /models")

        if not api_key_sel:
            st.info(f"填写 API Key 后将实时拉取{provider_label}当前可用模型（不使用本地写死名单）。")
            model_sel = ""
        elif not (api_base_sel or "").strip():
            st.warning("请先填写 API Base URL。")
            model_sel = ""
        else:
            model_sel = _render_live_model_pick(
                provider_id=provider_id,
                api_base=api_base_sel,
                api_key=api_key_sel,
                fetch_fn=list_openai_models,
                filter_fn=filter_model_catalog,
                sentinel=CUSTOM_MODEL_SENTINEL,
                select_label=f"{provider_label}模型（实时）",
                search_help="列表来自 GET /v1/models；下架后刷新即消失。",
            )

    if live_qoder:
        backend_sel = "qoder-cloud"
        from xuzhi.qoder_cloud import (
            CUSTOM_MODEL_SENTINEL,
            filter_model_catalog,
            list_qoder_models,
        )

        if not api_key_sel:
            st.info("填写 PAT 后将实时拉取账号模型目录（不使用本地兜底列表）。")
            model_sel = ""
        else:
            model_sel = _render_live_model_pick(
                provider_id=provider_id,
                api_base=api_base_sel,
                api_key=api_key_sel,
                fetch_fn=list_qoder_models,
                filter_fn=filter_model_catalog,
                sentinel=CUSTOM_MODEL_SENTINEL,
                select_label="Qoder 模型（实时）",
                search_help="列表来自官方 GET /models，与 PyCharm 一致；模型下架后刷新即消失。",
            )
        e1, e2 = st.columns(2)
        extra["environment_id"] = e1.text_input("Environment ID（可空=自动）", key="qoder_env_id", placeholder="env_…").strip()
        extra["agent_id"] = e2.text_input("Agent ID（可空=自动）", key="qoder_agent_id", placeholder="agent_…").strip()
    return api_key_sel, api_base_sel, model_sel, backend_sel, extra


def _commit_dictation(chunk: str, box: str | None = None) -> None:
    """把新口述接在原话框当前内容后面；box 为框里现有文字（含已删空）。"""
    chunk = correct_domain_speech(chunk or "")
    if not chunk:
        return
    prev = box if box is not None else st.session_state.get("raw_composed", "")
    composed = append_dictation(prev, chunk)
    st.session_state.raw_composed = composed
    st.session_state.speech_rev = st.session_state.get("speech_rev", 0) + 1
    st.session_state.sample = "（粘贴自己的）"
    st.session_state.src = "口述"


def _collect_drafts(
    uploads,
    url_entries: list[dict] | None,
    use_sample: bool,
    *,
    ssl_no_verify: bool = False,
) -> tuple[list[Draft], list[RepoBundle], list[str]]:
    drafts: list[Draft] = []
    repos: list[RepoBundle] = []
    errors: list[str] = []
    if uploads:
        for f in uploads:
            try:
                drafts.append(draft_from_bytes(f.name, f.getvalue(), source="upload"))
            except Exception as e:
                errors.append(f"未读到：{getattr(f, 'name', 'upload')} — {e}")
    if url_entries:
        more_d, more_r, err = load_urls(entries=url_entries, git_only=True, ssl_no_verify=ssl_no_verify)
        drafts.extend(more_d)
        repos.extend(more_r)
        errors.extend(err)
    if use_sample and not drafts and not repos:
        sample = config.DATA_DIR / "drafts" / "净值日报_示例底稿.html"
        if sample.exists():
            drafts.append(draft_from_bytes(sample.name, sample.read_bytes(), source="sample"))
    seen: set[str] = set()
    uniq: list[Draft] = []
    for d in drafts:
        key = d.name + "|" + str(len(d.html))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(d)
    return uniq[:12], repos, errors


def _ensure_draft_link_rows() -> list[dict]:
    """会话内维护可增减的链接行。"""
    if "draft_link_rows" not in st.session_state:
        # 兼容旧「整段文本」：拆成多行
        legacy = (st.session_state.get("draft_urls") or "").strip()
        rows = []
        if legacy:
            for i, line in enumerate(legacy.splitlines()):
                line = line.strip()
                if line:
                    rows.append({"id": i})
                    st.session_state[f"draft_link_url_{i}"] = line
        if not rows:
            rows = [{"id": 0}]
        st.session_state.draft_link_rows = rows
        st.session_state.draft_link_next_id = max((r["id"] for r in rows), default=-1) + 1
    return st.session_state.draft_link_rows


def _read_draft_link_entries() -> list[dict]:
    entries = []
    for row in st.session_state.get("draft_link_rows") or []:
        rid = row["id"]
        url = (st.session_state.get(f"draft_link_url_{rid}") or "").strip()
        if not url:
            continue
        entries.append(
            {
                "url": url,
                "token": (st.session_state.get(f"draft_link_tok_{rid}") or "").strip(),
                "username": (st.session_state.get(f"draft_link_user_{rid}") or "").strip(),
                "branch": (st.session_state.get(f"draft_link_branch_{rid}") or "").strip(),
            }
        )
    return entries


def _render_draft_link_rows() -> None:
    rows = _ensure_draft_link_rows()
    st.caption(
        "只填 Git 仓库 http(s) 地址。Token / 分支填在本行旁（不是上方模型 Key）。"
        "分支可不填：不填则拉该仓**默认分支**（GitLab/GitHub 上设置的 default，常见 main / master）。"
    )
    for idx, row in enumerate(list(rows)):
        rid = row["id"]
        c1, c2, c3, c4, c5 = st.columns([2.8, 1.6, 1.3, 1.3, 0.5])
        with c1:
            st.text_input(
                f"Git 仓库 {idx + 1}",
                key=f"draft_link_url_{rid}",
                placeholder="https://主机/组/仓.git（不支持本机 / 内网地址）",
                label_visibility="collapsed" if idx else "visible",
            )
        with c2:
            st.text_input(
                "Token（可选）",
                type="password",
                key=f"draft_link_tok_{rid}",
                placeholder="私有仓 Token",
                label_visibility="collapsed" if idx else "visible",
                help="仅本条仓库使用；不填则匿名拉取。",
            )
        with c3:
            st.text_input(
                "分支（可选）",
                key=f"draft_link_branch_{rid}",
                placeholder="默认分支",
                label_visibility="collapsed" if idx else "visible",
                help="不填则拉取仓库默认分支；也可在 URL 里写 /-/tree/分支名。",
            )
        with c4:
            st.text_input(
                "用户名（可选）",
                key=f"draft_link_user_{rid}",
                placeholder="多数可空",
                label_visibility="collapsed" if idx else "visible",
                help="GitHub 可空（只用 Token）；Gitee 填登录用户名。",
            )
        with c5:
            label = "删" if len(rows) > 1 else " "
            if st.button(label, key=f"draft_link_del_{rid}", disabled=len(rows) <= 1):
                st.session_state.draft_link_rows = [r for r in rows if r["id"] != rid]
                for suffix in ("url", "tok", "user", "branch"):
                    st.session_state.pop(f"draft_link_{suffix}_{rid}", None)
                st.rerun()
    b1, b2 = st.columns([1, 4])
    with b1:
        if st.button("＋添加仓库", key="draft_link_add"):
            nid = int(st.session_state.get("draft_link_next_id", len(rows)))
            st.session_state.draft_link_rows = list(rows) + [{"id": nid}]
            st.session_state.draft_link_next_id = nid + 1
            st.rerun()
    with b2:
        st.caption("网页请左侧上传 HTML；Token 仅会话内使用。")

ledger = get_ledger()
memory = get_memory()
audit = get_audit()
samples = sorted(config.SAMPLES_DIR.glob("*.md"))
stats = ledger.stats()
dash0 = ledger.dashboard()
if "api_keys" not in st.session_state:
    st.session_state.api_keys = {}

preset_label = ""
mode_sel, provider_id, api_base_sel, model_sel = "api", "", "", ""
backend_sel, extra, api_key_sel = "openai", {}, ""

hero_slot = st.container()   # 品牌区等「模型与材料」渲染完再填：状态灯要看这一轮是否真填了 Key
k = st.columns(4)
for col, n, l, c in ((k[0], f"{stats['count']} 份", "已分析需求", "#22D3EE"), (k[1], f"{len(knowledge.probes())} 条", "期货追问知识库", "#A78BFA"),
                     (k[2], f"{len(knowledge.history())} / {len(knowledge.systems())} / {knowledge.data_domains()}", "历史需求 · 系统目录 · 数据域", "#34D399"),
                     (k[3], f"{dash0['saved_days']} 人天" + (f" · 偏差 {dash0['mape']}%" if dash0.get("mape") is not None else ""), "AI 协同累计省时（估）· 已对账估算偏差", "#FBBF24")):
    col.markdown(f'<div class="xz-kpi" style="--c:{c}"><div class="n">{n}</div><div class="l">{l}</div></div>', unsafe_allow_html=True)
st.write("")

st.markdown('<div class="xz-sec"><span class="no">01</span><span class="t">收需求</span>'
            '<span class="s">微信、纪要、邮件、Word，原话扔进来就行，也可口述；不选模型也能开工，规则引擎秒出初稿</span></div>',
            unsafe_allow_html=True)
# 摆放：样例 / 口述 / 原话 / 开工在上，一屏看全；选模型、填 Key、底稿与仓库收进下方折叠栏（投屏时 Key 输入框也不露出）。
# 代码顺序仍是先模型与材料、后输入区：开工处理要用到前者的取值。
intake_area = st.container()
settings_area = st.expander("模型与材料（可选）— 选用模型、填 API Key；上传页面底稿 / 填 Git 仓库", icon=":material/tune:", expanded=False)

with settings_area:
    model_box = st.container(border=True)
    with model_box:
        st.markdown("**选用模型**")
        preset_label, mode_sel, provider_id, api_base_sel, model_sel = _render_model_preset()
        st.caption("开工调用所选模型。未填 API Key 时自动用规则引擎出初稿，可稍后在「问清」补 Key 再润色。")
        api_key_sel, api_base_sel, model_sel, backend_sel, extra = _render_model_secrets(
            mode_sel, provider_id, api_base_sel, model_sel
        )
    draft_box = st.container(border=True)
    with draft_box:
        st.markdown(
            "**页面底稿 / 代码仓库（可选）** — 不传也能开工。"
            "左侧 **上传 HTML** 做页面底稿；右侧只填 **Git 仓库**（Token 可选）。"
            "不要贴浏览器网页地址（多为空壳）。"
            "HTML 建议：页面加载完 F12 → Copy outerHTML → 存成 .html 再上传。"
        )
        d1, d2 = st.columns([1, 1])
        with d1:
            draft_uploads = st.file_uploader(
                "上传 HTML 底稿（可多选）",
                type=["html", "htm"],
                accept_multiple_files=True,
                key="draft_uploads",
            )
            use_sample_draft = st.toggle("没有底稿时用示例「净值日报」底稿演示", value=False, key="use_sample_draft")
        with d2:
            _render_draft_link_rows()
            git_ssl_no_verify = st.toggle(
                "自建 Git 跳过 SSL 校验",
                key="git_ssl_no_verify",
                help="仅在确认自建 GitLab 使用可信的私有证书时勾选；默认校验证书。本机 / 内网地址仍不支持。",
            )

_has_key = mode_sel == "api" and bool((api_key_sel or "").strip())
with hero_slot:
    _skills = "".join(f'<span class="xz-skill" style="--c:{SKILL_COLORS[n]}"><b>{n}</b>{html.escape(d)}</span>'
                      for n, d in [*config.SKILLS, ("隐盾", "进模型前脱敏")])
    _status = (f'<span class="xz-status model"><span class="dot"></span>开工调用模型 · {html.escape(preset_label)}</span>' if _has_key
               else '<span class="xz-status"><span class="dot"></span>规则引擎 · 离线秒出（未填 Key）</span>')
    st.markdown(f"""
<div class="xz-hero">
  <div class="xz-hero-top">
    <div class="xz-brand"><div class="xz-logo">需</div>
      <div><div class="xz-name">{config.PRODUCT_NAME}</div><div class="xz-en">{config.PRODUCT_EN} · 需求分析智能体</div></div></div>
    {_status}
  </div>
  <div class="slogan">{config.PRODUCT_SLOGAN}</div>
  <div class="xz-skills">{_skills}</div>
</div>""", unsafe_allow_html=True)

def _sample_body(choice: str) -> str:
    return next((p.read_text(encoding="utf-8") for p in samples if p.stem == choice), "")


with intake_area:
    pick_l, pick_r = st.columns([2, 3])
    with pick_r:
        st.caption(":material/mic: 口述用 Chrome / Edge，说完点「结束口述」接到原话后面。语音经浏览器厂商识别，**含客户姓名、账号的内容勿口述**（隐盾只管文字）。")
        heard = dictation_bar()
        if isinstance(heard, dict):
            ts = heard.get("ts")
            chunk = (heard.get("text") or "").strip()
            if ts and chunk and ts != st.session_state.get("speech_last_ts"):
                st.session_state.speech_last_ts = ts
                box = (heard.get("box") or "") if heard.get("has_box") else None
                _commit_dictation(chunk, box)
                st.rerun()
    with pick_l:
        choice = st.selectbox("选一个样例（或在下面粘贴）", ["（粘贴自己的）"] + [p.stem for p in samples], key="sample")
    if st.session_state.get("sample_shown") != choice:
        st.session_state["sample_shown"] = choice
        st.session_state["speech_rev"] = int(st.session_state.get("speech_rev", 0) or 0) + 1

    with st.form("xuzhi_go"):
        c1, c2 = st.columns([2, 3])
        with c1:
            src = st.selectbox("来源（可不选）", ["自动识别", "企业微信", "会议纪要", "邮件", "需求单", "口述"], key="src")
            mobile = st.toggle("原型按手机版出", value=False, key="mobile")
            client = st.toggle("原型出客户版（脱敏）", value=False, key="client")
        with c2:
            default_text = st.session_state.get("raw_composed", "") if choice == "（粘贴自己的）" else _sample_body(choice)
            text = st.text_area(
                "原话",
                value=default_text,
                height=190,
                placeholder="例如：净值日报能不能加一列……也可点上方「开始口述」",
                key=f"raw_text_{st.session_state.get('speech_rev', 0)}",
            )
            go = st.form_submit_button("需知，开工", type="primary", icon=":material/bolt:")

llm = None
if go:
    if choice == "（粘贴自己的）":
        st.session_state.raw_composed = text
    elif not text.strip():
        text = _sample_body(choice)
    if not text.strip():
        st.warning("请粘贴原话，或选一个样例后再点开工。")
    else:
        run_mode, run_model, run_base, run_key = mode_sel, model_sel, api_base_sel, api_key_sel
        if run_mode == "api" and (not run_model or run_model in ("__custom__", "__custom_qoder_model__")):
            st.info("未填模型 ID，本次按规则引擎运行。")
            run_mode = "mock"
        if run_mode == "api" and not run_base:
            st.info("未填 API Base URL，本次按规则引擎运行。")
            run_mode = "mock"
        if run_mode == "api" and not run_key:
            st.info("未填 Key → 本次按规则引擎出初稿（可稍后在「问清」补 Key 再润色）。")
            run_mode = "mock"
        llm = build_llm(run_mode, run_model, run_base, run_key, backend=backend_sel, extra=extra)
        do_polish = llm.mode == "api"
        drafts, repos, draft_errs = _collect_drafts(
            draft_uploads,
            _read_draft_link_entries(),
            use_sample_draft,
            ssl_no_verify=bool(git_ssl_no_verify),
        )
        for e in draft_errs:
            if e.startswith("未读到："):
                st.error(e)
            elif e.startswith("无有效内容：") or e.startswith("未纳入："):
                st.warning(e)
            else:
                st.warning(e)
        load_lines = materials_report(drafts, repos)
        st.session_state.materials_report = load_lines
        if drafts or repos or draft_errs:
            with st.expander("📦 材料加载结果（Git / HTML）", expanded=True):
                for line in load_lines:
                    st.markdown(line)
                for h in draft_usability_hints(drafts):
                    st.warning(h)
                if drafts and not visual_drafts(drafts) and not repos:
                    st.info("没有可复刻的静态界面，出样将按需求原话生成。")
        if do_polish and (drafts or repos):
            spin = "模型正在读现有材料并出结果……"
        elif do_polish:
            spin = "模型正在理解需求并出结果……"
        else:
            spin = "规则引擎正在拆需求、估工、出原型……"
        try:
            with st.spinner(spin):
                a = analyze(
                    text, "" if src == "自动识别" else src, llm, answers=None,
                    mobile=mobile, client_view=client, llm_polish=do_polish,
                    drafts=drafts, repos=repos, memory=memory,
                )
        except Exception as e:
            st.error(f"分析失败：{e}。可换模型或去掉材料后重试；若仍失败请把上面这行错误发给技术部。")
            st.stop()
        st.session_state.analysis = a
        st.session_state.drafts = drafts
        st.session_state.repos = repos
        st.session_state.answers = {}
        st.session_state.req_id = ledger.log_analysis(a)
        st.session_state.last_llm = {
            "mode": llm.mode, "model": llm.model, "api_base": run_base, "backend": backend_sel,
            "provider_id": provider_id,
        }
        if do_polish:
            st.session_state.just_polished = a.engine != "规则"
        st.session_state.scroll_to_results = True
        st.rerun()

_last = st.session_state.get("last_llm") or {}
llm = llm or build_llm(
    _last.get("mode", "mock"),
    _last.get("model", ""),
    _last.get("api_base", api_base_sel),
    st.session_state.api_keys.get(_last.get("provider_id", provider_id), ""),
    backend=_last.get("backend", "openai"),
    extra=extra if provider_id in ("qoder-cloud", "qoder-cloud-intl") else {},
)

a = st.session_state.get("analysis")
if not a:
    st.caption("选一个样例或粘贴一段话，点「需知，开工」。所有样例与数据均为虚构。")
    # 首页不铺整本台账（对账表单 + 看板 + 明细 + 审计太长）；要看再打开，开工后也在「一本账」页
    if st.toggle(f"查看一本账 · {stats['count']} 条需求的看板与台账", key="home_ledger"):
        render_ledger(ledger, memory, audit)
    st.stop()

card, est, arch = a.card, a.estimate, a.architecture
_mat = st.session_state.get("materials_report") or materials_report(
    getattr(a, "drafts", None) or st.session_state.get("drafts"),
    getattr(a, "repos", None) or st.session_state.get("repos"),
)
if _mat and (st.session_state.get("drafts") or st.session_state.get("repos")):   # 没传底稿 / 仓库就不占一行
    with st.expander("📦 本次已读材料（Git / HTML）", expanded=bool(st.session_state.get("repos") or getattr(a, "repos", None))):
        for line in _mat:
            st.markdown(line)

tabs = st.tabs([":material/forum: 问清", ":material/description: 写单", ":material/speed: 估量", ":material/account_tree: 定架",
                ":material/web: 出样", ":material/menu_book: 一本账", ":material/shield: 隐盾"])
if st.session_state.pop("scroll_to_results", False):   # 刚开工：把结果滚到眼前，投屏时不用手动往下找
    components.html(
        "<script>setTimeout(function(){var t=window.parent.document.querySelector('[data-testid=\"stTabs\"]');"
        "if(t)t.scrollIntoView({behavior:'smooth',block:'start'});},300);</script>",
        height=0,
    )

# ---------- 问清 ----------
with tabs[0]:
    left, right = st.columns([3, 2])
    with left:
        st.markdown(f"### {card.title}")
        chips = [f"来源 {card.source}", f"提出方 {card.requester or '未注明'}", f"类型 {card.req_type}" + (f" +{'/'.join(card.secondary_types)}" if card.secondary_types else ""),
                 f"触发 {card.trigger}", f"频率 {card.frequency}"] + ([f"期望 {card.deadline}"] if card.deadline else []) + [f"引擎 {a.engine}", f"{a.seconds}s"]
        if getattr(llm, "last_model", ""):
            chips.append(f"实际模型 {llm.last_model}")
        st.markdown("".join(f'<span class="xz-chip">{html.escape(c)}</span>' for c in chips), unsafe_allow_html=True)
        if a.engine == "规则":
            polish_clicked = False
            polish_box = st.container(border=True)
            with polish_box:
                if _last.get("mode") == "api":   # 开工时调了模型但没用上（超时 / 网关不通 / 返回不可用）
                    st.markdown("**模型润色** — 这次开工调模型没成功，已用规则引擎出稿。可在上方「模型与材料」换个模型后点润色。")
                else:
                    st.markdown("**规则引擎初稿**（毫秒级、不调模型）。想让措辞更顺、多补几条追问，可在上方「模型与材料」选模型、填 Key，再点润色。")
            with st.form("xuzhi_polish"):
                polish_clicked = st.form_submit_button(
                    "✨ 模型润色：补追问、润色标题 / 功能点 / 需求单"
                )
            if polish_clicked:
                polish_key = (api_key_sel or st.session_state.api_keys.get(provider_id, "")).strip()
                polish_llm = build_llm(mode_sel, model_sel, api_base_sel, polish_key, backend=backend_sel, extra=extra)
                if polish_llm.mode != "api":
                    st.warning("请先在上方「模型与材料」里选用模型并填写 API Key，再点润色。")
                else:
                    with st.spinner(f"{polish_llm.model or '模型'} 正在读……"):
                        a2 = analyze(
                            a.raw_text, "" if src == "自动识别" else src, polish_llm,
                            answers={k: v for k, v in st.session_state.get("answers", {}).items() if v.strip()},
                            mobile=mobile, client_view=client, llm_polish=True,
                            drafts=getattr(a, "drafts", None) or st.session_state.get("drafts") or [],
                            repos=getattr(a, "repos", None) or st.session_state.get("repos") or [],
                            memory=memory,
                        )
                    st.session_state.analysis = a2
                    st.session_state.last_llm = {
                        "mode": polish_llm.mode, "model": polish_llm.model, "api_base": api_base_sel,
                        "backend": backend_sel, "provider_id": provider_id,
                    }
                    ledger.event(st.session_state.get("req_id", 0), "模型润色", f"{a2.seconds}s")
                    if a2.engine != "规则":
                        st.session_state.just_polished = True
                        st.rerun()
                    st.warning("已调用模型，但润色未改写初稿（仍是规则引擎结果）。按钮还在，可换模型或再试一次。")
        elif a.engine != "规则":
            model_hint = (getattr(llm, "last_model", "") or (_last.get("model") or "")).strip()
            if st.session_state.pop("just_polished", False):
                st.toast("模型润色完成")
                st.success(
                    (f"已用 **{model_hint}** 润色标题、追问和需求单。" if model_hint else "模型润色完成。")
                    + " 问清里不再出润色按钮，避免再调一次；改口径请填业务答复后点重算。"
                )
            else:
                st.caption(
                    ("已用模型润色" + (f"（{model_hint}）" if model_hint else "") + "。")
                    + " 改口径请填业务答复后点重算。"
                )
        st.markdown(f"**一句话理解**：{card.goal}。")
        cc = st.columns(2)
        cc[0].markdown("**功能点（原话清洗）**\n" + "\n".join(f"{i}. {f}" for i, f in enumerate(card.features, 1)))
        cc[1].markdown("**抽到的要素**\n" + "\n".join(f"- {k}：{'、'.join(v) if isinstance(v, list) else v}" for k, v in (
            ("使用者", card.users), ("品种", card.symbols or ["—"]), ("指标", card.indicators or ["—"]), ("对象", card.scope_objects or ["—"]),
            ("数据来源", card.data_sources or ["待确认"]), ("渠道", card.channels or ["未提"]), ("非功能", card.nonfunctional or ["—"]))))
        if card.assumptions:
            st.markdown("**默认假设**：" + "；".join(card.assumptions))
        _n_recalled = sum(1 for q in a.questions if getattr(q, "recalled", ""))
        st.markdown(f"#### 待确认清单（{len(a.questions)} 条，按影响排序；期货问题带 🌾"
                    + (f"；🧠 沿用上次口径 {_n_recalled} 条，本次不再问" if _n_recalled else "") + "）")
        if _n_recalled:
            st.caption("追问记忆：同一提出方上次答过的口径直接沿用，答复框已预填；口径变了直接改再重算，记忆随之更新。")
        answers = st.session_state.get("answers", {})
        for q in a.questions:
            cls = {"高": "hi", "中": "md", "低": "lo"}.get(q.impact, "md")
            fut = '<span class="xz-chip fut">🌾 期货</span>' if q.tag == "期货" else ""
            rec = (f'<span class="xz-chip mem" title="来源：{html.escape(q.recalled)}">🧠 沿用上次口径</span>'
                   if getattr(q, "recalled", "") else "")
            st.markdown(f'<div class="xz-q {cls}"><span class="xz-chip {cls}">影响{q.impact}</span>{fut}{rec}<span class="xz-chip">{q.category}</span> '
                        f'<b>{html.escape(q.question)}</b><div class="why">不问会怎样：{html.escape(q.why)}　｜　默认：{html.escape(q.default)}</div></div>', unsafe_allow_html=True)
            _prefill = answers.get(q.id, q.answer if getattr(q, "recalled", "") else "")
            answers[q.id] = st.text_input("业务答复", value=_prefill, key=f"ans_{q.id}", label_visibility="collapsed",
                                          placeholder="业务答复（留空 = 按默认假设）")
        st.session_state.answers = answers
        if st.button("🔁 按业务答复重算（写单 / 估量 / 定架同步更新）", type="primary"):
            with st.spinner("重算中……"):
                a2 = analyze(
                    a.raw_text, "" if src == "自动识别" else src, llm,
                    answers={k: v for k, v in answers.items() if v.strip()},
                    mobile=mobile, client_view=client,
                    llm_polish=(a.engine != "规则"),
                    drafts=getattr(a, "drafts", None) or st.session_state.get("drafts") or [],
                    repos=getattr(a, "repos", None) or st.session_state.get("repos") or [],
                    memory=memory,
                )
            st.session_state.analysis = a2
            _rid = st.session_state.get("req_id", 0)
            ledger.event(_rid, "业务答复重算", f"{sum(1 for v in answers.values() if v.strip())} 条")
            ledger.update_answers(_rid, sum(1 for q in a2.questions if q.answer.strip()), int(getattr(a2.estimate, "unanswered_high", 0) or 0))
            _learned = memory.learn(a2.card, a2.questions, _rid)
            if _learned:
                ledger.event(_rid, "追问记忆", f"记住 {_learned} 条口径（{a2.card.requester or '未注明'}）")
            st.rerun()
    with right:
        st.markdown("**发给业务的确认消息**（一键复制到企业微信）")
        st.code(a.message, language=None)
        st.download_button("下载确认消息 .txt", a.message, file_name=f"确认消息_{card.title}.txt")
        st.markdown('<div class="xz-foot">清单来自「期货 IT 需求追问知识库」+ 通用检查项；答复后重算，未答复项按默认假设并在需求单里标注。</div>', unsafe_allow_html=True)

# ---------- 写单 ----------
with tabs[1]:
    v = st.radio("版本", ["业务版（一页纸，业务签字）", "技术版（用户故事 / 验收 / 字段 / 接口）"], horizontal=True, key="specv")
    md = a.spec.business_md if v.startswith("业务") else a.spec.tech_md
    with st.container(border=True):
        st.markdown(md)
    st.download_button("下载本版 .md", md, file_name=f"需求单_{'业务版' if v.startswith('业务') else '技术版'}_{card.title}.md")
    st.markdown(f'<div class="xz-foot">技术版每条用户故事下方都带「原话」回链；已答复 {sum(1 for q in a.questions if q.answer.strip())} / {len(a.questions)} 项，其余按默认假设标注。</div>', unsafe_allow_html=True)

# ---------- 估量 ----------
with tabs[2]:
    m = st.columns(5)
    m[0].metric("传统开发", f"{est.mid} 人天", help=f"区间 {est.low} ～ {est.high}；六维加权 + 历史需求类比")
    m[1].metric("AI 协同", f"{est.ai_mid} 人天", delta=f"-{est.saving_pct}%", delta_color="inverse", help=f"区间 {est.ai_low} ～ {est.ai_high}；按任务性质分别乘 AI 协同系数")
    m[2].metric("置信度", est.confidence)
    m[3].metric("未答复的高影响问题", f"{est.unanswered_high} 个")
    m[4].metric("AI 能做的活占", f"{est.who.get('AI 能做', 0):.0%}", help="按传统人天口径统计")
    st.caption(est.confidence_reason)
    st.markdown(f"**建议交付方式**：{est.delivery}")
    st.markdown("#### 双轨拆分：先拆活，再分工")
    who_cls = {"AI 能做": "lo", "AI 做人审": "md", "人必须做": "hi"}
    st.markdown("".join(f'<span class="xz-chip {who_cls[k]}">{k} {v:.0%}</span>' for k, v in est.who.items()), unsafe_allow_html=True)
    tdf = pd.DataFrame([{"任务": t.name, "阶段": t.phase, "分工": t.who, "传统人天": t.trad_days, "AI 协同人天": t.ai_days, "系数": t.coef, "为什么": t.note} for t in est.tasks])
    st.dataframe(tdf, hide_index=True, width="stretch", height=min(60 + 36 * len(tdf), 620))
    l, r = st.columns([1, 1])
    with l:
        st.markdown("**按阶段对比（人天）**")
        pdf = pd.DataFrame({"传统开发": est.phases, "AI 协同": est.ai_phases})
        try:
            st.bar_chart(pdf, height=220, stack=False)
        except TypeError:
            st.bar_chart(pdf, height=220)
        st.markdown(f"基础估算 **{est.base_days} 人天**（六维加权）" + (f"；类比校准 **{est.history_days} 人天**（相似历史需求实际值加权）" if est.history_days else "；无相似历史需求，未校准"))
        st.markdown("**六维复杂度**（1～5）")
        st.dataframe(pd.DataFrame({"维度": list(est.dims.keys()), "分": list(est.dims.values()), "依据": [est.reasons[d] for d in est.dims]}), hide_index=True, width="stretch")
    with r:
        st.markdown("**类比估算：相似历史需求**（当前 30 条均为传统口径；上线后回写「是否用 AI / 参与度」，AI 轨随之校准）")
        if est.similar:
            st.dataframe(pd.DataFrame([{"相似度": f"{s.score:.0%}", "需求": s.title, "类型": s.type, "部门": s.dept, "年份": s.year, "当时估": s.estimate_days, "实际": s.actual_days, "备注": s.note} for s in est.similar]),
                         hide_index=True, width="stretch")
        else:
            st.info("历史需求库里没有相似需求；上线后把实际工时回写，下次就有了。")
    st.markdown('<div class="xz-foot">数字全由程序算：六维加权 → 基础人天；相似历史需求实际人天按相似度加权 → 传统轨；每个任务按「AI 能做 / AI 做人审 / 人必须做」乘系数 → AI 协同轨。涉及资金数字、交易链路、对客内容的任务只按"AI 做人审"折算，验证成本不打折。</div>', unsafe_allow_html=True)

# ---------- 定架 ----------
with tabs[3]:
    st.markdown(f"**复用发现**：{arch.coverage_line}")
    if getattr(arch, "draft_names", None):
        st.caption("本需求已绑定页面底稿：" + "、".join(arch.draft_names))
    dmap = getattr(arch, "data_map", None) or []
    st.markdown(f"#### 数据地图：数据从哪取")
    st.markdown(getattr(arch, "data_map_line", "") or "原话里没抽到明确的数据项。")
    if dmap:
        _st = getattr(arch, "data_map_stats", {}) or {}
        _chips = [("lo", f"可复用 {_st.get('reuse', 0)}"), ("md", f"需申请 / 受控 {_st.get('apply', 0)}"),
                  ("hi", f"需新建 / 时效不符 {_st.get('new', 0)}")] + ([("", f"需计算 {_st['calc']}")] if _st.get("calc") else [])
        st.markdown("".join(f'<span class="xz-chip {c}">{t}</span>' for c, t in _chips), unsafe_allow_html=True)
        st.dataframe(pd.DataFrame([{"数据项": x.need, "来源系统": x.system, "数据域": x.domain, "接口 / 取数方式": x.interface,
                                    "时效": x.freshness, "状态": x.status, "负责": x.owner, "说明": x.note} for x in dmap]),
                     hide_index=True, width="stretch", height=min(60 + 36 * len(dmap), 480))
        st.markdown('<div class="xz-foot">数据地图来自「系统目录」里的数据域与接口（knowledge/system_catalog.json，换成公司真实目录即生效）：'
                    '需求里的每个数据项定位到 系统 · 数据域 · 接口 · 时效；盘中 / 下单前的需求自动检查时效是否够用。</div>', unsafe_allow_html=True)
    l, r = st.columns([1, 1])
    with l:
        st.markdown("**可复用的系统 / 组件**")
        st.dataframe(pd.DataFrame([{"系统": x.system, "命中能力": "、".join(x.matched), "负责": x.owner, "接入方式": x.integration, "成熟度": x.maturity} for x in arch.reuse]),
                     hide_index=True, width="stretch")
        st.markdown("**技术栈建议**")
        st.dataframe(pd.DataFrame([{"层": s[0], "建议": s[1]} for s in arch.stack]), hide_index=True, width="stretch")
    with r:
        st.markdown("**分层架构（自动画）**")
        _iframe(a.layers_html, height=330, bg="transparent")   # 架构图本身是暗色；原型页保持白底，像真实页面
    st.markdown(f"#### 需要 IT 拍板的决策（{len(arch.decisions)} 项）")
    for d in arch.decisions:
        with st.expander(f"{d.id} {d.topic} — 建议：{d.recommended}", expanded=(d.id in ("D0", "D1"))):
            st.markdown(f"**问题**：{d.question}")
            st.dataframe(pd.DataFrame([{"方案": o[0], "优点": o[1], "代价": o[2], "建议": "✅" if o[0].startswith(d.recommended[:4]) else ""} for o in d.options]), hide_index=True, width="stretch")
            st.markdown(f"**理由**：{d.reason}　**影响**：{d.impact}")
    st.download_button("下载 ADR（架构决策记录）.md", arch.adr_md, file_name=f"ADR_{card.title}.md")

# ---------- 出样 ----------
with tabs[4]:
    t1, t2 = st.columns([1, 4])
    mob = t1.toggle("手机版", value=mobile, key="proto_mobile")
    cli = t1.toggle("客户版", value=client, key="proto_client")
    draft_list = getattr(a, "drafts", None) or st.session_state.get("drafts") or []
    source = getattr(a, "prototype_source_html", "") or ""
    if source:
        proto = apply_prototype_view(source, mobile=mob, client_view=cli)
    else:
        proto = render_proto(card, mobile=mob, client_view=cli, drafts=draft_list or None, llm=None)
    t1.download_button("下载原型 .html", proto, file_name=f"原型_{card.title}.html")
    if draft_list or getattr(a, "repos", None):
        t1.markdown(
            '<div class="xz-foot">在原 HTML 上落地改动；Git 源码用于问清/定架。'
            "不相干菜单可点但进不去。</div>",
            unsafe_allow_html=True,
        )
        caps = []
        if draft_list:
            caps.append("页：" + "、".join(d.name for d in draft_list[:4]))
        repos_now = getattr(a, "repos", None) or st.session_state.get("repos") or []
        if repos_now:
            caps.append("仓：" + "、".join(r.name for r in repos_now[:3]))
            nfiles = sum(len(getattr(r, "files", []) or []) for r in repos_now)
            caps.append(f"已读源文件 {nfiles} 个")
        if caps:
            t1.caption(" · ".join(caps))
    else:
        t1.markdown(
            '<div class="xz-foot">未提供底稿/仓库：有 Key 时模型按需求自行理解出样；否则用内置模板。</div>',
            unsafe_allow_html=True,
        )
    with t2:
        _iframe(proto, height=640 if not mob else 760)

# ---------- 一本账 ----------
with tabs[5]:
    render_ledger(ledger, memory, audit, req_id=st.session_state.get("req_id", 0), mid=est.mid)

# ---------- 隐盾 ----------
with tabs[6]:
    st.markdown(f"**进模型前脱敏 {a.redacted} 处**（手机号、账号、证件号、内网地址、密钥、客户姓名 / 机构名 → 语义标签；本地规则，不联网）")
    with st.container(border=True):
        st.text(a.redacted_text)
    st.markdown("**三道线**：① 需求原话先脱敏再进模型；② 不接生产数据库；代码仓库仅在你主动粘贴链接时浅读摘要，不扫内网；③ 每次分析、答复、决策、模型调用留痕，可导出审计。")

    st.markdown("#### 沙箱策略（一页看完：数据去哪了、谁能用、能碰什么）")
    st.caption("所有访问边界写在 `sandbox.yaml` 一个文件里，程序启动时加载；下表每条规则都标了在代码里的执行位置，评审可对照。改文件即改行为。")
    if sandbox.note():
        st.warning(sandbox.note())
    _sum = sandbox.summary()
    st.markdown("".join(f'<span class="xz-chip">{html.escape(k)} · {html.escape(v)}</span>' for k, v in _sum.items()), unsafe_allow_html=True)
    _srows = sandbox.rows()
    st.dataframe(pd.DataFrame(_srows, columns=["边界", "规则", "当前值", "执行位置（代码）"]), hide_index=True, width="stretch", height=min(60 + 36 * len(_srows), 900))
    with st.expander("sandbox.yaml 原文"):
        st.code(sandbox.raw_text(), language="yaml")
