"""需知 · Demo 界面（Streamlit）。启动：streamlit run app.py"""
from __future__ import annotations

import html
import json
import re
from hashlib import sha256
from uuid import uuid4

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from app_jira import SRC_OPTIONS, calib_pool, jira_text_from_analysis, render_jira_mode
from app_minutes import render_minutes_split
import app_versions

from xuzhi import config, knowledge, sandbox, textutil
from xuzhi.asr import correct_domain_speech
from xuzhi.audit import Audit
from xuzhi.background import start_analysis_job, start_estimate_job
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
from xuzhi.pipeline.estimate import Estimate, estimate
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
.xz-status .dot { width:8px; height:8px; border-radius:50%; background:var(--xz-cyan); box-shadow:0 0 10px var(--xz-cyan); }
.xz-status.model .dot { background:var(--xz-green); box-shadow:0 0 10px var(--xz-green); }
.xz-loading { display:inline-block; width:16px; height:16px; border:2px solid #22304F; border-top-color:#22D3EE;
  border-radius:50%; animation:xzspin .8s linear infinite; margin-right:8px; vertical-align:middle; }
@keyframes xzspin { to { transform:rotate(360deg); } }
.xz-skills { display:flex; flex-wrap:wrap; gap:8px; margin-top:12px; }
.xz-skill { --c:#22D3EE; display:inline-flex; align-items:center; gap:6px; padding:4px 11px; border-radius:999px; font-size:12.5px;
  color:#DCE4F5; background:color-mix(in srgb, var(--c) 12%, transparent); border:1px solid color-mix(in srgb, var(--c) 45%, transparent); }
.xz-skill b { color:var(--c); font-weight:700; }
.xz-dims { width:100%; border-collapse:collapse; margin:4px 0 8px; }
.xz-dims th, .xz-dims td { text-align:left; padding:8px 10px; border-bottom:1px solid #22304F; vertical-align:top; color:#E6ECF8; font-size:14px; white-space:normal; line-height:1.45; }
.xz-dims th { color:#8A97B4; font-weight:600; }
.xz-dims td.num { white-space:nowrap; width:4.5em; }
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
[data-testid="stForm"], .st-key-xuzhi_intake, [data-testid="stExpander"] details { background:rgba(13,20,38,.55); }
[data-testid="stMetric"] { background:var(--xz-panel); border:1px solid var(--xz-line); border-radius:12px; padding:10px 14px; }
[data-testid="stMetricValue"] { color:#9BE7F7; }
iframe[title="xuzhi_dictation"] { border: none !important; }
[data-testid="stHeaderActionElements"] { display: none; }   /* 标题旁的锚点链接图标，演示用不上 */
/* 「用法」切换（零散需求 / Jira 评估）做大一点，像主导航 */
.st-key-app_mode button { font-size:15px; padding:8px 20px; min-height:42px; }
.st-key-app_mode [aria-checked="true"], .st-key-app_mode button[kind*="Active"] { box-shadow:0 0 18px rgba(34,211,238,.25); }
</style>""", unsafe_allow_html=True)


@st.cache_resource
def get_ledger(api_version: str = "versions-v1") -> Ledger:
    """API 版本参与缓存，升级后不复用缺少新接口（模型估时、版本记录）的旧实例。"""
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


def _color_bars(data: dict, value_name: str, height: int = 220, colors: list[str] | None = None, label_angle: int = -30) -> None:
    """一类一色的柱状图：暗底上比同色柱子好认；类别名就在横轴上，不再出图例。"""
    df = pd.DataFrame({"类别": list(data.keys()), value_name: list(data.values())})
    chart = (
        alt.Chart(df)
        .mark_bar(cornerRadiusTopLeft=5, cornerRadiusTopRight=5)
        .encode(
            x=alt.X("类别:N", sort=None, title=None, axis=alt.Axis(labelAngle=label_angle, labelLimit=80)),
            y=alt.Y(f"{value_name}:Q", title=None),
            color=alt.Color("类别:N", sort=None, legend=None, scale=alt.Scale(range=colors or CHART_COLORS)),
            tooltip=["类别", f"{value_name}:Q"],
        )
        .properties(height=height)
    )
    st.altair_chart(chart, width="stretch")


def _radar_dims(dims: dict, reasons: dict | None = None) -> None:
    """六维雷达：每维 1～5，轴从中心 0 画到外圈 5。"""
    import math

    order = [name for name in ("功能点", "数据接入", "界面", "权限", "性能", "合规") if name in dims]
    order += [name for name in dims if name not in order]
    why = reasons or {}
    n = len(order)
    if n < 3:
        return
    cx, cy, rmax = 280, 198, 108

    def at(index: int, score: float, turn: float = 0.0, *, beyond: bool = False) -> tuple[float, float]:
        angle = -math.pi / 2 + index * 2 * math.pi / n + turn
        raw = max(0.0, score)
        if not beyond:
            raw = min(5.0, raw)
        radius = rmax * raw / 5
        return cx + radius * math.cos(angle), cy + radius * math.sin(angle)

    rings = []
    spokes = []
    for level in (1, 2, 3, 4, 5):
        points = " ".join(f"{at(i, level)[0]:.1f},{at(i, level)[1]:.1f}" for i in range(n))
        rings.append(f'<polygon points="{points}" fill="none" stroke="#334155" stroke-width="1"/>')
    for index in range(n):
        x, y = at(index, 5)
        spokes.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" stroke="#334155" stroke-width="1"/>')
    shape = " ".join(f"{at(i, int(dims[name]))[0]:.1f},{at(i, int(dims[name]))[1]:.1f}" for i, name in enumerate(order))
    labels = []
    dots = []
    ticks = []
    for level in (1, 5):
        tx, ty = at(0, level)
        ticks.append(
            f'<text x="{tx - 8:.1f}" y="{ty:.1f}" fill="#E5E7EB" font-size="12" text-anchor="end" dominant-baseline="middle" '
            f'stroke="#070B16" stroke-width="3" paint-order="stroke">{level}</text>'
        )
    for index, name in enumerate(order):
        score = int(dims[name])
        lx, ly = at(index, 6.35, beyond=True)
        labels.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}" fill="#E5E7EB" font-size="13" text-anchor="middle" dominant-baseline="middle">{html.escape(name)} {score}</text>'
        )
        dx, dy = at(index, score)
        title = html.escape(f"{name} {score} 分 {why.get(name, '')}")
        dots.append(f'<g><title>{title}</title><circle cx="{dx:.1f}" cy="{dy:.1f}" r="4" fill="#22D3EE"/></g>')
    svg = (
        '<svg viewBox="0 0 560 400" width="100%" height="380" role="img" aria-label="六维复杂度">'
        + "".join(rings) + "".join(spokes) + "".join(ticks)
        + f'<polygon points="{shape}" fill="rgba(34,211,238,0.28)" stroke="#22D3EE" stroke-width="2"/>'
        + "".join(dots) + "".join(labels)
        + "</svg>"
    )
    st.markdown(svg, unsafe_allow_html=True)


def render_ledger(ledger: Ledger, memory: Memory, audit: Audit, *, req_id: int = 0, mid: float | None = None) -> None:
    """一本账：按编号补任意历史需求的实际人天。刚开工的那条默认选中。"""
    catalog = list(reversed(ledger.export_rows()))
    st.markdown("#### 对账")
    st.caption("选一条需求，回写交付后的实际人天；刚开工的那条已默认选中。")
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
                is_jira = row_now.get("source") == "Jira"
                st.markdown("**对账：交付后回写实际人天**" + ("（记作 Jira 校准样本，下次评估即用）" if is_jira else "（写进历史需求库，估量飞轮）"))
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
                if st.button("对账并回写", key=f"rec_submit_{picked}", icon=":material/task_alt:"):
                    try:
                        res = ledger.reconcile(picked, actual_days, ai_used, ai_share if ai_used else 0.0, rec_note)
                    except ValueError as error:
                        st.warning(str(error))
                    else:
                        _refresh_ledger_grid()
                        if res.get("deviation") is None:
                            st.success(f"#{picked} 已记录实际 {res['actual']} 人天；本条没有有效估算，未计算偏差或校准历史。")
                        else:
                            st.success(f"#{picked} 已对账：估 {res['estimate']} 人天，实际 {res['actual']} 人天，偏差 {res['deviation']:+.0%}；"
                                       + ("已记作 Jira 校准样本，下次评估即用。" if is_jira else f"已写入历史需求库（{res['history_id']}）。"))
                        st.rerun()
            with c3:
                st.markdown("**本条流水**")
                events = ledger.events_of(picked)[-8:]
                if events:
                    for ev in events:
                        event_detail = ev["detail"]
                        if ev["action"] == "估量重算":
                            try:
                                current = json.loads(event_detail).get("current") or {}
                                before_hours = (current.get("before") or {}).get("mid")
                                after_hours = (current.get("after") or {}).get("mid")
                                if current.get("status") == "ready" and before_hours is not None and after_hours is not None:
                                    event_detail = f"已更新：以前 {before_hours:g} 小时，现在 {after_hours:g} 小时（参考值）"
                                else:
                                    event_detail = "模型估时未成功，当前未估时" if current.get("status") == "failed" else "当前未估时"
                            except (ValueError, TypeError, AttributeError):
                                event_detail = "估量结果已更新"
                        st.caption(f"{ev['ts'][5:16]}　{ev['action']}　{event_detail}")
                else:
                    st.caption("还没有流水。")
            app_versions.render_version_history(ledger, picked)   # 版本记录：任选两版对比，下载变更说明

    st.markdown("#### 需求经营看板")
    dash = ledger.dashboard()
    if not dash["count"]:
        st.info("台账里还没有需求。")
    else:
        _mem = memory.stats()
        k1, k2, k3, k4, k5, k6, k7 = st.columns(7)
        k1.metric("需求数", f"{dash['count']}", help=f"来自 {dash['depts']} 个部门")
        k2.metric("平均追问", f"{dash['avg_questions']} 条", help=f"业务答复率 {dash['answer_rate']:.0%}；只算零散需求（Jira 工单不追问）")
        k7.metric("记忆省问", f"{_mem['hits']} 条", help=f"追问记忆：记住 {_mem['requesters']} 个提出方的 {_mem['entries']} 条口径；命中即不再问")
        k3.metric("平均澄清轮次", f"{dash['avg_rounds']}", help="开工一轮 + 每次按业务答复重算算一轮；人工通常三四轮")
        saving_value = f"{dash['saved_days']} 人天" if dash.get("saved_days") is not None else "暂无模型统计"
        saving_delta = f"{-dash['saving_pct']:+g}%" if dash.get("saving_pct") is not None else None
        k4.metric("全部台账预计省时", saving_value, delta=saving_delta, delta_color="inverse",
                  help="仅汇总有有效双轨估算的记录；包含测试及重复录入，不是本条估时，也不是已实现省时。本条工作量见「估量」。")
        k5.metric("估算偏差", "—" if dash["mape"] is None else f"{dash['mape']}%",
                  help=("已对账 " + str(dash["closed"]) + " 条；平均绝对偏差（估 vs 实际；用了 AI 协同的比 AI 轨估算）"
                        + "".join(f"，{n} {v}%" for n, v in (("传统轨", dash["mape_trad"]), ("AI 轨", dash["mape_ai"])) if v is not None))
                  if dash["closed"] else "还没有对账记录：交付后在上方回写实际人天")
        k6.metric("出稿用时", ("<1 s" if dash["avg_seconds"] < 1 else f"{dash['avg_seconds']} s"), help=f"需求分析用时；单独重新估时不计入原出稿时间。👍 {dash['thumbs_up']} / 👎 {dash['thumbs_down']}")
        st.caption(f"有效模型估算 {dash.get('estimated_count', '—')} 条，待估算 {dash.get('unestimated_count', '—')} 条，旧版规则记录 {dash.get('legacy_count', '—')} 条（不计入汇总）；预计省时含测试及重复录入，不代表已实现省时。本条工作量见「估量」。")
        if dash["jira_count"]:
            _jt, _ja = dash["jira_coding_trad"], dash["jira_coding_ai"]
            _kinds = " · ".join(f"{k} {v}" for k, v in dash["jira_by_kind"].items())
            st.caption(f":material/fact_check: 其中 Jira 工单 {dash['jira_count']} 条（{_kinds}）：编码阶段 {_jt} → {_ja} 人天"
                       + (f"（AI 编码省 {round(100 * (1 - _ja / _jt))}%）" if _jt else "")
                       + "。下方部门 / 类型分布与平均追问、澄清轮次只算零散需求。")
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
    st.caption("表格可左右滑动；在「状态」列改好后点「保存状态」。改成「已对账」前，先在上方回写实际人天。")
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
            ("传统(人天)", 0.85, "mid_days"), ("AI 协同(人天)", 1.05, "ai_days"), ("AI 编码(人天)", 1.05, "coding_ai"), ("AI 参与度", 0.85, "ai_share"),
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
        st.warning(f"模型初始化失败，模型估时暂不可用；需求初稿、规则模板和历史参考仍可查看：{e}")
        return LLM(mode="mock")


def _cancel_model_job() -> None:
    previous = st.session_state.pop("_model_job", None)
    if previous is not None:
        previous.discard()
    st.session_state.pop("_model_job_meta", None)
    st.session_state.pop("_model_last_status", None)
    st.session_state["_analysis_revision"] = uuid4().hex


def _model_running() -> bool:
    # 未归并完成结果前也保持忙碌，避免快速点击重复提交。
    return st.session_state.get("_model_job") is not None


def _begin_model_job(seed, client, *, kind: str) -> None:
    _cancel_model_job()
    if client.mode != "api":
        return
    if kind == "estimate":
        seed.estimate = Estimate(unanswered_high=seed.estimate.unanswered_high,
                                 references=getattr(seed.estimate, "references", {}) or {})
        ledger.update_estimate(st.session_state.req_id, seed.estimate)
    client.req_id = st.session_state.get("req_id", 0)
    # 每项后台任务使用独立审计连接，结束后由任务释放。
    if isinstance(getattr(client, "audit", None), Audit):
        client.audit = Audit()
        client._job_owned_audit = True
    factory = start_estimate_job if kind == "estimate" else start_analysis_job
    job = factory(seed, client)
    st.session_state["_model_job"] = job
    st.session_state["_model_job_meta"] = {
        "req_id": client.req_id, "revision": st.session_state["_analysis_revision"], "kind": kind,
    }
    st.session_state["analysis"] = seed


def _settle_model_job(ledger: Ledger) -> bool:
    job = st.session_state.get("_model_job")
    if job is None:
        return False
    meta = st.session_state.get("_model_job_meta") or {}
    if (meta.get("req_id") != st.session_state.get("req_id")
            or meta.get("revision") != st.session_state.get("_analysis_revision")):
        _cancel_model_job()
        return True
    snapshot = job.snapshot()
    if snapshot["status"] == "running":
        return False
    st.session_state.pop("_model_job", None)
    st.session_state.pop("_model_job_meta", None)
    if snapshot.get("discarded"):
        return True
    current = st.session_state.get("analysis")
    if current is None:
        return True
    result = snapshot.get("result")
    failed = snapshot["status"] == "failed"
    if result is not None:
        if meta["kind"] == "estimate":
            current.estimate = result
        else:
            current = result
            current.prototype_html = apply_prototype_view(
                current.prototype_source_html, mobile=bool(st.session_state.get("mobile")),
                client_view=bool(st.session_state.get("client")),
            )
        failed = failed or current.estimate.status != "ready"
        st.session_state.analysis = current
        if meta["kind"] == "estimate":
            ledger.update_estimate(meta["req_id"], current.estimate)
            if current.estimate.status == "ready":   # 只有成功的模型估时算一版，失败不记
                ledger.add_version(meta["req_id"], current, "模型估时")
        else:
            ledger.update_analysis(meta["req_id"], current)
    else:
        # 后台异常不能让旧模型数字冒充本轮结果；初稿及参考仍然保留。
        current.estimate = Estimate(status="failed", error=snapshot.get("error") or "模型分析失败，请重试。",
                                    references=getattr(current.estimate, "references", {}) or {})
        ledger.update_estimate(meta["req_id"], current.estimate)
    st.session_state["_model_last_status"] = {
        "status": "failed" if failed else "completed", "elapsed": snapshot["elapsed"],
        "kind": meta["kind"], "error": snapshot.get("error") or current.estimate.error,
    }
    ledger.event(meta["req_id"], "后台模型分析", "失败或部分未成功" if failed else "已完成")
    return True


@st.fragment(run_every=1.0)
def _render_model_progress() -> None:
    if _settle_model_job(ledger):
        st.rerun()
    job = st.session_state.get("_model_job")
    if job is not None:
        progress = job.snapshot()
        box = st.status(f"模型正在分析 · {progress['stage']} · 已等待 {progress['elapsed']:.0f} 秒",
                        state="running", expanded=True)
        box.write("规则初稿、规则模板、历史参考和原型已可查看；你可以先切换下方页签。模型结果完成后会自动更新。")


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
    preset_label = st.selectbox("模型来源", labels, key="model_preset")
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


def _custom_model_binding(api_base: str, api_key: str) -> str:
    """目录只属于当前网关与账号；缓存标识不再复制明文 Key。"""
    return sha256(json.dumps([api_base.strip().rstrip("/"), api_key.strip()]).encode("utf-8")).hexdigest()


def _apply_custom_catalog_selection(fallback_base: str) -> None:
    # 回调在下一轮控件渲染前运行，才能同步手填框而不覆盖用户后续的手动修改。
    current_base = (st.session_state.get("custom_api_base_custom") or "").strip() or fallback_base
    current_key = (st.session_state.get("api_key_input_custom") or "").strip()
    if st.session_state.get("custom_catalog_binding") != _custom_model_binding(current_base, current_key):
        return
    selected = st.session_state.get("custom_catalog_pick")
    if selected in (st.session_state.get("custom_catalog_models") or {}):
        st.session_state["custom_model_custom"] = selected


def _render_custom_model_catalog(api_base: str, api_key: str, *, fallback_base: str) -> None:
    """自定义网关仅在明确点击时读目录；读取本身不改变当前模型。"""
    binding = _custom_model_binding(api_base, api_key)
    if st.session_state.get("custom_catalog_binding") != binding:
        st.session_state["custom_catalog_binding"] = binding
        st.session_state["custom_catalog_models"] = {}
        st.session_state["custom_catalog_note"] = ""
        st.session_state["custom_catalog_pick"] = None
    if st.button("读取可用模型", key="custom_read_models", disabled=not api_key or not api_base,
                 help="使用当前网关和 API Key 读取目录；仅在你选择模型后修改模型 ID。"):
        from xuzhi.llm import list_openai_models

        with st.spinner("正在读取当前网关的模型目录……"):
            try:
                rows, note = list_openai_models(api_base, api_key)
                catalog = {
                    mid.strip(): str(label or mid)
                    for mid, label in rows if isinstance(mid, str) and mid.strip()
                }
            except Exception:
                catalog, note = {}, "读取模型目录失败，请检查网关配置后重试。"
        st.session_state["custom_catalog_models"] = catalog
        st.session_state["custom_catalog_note"] = str(note or ("当前账号的模型目录为空。" if not catalog else "")).replace(api_key, "[已隐藏]")
        st.session_state["custom_catalog_pick"] = None
    catalog = st.session_state.get("custom_catalog_models") or {}
    note = st.session_state.get("custom_catalog_note") or ""
    if catalog:
        selected = st.session_state.get("custom_catalog_pick")
        if selected not in catalog or selected != st.session_state.get("custom_model_custom"):
            st.session_state["custom_catalog_pick"] = None
        st.selectbox("网关可用模型", list(catalog), index=None, key="custom_catalog_pick",
                     placeholder="选择后填入模型 ID", format_func=lambda mid: catalog.get(mid, mid),
                     on_change=_apply_custom_catalog_selection, args=(fallback_base,))
        st.caption((note + "；" if note else "") + "读取目录不会切换模型，选择后才填入上方模型 ID。")
    elif note:
        st.warning(note + " 当前模型 ID 保持不变。")


def _render_model_secrets(
    mode_sel: str, provider_id: str, api_base_sel: str, model_sel: str, key_slot=None,
) -> tuple[str, str, str, str, dict[str, str]]:
    """Key / 自定义网关 / Qoder。须放在 form 外，填 PAT 才能立刻拉模型目录。key_slot：Key 输入框放哪（默认当前位置）。"""
    extra: dict[str, str] = {}
    backend_sel = "openai"
    api_key_sel = ""
    if mode_sel != "api":
        return api_key_sel, api_base_sel, model_sel, backend_sel, extra

    key_state = f"api_key_input_{provider_id}"
    if key_state not in st.session_state and config.LLM_API_KEY and api_base_sel and api_base_sel == config.LLM_API_BASE:
        st.session_state[key_state] = config.LLM_API_KEY
    with (key_slot or st.container()):
        api_key_sel = st.text_input(
            "API Key",
            type="password",
            key=f"api_key_input_{provider_id}",
            placeholder="PAT（pt-…）" if provider_id.startswith("qoder") else "sk-…（不填 = 规则引擎）",
            help=("只在本次会话内使用，不写入仓库。点击「读取可用模型」才请求当前网关的目录。" if provider_id == "custom"
                  else "只在本次会话内使用，不写入仓库。填了会实时拉取网关当前可用的模型。"),
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
                placeholder="手填模型 ID，或点击下方「读取可用模型」" if provider_id == "custom" else "可先填 Key，在上方网关用 /models 核对 id",
            ).strip()
        if provider_id == "custom":
            _render_custom_model_catalog(api_base_sel, api_key_sel, fallback_base=default_base)
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
            st.caption(f":material/lan: 网关 `{api_base_sel}`")

        if not api_key_sel:
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
    st.session_state.setdefault("intake_drafts", {})["（粘贴自己的）"] = composed
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
    """Git 仓库行：地址 / 分支 / Token / 用户名，各只留一个短标签；多于一行才出删除键。"""
    rows = _ensure_draft_link_rows()
    for idx, row in enumerate(list(rows)):
        rid = row["id"]
        vis = "collapsed" if idx else "visible"
        c1, c2, c3, c4, c5 = st.columns([3.2, 1.3, 1.5, 1.3, 0.45], vertical_alignment="bottom")
        c1.text_input("仓库地址", key=f"draft_link_url_{rid}", placeholder="https://github.com/组/仓库", label_visibility=vis)
        c2.text_input("分支", key=f"draft_link_branch_{rid}", placeholder="默认分支", label_visibility=vis)
        c3.text_input("Token", type="password", key=f"draft_link_tok_{rid}", placeholder="私有仓才填", label_visibility=vis)
        c4.text_input("用户名", key=f"draft_link_user_{rid}", placeholder="Gitee 才填", label_visibility=vis)
        if len(rows) > 1 and c5.button(":material/close:", key=f"draft_link_del_{rid}", help="删掉这一行", type="tertiary"):
            st.session_state.draft_link_rows = [r for r in rows if r["id"] != rid]
            for suffix in ("url", "tok", "user", "branch"):
                st.session_state.pop(f"draft_link_{suffix}_{rid}", None)
            st.rerun()
    if st.button("再加一个仓库", key="draft_link_add", icon=":material/add:", type="tertiary"):
        nid = int(st.session_state.get("draft_link_next_id", len(rows)))
        st.session_state.draft_link_rows = list(rows) + [{"id": nid}]
        st.session_state.draft_link_next_id = nid + 1
        st.rerun()
    st.caption("只拉 http(s) 公网或公司代码托管地址，不拉本机 / 内网；Token 只在本次会话使用。")

ledger = get_ledger()
memory = get_memory()
audit = get_audit()
_settle_model_job(ledger)
samples = sorted(config.SAMPLES_DIR.glob("*.md"))
stats = ledger.stats()
dash0 = ledger.dashboard()
if "api_keys" not in st.session_state:
    st.session_state.api_keys = {}

preset_label = ""
mode_sel, provider_id, api_base_sel, model_sel = "api", "", "", ""
backend_sel, extra, api_key_sel = "openai", {}, ""

hero_slot = st.container()   # 品牌区等「模型与参考材料」渲染完再填：状态灯要看这一轮是否真填了 Key


def _render_hero(status_html: str) -> None:
    skills = "".join(f'<span class="xz-skill" style="--c:{SKILL_COLORS[n]}"><b>{n}</b>{html.escape(d)}</span>'
                     for n, d in [*config.SKILLS, ("隐盾", "进模型前脱敏")])
    with hero_slot:
        st.markdown(f"""
<div class="xz-hero">
  <div class="xz-hero-top">
    <div class="xz-brand"><div class="xz-logo">需</div>
      <div><div class="xz-name">{config.PRODUCT_NAME}</div><div class="xz-en">{config.PRODUCT_EN} · 需求分析智能体</div></div></div>
    {status_html}
  </div>
  <div class="slogan">{config.PRODUCT_SLOGAN}</div>
  <div class="xz-skills">{skills}</div>
</div>""", unsafe_allow_html=True)


# 两种用法：零散的话 → 完整需求（问清 / 写单 / 估量 / 定架 / 出样）；已写好的 Jira 工单 → 复杂度、工时、AI 编码工时
MODES = {"free": ":material/edit_note: 零散需求 → 完整需求", "jira": ":material/fact_check: Jira 需求评估"}
app_mode = st.segmented_control("用法", list(MODES), format_func=MODES.get, default="free", required=True,
                                key="app_mode", label_visibility="collapsed")
if app_mode == "jira":
    st.markdown('<div class="xz-sec"><span class="no">01</span><span class="t">Jira 需求评估</span>'
                '<span class="s">粘贴一条工单，或上传 Jira 导出的 CSV / Excel；不调模型，按技术复杂度给出工时与 AI 编码工时</span></div>',
                unsafe_allow_html=True)
    render_jira_mode(ledger, show_ledger=lambda: render_ledger(ledger, memory, audit))
    _render_hero('<span class="xz-status"><span class="dot"></span>规则引擎 · 离线秒出</span>')
    st.stop()

k = st.columns(4)
for col, n, l, c in ((k[0], f"{stats['count']} 份", "已分析需求", "#22D3EE"), (k[1], f"{len(knowledge.probes())} 条", "期货追问知识库", "#A78BFA"),
                     (k[2], f"{len(knowledge.history())} / {len(knowledge.systems())} / {knowledge.data_domains()}", "历史需求 · 系统目录 · 数据域", "#34D399"),
                     (k[3], f"{dash0['saved_days']} 人天" if dash0.get("saved_days") is not None else "暂无模型统计", "全部台账预计省时", "#FBBF24")):
    col.markdown(f'<div class="xz-kpi" style="--c:{c}"><div class="n">{n}</div><div class="l">{l}</div></div>', unsafe_allow_html=True)
k[3].caption("这是台账汇总，不表示模型正在运行。仅汇总有效模型估算，含测试及重复录入；本条进度和结果见下方。")
st.write("")

st.markdown('<div class="xz-sec"><span class="no">01</span><span class="t">收需求</span>'
            '<span class="s">微信、纪要、邮件、Word，原话扔进来就行，也可口述；不选模型也能开工，规则引擎秒出初稿</span></div>',
            unsafe_allow_html=True)
# 摆放：样例 / 口述 / 原话 / 开工在上，一屏看全；选模型、填 Key、底稿与仓库收进下方折叠栏（投屏时 Key 输入框也不露出）。
# 代码顺序仍是先模型与材料、后输入区：开工处理要用到前者的取值。
intake_area = st.container()
settings_area = st.expander("模型与参考材料（可选）", icon=":material/tune:", expanded=False)

MAT_OPTIONS = ["不用", "示例底稿", "上传网页", "Git 仓库"]
draft_uploads, use_sample_draft, git_ssl_no_verify = None, False, False
with settings_area:
    col_model, col_mat = st.columns(2, gap="medium")
    with col_model:
        with st.container(border=True):
            st.markdown("**:material/smart_toy: 模型**")
            st.caption("不填 Key 可整理需求、出原型，并查看规则模板和历史参考；填好后可增加独立模型估时，也可在「估量」单独重估。")
            m1, m2 = st.columns(2)
            with m1:
                preset_label, mode_sel, provider_id, api_base_sel, model_sel = _render_model_preset()
            api_key_sel, api_base_sel, model_sel, backend_sel, extra = _render_model_secrets(
                mode_sel, provider_id, api_base_sel, model_sel, key_slot=m2
            )
    with col_mat:
        with st.container(border=True):
            st.markdown("**:material/upload_file: 参考材料**")
            st.caption("给一张现有页面，出样就在原页上改；给代码仓库，问清、估时和定架会参考已读取的代码摘录。")
            mat_mode = st.segmented_control("参考材料", MAT_OPTIONS, default="不用", required=True, key="mat_mode",
                                            label_visibility="collapsed") or "不用"
            if mat_mode == "示例底稿":
                st.caption(":material/check_circle: 用内置的「净值日报」页面演示：新列直接插进原表，客户版自动去掉内部字段。")
            elif mat_mode == "上传网页":
                draft_uploads = st.file_uploader("上传 HTML（可多选）", type=["html", "htm"], accept_multiple_files=True,
                                                 key="draft_uploads", label_visibility="collapsed")
                st.caption("直接「另存为」的网页常是空壳：页面加载完按 F12，复制 outerHTML，存成 .html 再上传。")
            elif mat_mode == "Git 仓库":
                _render_draft_link_rows()
                git_ssl_no_verify = st.toggle("自建 GitLab 证书不受信时跳过 SSL 校验", key="git_ssl_no_verify")
    use_sample_draft = mat_mode == "示例底稿"

_has_key = mode_sel == "api" and bool((api_key_sel or "").strip())
_render_hero(f'<span class="xz-status model"><span class="dot"></span>模型已配置 · {html.escape(preset_label)}</span>' if _has_key
             else '<span class="xz-status"><span class="dot"></span>规则初稿与参考可用 · 模型未配置</span>')

def _to_jira(analysis) -> None:
    from xuzhi.jira import parse_issue_text
    from xuzhi.pipeline.assess import assess, calibrate

    text = jira_text_from_analysis(analysis)
    st.session_state.app_mode = "jira"
    st.session_state.jira_src = SRC_OPTIONS[0]
    st.session_state.jira_sample = st.session_state.jira_sample_shown = "（粘贴自己的）"
    st.session_state.jira_text = text
    st.session_state.jira_rev = st.session_state.get("jira_rev", 0) + 1
    led = get_ledger()
    a_j = assess(parse_issue_text(text), calibrate(calib_pool(led)))
    st.session_state.jira_single = a_j
    rid = int(st.session_state.get("req_id", 0) or 0)
    led.attach_jira(rid, a_j)   # 挂回零散需求那行账，不另开一行
    st.session_state.jira_single_rid = rid or None


def _sample_body(choice: str) -> str:
    return next((p.read_text(encoding="utf-8") for p in samples if p.stem == choice), "")


def _save_intake_draft(choice: str, text_key: str) -> None:
    """失焦时保存当前原话；切换样例不会覆盖各自尚未提交的修改。"""
    text = st.session_state.get(text_key, "")
    st.session_state.setdefault("intake_drafts", {})[choice] = text
    if choice == "（粘贴自己的）":
        st.session_state.raw_composed = text


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

    # 原话需在失焦时同步到会话；form 会等到开工才提交，切样例时会丢掉未提交文字。
    with st.container(border=True, key="xuzhi_intake"):
        c1, c2 = st.columns([2, 3])
        with c1:
            src = st.selectbox("来源（可不选）", ["自动识别", "企业微信", "会议纪要", "邮件", "需求单", "口述"], key="src")
            mobile = st.toggle("原型按手机版出", value=False, key="mobile")
            client = st.toggle("原型出客户版（脱敏）", value=False, key="client")
        with c2:
            defaults = st.session_state.get("raw_composed", "") if choice == "（粘贴自己的）" else _sample_body(choice)
            default_text = st.session_state.get("intake_drafts", {}).get(choice, defaults)
            text_key = f"raw_text_{st.session_state.get('speech_rev', 0)}"
            text = st.text_area(
                "原话",
                value=default_text,
                height=190,
                placeholder="例如：净值日报能不能加一列……也可点上方「开始口述」",
                key=text_key,
                on_change=_save_intake_draft,
                args=(choice, text_key),
            )
            go = st.button("需知，开工", type="primary", icon=":material/bolt:")
            change_of = app_versions.change_toggle()   # 屏上已有分析时可记为它的新版本（需求变更），0 = 新需求

llm = None
if go:
    _save_intake_draft(choice, text_key)
    if not text.strip():
        st.warning("请粘贴原话，或选一个样例后再点开工。")
    else:
        _cancel_model_job()
        run_mode, run_model, run_base, run_key = mode_sel, model_sel, api_base_sel, api_key_sel
        if run_mode == "api" and (not run_model or run_model in ("__custom__", "__custom_qoder_model__")):
            st.info("未填模型 ID，本次按规则引擎运行。")
            run_mode = "mock"
        if run_mode == "api" and not run_base:
            st.info("未填 API Base URL，本次按规则引擎运行。")
            run_mode = "mock"
        if run_mode == "api" and not run_key:
            st.info("未填 Key → 本次先出需求初稿、规则模板和历史参考；补 Key 后可在「问清」润色，或在「估量」增加模型分析。")
            run_mode = "mock"
        llm = build_llm(run_mode, run_model, run_base, run_key, backend=backend_sel, extra=extra)
        do_polish = llm.mode == "api"
        drafts, repos, draft_errs = _collect_drafts(
            draft_uploads,
            _read_draft_link_entries() if mat_mode == "Git 仓库" else [],
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
        try:
            with st.spinner("正在生成快速初稿、规则模板、历史参考和原型……"):
                a = analyze(
                    text, "" if src == "自动识别" else src, llm,
                    answers=app_versions.carried_answers(st.session_state.get("analysis"), st.session_state.get("answers"))
                    if change_of else None,
                    mobile=mobile, client_view=client, llm_polish=False,
                    drafts=drafts, repos=repos, memory=memory,
                )
        except Exception as e:
            st.error(f"分析失败：{e}。可换模型或去掉材料后重试；若仍失败请把上面这行错误发给技术部。")
            st.stop()
        st.session_state.analysis = a
        st.session_state.drafts = drafts
        st.session_state.repos = repos
        st.session_state.answers = {}
        if change_of:   # 需求变更：沿用编号，同一行更新、记「需求变更」并存一版
            app_versions.apply_change(ledger, change_of, a)
        else:
            st.session_state.req_id = ledger.log_analysis(a)
        st.session_state.last_llm = {
            "mode": llm.mode, "model": llm.model, "api_base": run_base, "backend": backend_sel,
            "provider_id": provider_id,
        }
        if do_polish:
            _begin_model_job(a, llm, kind="analysis")
        st.session_state.scroll_to_results = True
        st.rerun()

_last = st.session_state.get("last_llm") or {}
page_mode = mode_sel
if not api_key_sel.strip() or not api_base_sel.strip() or not model_sel.strip() or model_sel in ("__custom__", "__custom_qoder_model__"):
    page_mode = "mock"
llm = llm or build_llm(
    page_mode, model_sel, api_base_sel, api_key_sel,
    backend=backend_sel, extra=extra,
)

a = st.session_state.get("analysis")
if not a:
    st.caption("选一个样例或粘贴一段话，点「需知，开工」。所有样例与数据均为虚构。")
    # 首页不铺整本台账（对账表单 + 看板 + 明细 + 审计太长）；要看再打开，开工后也在「一本账」页
    if st.toggle(f"查看一本账 · {stats['count']} 条需求的看板与台账", key="home_ledger"):
        render_ledger(ledger, memory, audit)
    st.stop()

card, est, arch = a.card, a.estimate, a.architecture
if _model_running():
    _render_model_progress()
else:
    last_status = st.session_state.get("_model_last_status") or {}
    if last_status.get("status") == "completed":
        st.success(f"模型分析已完成 · 用时 {last_status['elapsed']:.0f} 秒。结果已自动更新到本条需求。")
    elif last_status.get("status") == "failed":
        st.warning("模型分析未完全成功；已保留快速初稿、规则模板和历史参考，可在对应页签重试。")
    else:
        st.info("快速结果已生成 · 尚未调用模型。规则初稿、模板和历史参考均可查看。")
app_versions.render_change_banner(ledger, st.session_state.get("req_id", 0), a)   # 存了新版就一句话说清本次改了什么
_mat = st.session_state.get("materials_report") or materials_report(
    getattr(a, "drafts", None) or st.session_state.get("drafts"),
    getattr(a, "repos", None) or st.session_state.get("repos"),
)
if _mat and (st.session_state.get("drafts") or st.session_state.get("repos")):   # 没传底稿 / 仓库就不占一行
    with st.expander("📦 本次已读材料（Git / HTML）", expanded=bool(st.session_state.get("repos") or getattr(a, "repos", None))):
        for line in _mat:
            st.markdown(line)
# 纪要直通车：一段话里有几条需求时提示拆开；单条需求不显示
render_minutes_split(a, ledger=ledger, memory=memory, source="" if src == "自动识别" else src,
                     busy=_model_running(), on_switch=_cancel_model_job)

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
            with st.container(border=True):
                p1, p2 = st.columns([3.2, 1], vertical_alignment="center")
                if _model_running():
                    p1.caption("模型正在后台分析，当前先展示规则初稿；你可以先查看和填写待确认项。")
                elif (st.session_state.get("_model_last_status") or {}).get("status") == "failed":
                    p1.caption(":material/error: 这次开工调模型没成功，已用规则引擎出稿；可在上方「模型与参考材料」换个模型再润色。")
                else:
                    p1.caption(":material/bolt: 规则引擎初稿（毫秒级、不调模型）。想让措辞更顺、多补几条追问，填好模型 Key 后点右边润色。")
                with p2:
                    with st.form("xuzhi_polish", border=False):
                        polish_clicked = st.form_submit_button("模型润色", icon=":material/auto_awesome:", width="stretch", disabled=_model_running())
            if polish_clicked:
                polish_key = (api_key_sel or st.session_state.api_keys.get(provider_id, "")).strip()
                polish_llm = build_llm(mode_sel, model_sel, api_base_sel, polish_key, backend=backend_sel, extra=extra)
                if polish_llm.mode != "api":
                    st.warning("请先在上方「模型与参考材料」里选用模型并填写 API Key，再点润色。")
                else:
                    a2 = analyze(
                        a.raw_text, "" if src == "自动识别" else src, polish_llm,
                        answers={k: v for k, v in st.session_state.get("answers", {}).items() if v.strip()},
                        mobile=mobile, client_view=client, llm_polish=False,
                        drafts=getattr(a, "drafts", None) or st.session_state.get("drafts") or [],
                        repos=getattr(a, "repos", None) or st.session_state.get("repos") or [], memory=memory,
                    )
                    st.session_state.analysis = a2
                    st.session_state.last_llm = {
                        "mode": polish_llm.mode, "model": polish_llm.model, "api_base": api_base_sel,
                        "backend": backend_sel, "provider_id": provider_id,
                    }
                    _rid = st.session_state.get("req_id", 0)
                    ledger.event(_rid, "模型润色", "后台启动；规则初稿已显示")
                    if _rid:
                        ledger.update_estimate(_rid, a2.estimate)
                    _begin_model_job(a2, polish_llm, kind="analysis")
                    st.rerun()
            if st.session_state.pop("polish_unchanged", False):
                st.warning("模型未改写需求初稿；估时结果已单独更新，可在「估量」查看。")
        elif a.engine != "规则":
            model_hint = (getattr(llm, "last_model", "") or (_last.get("model") or "")).strip()
            if st.session_state.pop("just_polished", False):
                st.toast("模型润色完成")
                st.success(
                    (f"已用 **{model_hint}** 润色标题、追问和需求单。" if model_hint else "模型润色完成。")
                    + " 改口径请填业务答复后点重算。"
                )
            else:
                st.caption(
                    ("已用模型润色" + (f"（{model_hint}）" if model_hint else "") + "。")
                    + " 改口径请填业务答复后点重算。"
                )
        st.markdown(f"**一句话理解**：{card.goal}。")
        cc = st.columns(2)
        cc[0].markdown("**功能点（原话清洗）**\n" + "\n".join(f"{i}. {textutil.markdown_literal(f)}" for i, f in enumerate(card.features, 1)))
        cc[1].markdown("**抽到的要素**\n" + "\n".join(f"- {k}：{'、'.join(v) if isinstance(v, list) else v}" for k, v in (
            ("使用者", card.users), ("品种", card.symbols or ["—"]), ("指标", card.indicators or ["—"]), ("对象", card.scope_objects or ["—"]),
            ("数据来源", card.data_sources or ["待确认"]), ("渠道", card.channels or ["未提"]), ("非功能", card.nonfunctional or ["—"]))))
        if card.assumptions:
            st.markdown("**默认假设**：" + "；".join(card.assumptions))
        _n_recalled = sum(1 for q in a.questions if getattr(q, "recalled", ""))
        st.markdown(f"#### 待确认清单（{len(a.questions)} 条，按影响排序；期货问题带 🌾"
                    + (f"；🧠 沿用上次口径 {_n_recalled} 条，本次不再问" if _n_recalled else "") + "）")
        if _n_recalled:
            st.caption("同一提出方上次答过的口径直接沿用，答复框已预填；口径变了改掉再重算即可。")
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
        if st.button("🔁 按业务答复重算（写单 / 估量 / 定架同步更新）", type="primary", disabled=_model_running()):
            _cancel_model_job()
            with st.spinner("重算中……"):
                a2 = analyze(
                    a.raw_text, "" if src == "自动识别" else src, llm,
                    answers={k: v for k, v in answers.items() if v.strip()},
                    mobile=mobile, client_view=client,
                    llm_polish=False,
                    drafts=getattr(a, "drafts", None) or st.session_state.get("drafts") or [],
                    repos=getattr(a, "repos", None) or st.session_state.get("repos") or [],
                    memory=memory,
                )
            st.session_state.analysis = a2
            st.session_state.last_llm = {
                "mode": llm.mode, "model": llm.model, "api_base": api_base_sel,
                "backend": backend_sel, "provider_id": provider_id,
            }
            _rid = st.session_state.get("req_id", 0)
            ledger.event(_rid, "业务答复重算", f"{sum(1 for v in answers.values() if v.strip())} 条")
            ledger.update_answers(_rid, sum(1 for q in a2.questions if q.answer.strip()), int(getattr(a2.estimate, "unanswered_high", 0) or 0))
            if _rid:
                ledger.update_estimate(_rid, a2.estimate)
                ledger.add_version(_rid, a2, "业务答复重算")
            _learned = memory.learn(a2.card, a2.questions, _rid)
            if _learned:
                ledger.event(_rid, "追问记忆", f"记住 {_learned} 条口径（{a2.card.requester or '未注明'}）")
            if llm.mode == "api":
                _begin_model_job(a2, llm, kind="analysis")
            st.rerun()
    with right:
        st.markdown("**发给业务的确认消息**（一键复制到企业微信）")
        st.code(a.message, language=None)
        st.download_button("下载确认消息 .txt", a.message, file_name=f"确认消息_{card.title}.txt")
        st.markdown('<div class="xz-foot">清单来自期货 IT 需求追问知识库 + 通用检查项；没答的按默认假设写进需求单。</div>', unsafe_allow_html=True)

# ---------- 写单 ----------
with tabs[1]:
    v = st.radio("版本", ["业务版（一页纸，业务签字）", "技术版（用户故事 / 验收 / 字段 / 接口）"], horizontal=True, key="specv")
    md = a.spec.business_md if v.startswith("业务") else a.spec.tech_md
    with st.container(border=True):
        st.markdown(md)
    b1, b2, _bpad = st.columns([1, 1.3, 3])
    b1.download_button("下载本版 .md", md, file_name=f"需求单_{'业务版' if v.startswith('业务') else '技术版'}_{card.title}.md",
                       icon=":material/download:")
    b2.button("转成 Jira 工单去评估", icon=":material/fact_check:", on_click=_to_jira, args=(a,), key="to_jira",
              help="把需求卡与需求单排成一条 Jira 故事，切到「Jira 需求评估」直接出复杂度与工时")
    st.markdown(f'<div class="xz-foot">技术版每条用户故事都回链原话；已答复 {sum(1 for q in a.questions if q.answer.strip())} / {len(a.questions)} 项，其余按默认假设标注。</div>', unsafe_allow_html=True)

# ---------- 估量 ----------
with tabs[2]:
    estimate_method = getattr(est, "method", "")
    estimate_status = getattr(est, "status", "unavailable")
    estimate_ready = estimate_method == "llm" and estimate_status == "ready" and all(
        getattr(est, field, None) is not None
        for field in ("mid", "low", "high", "ai_mid", "ai_low", "ai_high")
    )
    references = getattr(est, "references", {}) or {}
    if not references:
        # 兼容热更新前的会话：只补充展示参考，不改变已有模型结果或台账。
        try:
            from xuzhi.pipeline.estimate_references import build_references
            references = build_references(a.card, a.redacted_text, a.questions)
        except Exception:
            references = {}
    rule_reference = references.get("rule") or {}
    history_reference = references.get("history") or {}
    rule_ready = rule_reference.get("status") == "ready"
    history_items = history_reference.get("items") or []
    history_ready = history_reference.get("status") == "ready" and bool(history_items)

    def reference_hours(value) -> str:
        if isinstance(value, dict):
            low, mid, high = (value.get(key) for key in ("low", "mid", "high"))
            if all(isinstance(number, (int, float)) and not isinstance(number, bool) for number in (low, mid, high)):
                return f"{low:g}～{high:g} 小时（参考 {mid:g}）"
            return "暂无数值"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f"{value:g} 小时"
        return "暂无数值"

    st.markdown("模型分析、规则模板、历史记录分开提供依据，便于核对范围和差异。")
    st.caption("三路结果不自动加权混合；只有成功的模型估时进入模型工时汇总。未配置模型时，仍可查看规则与历史参考。")
    source_model, source_rule, source_history = st.columns(3)
    with source_model, st.container(border=True):
        st.markdown("**独立模型估时**")
        if _model_running():
            st.markdown('<div role="status" aria-live="polite"><span class="xz-loading"></span>模型正在分析</div>', unsafe_allow_html=True)
            st.caption("完成后自动更新；规则模板和历史参考已先展示。")
        elif estimate_ready:
            st.markdown(f"以前 **{est.mid * 8:g} 小时** · 现在 **{est.ai_mid * 8:g} 小时**")
            st.caption("模型按本次材料逐项分析；下方展开区间、理由与证据。")
        else:
            st.markdown("本次未成功" if estimate_status == "failed" else "尚未调用模型")
            st.caption("使用页面选择的模型，需要配置 Key。")
    with source_rule, st.container(border=True):
        st.markdown("**规则模板参考**")
        if rule_ready:
            st.markdown("以前：" + reference_hours(rule_reference.get("before")))
            st.markdown("现在：" + reference_hours(rule_reference.get("after")))
            st.caption(rule_reference.get("label") or "固定规则计算，独立于模型判断。")
        else:
            st.markdown("暂无规则参考")
            st.caption(rule_reference.get("summary") or "当前没有可用的规则模板结果。")
    with source_history, st.container(border=True):
        st.markdown("**历史记录参考**")
        if history_ready:
            st.markdown(f"找到 **{len(history_items)} 条**相关记录")
            st.caption("来源：" + "；".join(dict.fromkeys(item.get("source") or "来源未确认" for item in history_items)))
            st.caption("对照当时估算、实际工时与开发方式；历史范围可能不同。")
        else:
            st.markdown("暂无匹配记录")
            st.caption(history_reference.get("summary") or "尚无可比的历史记录。")

    st.markdown("#### 独立模型分析")
    st.caption("模型根据本条需求和已提供的材料拆分工作，分别判断人工开发与 AI 协同的投入；程序校验并汇总模型的逐项数字。")
    estimate_label = "模型分析中……" if _model_running() else "重新分析估时" if estimate_ready or estimate_status == "failed" else "大模型分析估时"
    if st.button(estimate_label, type="primary", key="estimate_with_model", disabled=_model_running()):
        estimate_llm = build_llm(page_mode, model_sel, api_base_sel, api_key_sel,
                                 backend=backend_sel, extra=extra)
        estimate_llm.req_id = st.session_state.get("req_id", 0)
        if estimate_llm.mode != "api":
            _cancel_model_job()
            next_estimate = estimate(
                a.card, a.redacted_text, a.questions, llm=estimate_llm,
                repos=getattr(a, "repos", None) or [],
                drafts=getattr(a, "drafts", None) or [],
            )
            a.estimate = next_estimate
            st.session_state.analysis = a
            _rid = st.session_state.get("req_id", 0)
            if _rid:
                ledger.update_estimate(_rid, next_estimate)
        else:
            _begin_model_job(a, estimate_llm, kind="estimate")
        st.session_state.last_llm = {
            "mode": estimate_llm.mode, "model": estimate_llm.model, "api_base": api_base_sel,
            "backend": backend_sel, "provider_id": provider_id,
        }
        st.rerun()

    if _model_running():
        st.info("模型正在分析；下方规则模板与历史参考已可查看，模型结果会自动补充。")
    elif not estimate_ready:
        if estimate_method not in ("llm", "unavailable"):
            st.info("当前会话尚无独立模型结果；规则模板已在参考区单独列出，可配置模型后增加分析。")
        elif estimate_status == "failed":
            st.error("本次模型估时未成功。请检查模型设置后重试；规则与历史参考仍独立保留。")
            if getattr(est, "error", ""):
                st.caption(est.error)
        else:
            st.info("尚未调用模型，尚无模型估时。规则模板和历史记录可在下方查看；如需模型分析，请在上方选择模型、填写 Key，再点击「大模型分析估时」。")
        st.caption("未提供代码时可根据需求粗估；估时仅使用本条需求已经读取的材料。")
    else:
        requirements_only = getattr(est, "basis", "requirements_only") != "code_excerpt"
        if requirements_only:
            st.warning("粗估：本次尚未用实现代码核实改动，仅依据需求和补充材料判断；字段复用、影响范围与改动位置仍待确认。")
        else:
            st.info("已参考所提供的代码摘录；估算覆盖范围以实际读取的文件为准，尚未验证运行环境与完整调用链。")
        if getattr(est, "summary", ""):
            st.markdown(est.summary)
        before, after = st.columns(2)
        before.metric("以前（人工开发）", f"{est.low * 8:g}～{est.high * 8:g} 小时")
        before.caption(f"参考投入：{est.mid * 8:g} 小时")
        after.metric("现在（AI协同）", f"{est.ai_low * 8:g}～{est.ai_high * 8:g} 小时")
        after.caption(f"参考投入：{est.ai_mid * 8:g} 小时")
        st.caption("均为投入工时，8 小时 = 1 人天，不是日历工期；AI 协同包含人工审核、数字核对与验收。")
        if est.saving_pct is not None:
            st.caption(f"按参考投入计算，预计人时减少 {est.saving_pct:g}%；两边均为估计，不是实测提效。")
        code_files = getattr(est, "code_files", []) or []
        st.caption(f"估时模型：{getattr(est, 'model', '') or '未记录'} · 材料范围："
                   + (f"需求与 {len(code_files)} 份已读取代码摘录" if code_files else "需求与补充材料（未读取实现代码）"))
        with st.expander("估算前提与材料范围", expanded=requirements_only):
            comparison = getattr(est, "comparison", {}) or {}
            if comparison:
                st.markdown("**本次比较口径**")
                for key in ("before", "after", "unit", "environment"):
                    if comparison.get(key):
                        st.markdown(f"- {textutil.markdown_literal(comparison[key])}")
                st.caption(f"口径版本：{comparison.get('version', '未记录')}")
            assumptions = getattr(est, "assumptions", []) or []
            if assumptions:
                for assumption in assumptions:
                    st.markdown(f"- {textutil.markdown_literal(assumption)}")
            else:
                st.caption("模型未单独列出前提，请结合任务依据核对。")
            if code_files:
                st.markdown("**本次读取的代码文件**")
                st.code("\n".join(code_files), language=None)
            else:
                st.caption("本次未读取实现代码。")
        st.markdown("#### 工作项与估算依据")
        task_rows = []
        for task in est.tasks:
            task_rows.append({
                "工作项": task.name,
                "阶段": task.phase,
                "以前（小时）": f"{task.trad_low_hours:g}～{task.trad_high_hours:g}（参考 {task.trad_days * 8:g}）",
                "现在（小时）": f"{task.ai_low_hours:g}～{task.ai_high_hours:g}（参考 {task.ai_days * 8:g}）",
                "以前的依据": task.before_reason,
                "现在的依据": task.after_reason,
            })
        st.dataframe(pd.DataFrame(task_rows), hide_index=True, width="stretch", height=min(60 + 52 * len(task_rows), 620))
        for index, task in enumerate(est.tasks, 1):
            with st.expander(f"{index}. {task.name} · 查看依据与证据"):
                st.markdown(f"**以前（人工开发）**：{textutil.markdown_literal(task.before_reason)}")
                st.markdown(f"**现在（AI协同）**：{textutil.markdown_literal(task.after_reason)}")
                if task.note:
                    st.caption(task.note)
                evidence = getattr(task, "evidence", []) or []
                if not evidence:
                    st.caption("未提供可回查的证据片段；此项判断需要复核。")
                for item in evidence:
                    location = " · ".join(str(item.get(key, "")) for key in ("source", "file") if item.get(key))
                    st.caption(location or "依据片段")
                    if item.get("quote"):
                        st.code(str(item["quote"]), language=None)
        if est.phases or est.ai_phases:
            st.markdown("**按阶段对比（小时）**")
            phase_frame = pd.DataFrame({
                "以前（人工开发）": {phase: round(days * 8, 2) for phase, days in est.phases.items()},
                "现在（AI协同）": {phase: round(days * 8, 2) for phase, days in est.ai_phases.items()},
            })
            try:
                st.bar_chart(phase_frame, height=220, stack=False)
            except TypeError:
                st.bar_chart(phase_frame, height=220)
        if est.delivery:
            st.markdown(f"**建议交付方式**：{est.delivery}")

    with st.expander("规则模板参考 · 六维复杂度、公式与任务", expanded=True):
        if rule_ready:
            if rule_reference.get("summary"):
                st.markdown(rule_reference["summary"])
            rule_before, rule_after = st.columns(2)
            rule_before.markdown("**以前（规则模板）**\n\n" + reference_hours(rule_reference.get("before")))
            rule_after.markdown("**现在（规则模板）**\n\n" + reference_hours(rule_reference.get("after")))
            dims = rule_reference.get("dims")
            if isinstance(dims, dict) and dims:
                reasons = rule_reference.get("reasons") or {}
                weights = rule_reference.get("weights") or {}
                st.markdown("**六维复杂度**")
                _radar_dims(dims, reasons)
                weight_line = "，".join(
                    f"{name} {weights[name]:g}" for name in ("功能点", "数据接入", "界面", "权限", "性能", "合规") if name in weights
                )
                st.caption("图形只表示 1～5 分，六条轴一样长。工时还要乘权重"
                           + (f"：{weight_line}" if weight_line else "")
                           + "。这些权重是手写示例，没有用实际工时校准。")
                parts = rule_reference.get("hour_parts")
                if isinstance(parts, dict) and parts:
                    st.markdown("**各维加进参考工时（小时）**")
                    _color_bars({str(name): float(value) for name, value in parts.items()}, "小时", label_angle=0)
                    st.caption("底数是 1.5 人天换成的 12 小时。其余每柱是 (分数 − 1) × 权重 × 8，1 分的维是 0。"
                               "加总等于上面「以前（规则模板）」的参考小时。这些是手写权重算出的模板小时，不是实测工时。")
                head = "<tr><th>规则维度</th><th>分值</th>" + ("<th>评分依据</th>" if reasons else "") + ("<th>规则权重</th>" if weights else "") + "</tr>"
                body = []
                for name, score in dims.items():
                    cells = f"<td>{html.escape(str(name))}</td><td class='num'>{html.escape(str(score))}</td>"
                    if reasons:
                        cells += f"<td>{html.escape(str(reasons.get(name, '')))}</td>"
                    if weights:
                        weight = weights.get(name, "")
                        cells += f"<td class='num'>{html.escape(f'{weight:g}' if isinstance(weight, float) else str(weight))}</td>"
                    body.append(f"<tr>{cells}</tr>")
                st.markdown(f"<table class='xz-dims'><thead>{head}</thead><tbody>{''.join(body)}</tbody></table>", unsafe_allow_html=True)
            elif rule_reference.get("method") == "incremental":
                st.markdown("**六维复杂度**")
                st.caption("这条匹配的是已有计算口径小改模板，按固定工作项加总，不打功能点、数据接入、界面、权限、性能、合规这六分。")
            if rule_reference.get("formula"):
                st.markdown("**计算公式**")
                st.code(rule_reference["formula"], language=None)
            rule_tasks = rule_reference.get("tasks") or []
            if rule_tasks:
                st.dataframe(pd.DataFrame([{
                    "模板工作项": task.get("name", ""), "阶段": task.get("phase", ""),
                    "以前（小时）": reference_hours(task.get("before")),
                    "现在（小时）": reference_hours(task.get("after")),
                    "模板系数（现在/以前）": task.get("coefficient"),
                    "规则说明": task.get("note", ""),
                } for task in rule_tasks]), hide_index=True, width="stretch")
            if rule_reference.get("assumptions"):
                st.markdown("**适用前提与局限**")
                for assumption in rule_reference["assumptions"]:
                    st.markdown(f"- {textutil.markdown_literal(assumption)}")
            st.caption("这是规则模板参考，不是模型输出；它不会替换模型结果，也不与模型工时自动加权。")
        else:
            st.info(rule_reference.get("summary") or "当前没有可用规则模板参考；模型结果和历史记录不受影响。")

    with st.expander("历史记录参考 · 当时估算与实际工时", expanded=not estimate_ready):
        if history_reference.get("summary"):
            st.markdown(history_reference["summary"])
        if history_ready:
            st.dataframe(pd.DataFrame([{
                "编号": item.get("id", ""), "历史需求": item.get("title", ""), "类型": item.get("type", ""),
                "相关度": f"{item['score']:.0%}" if isinstance(item.get("score"), (int, float)) else "—",
                "当时估算": reference_hours(item.get("estimated_hours")),
                "实际工时": reference_hours(item.get("actual_hours")),
                "实际开发方式": "AI 协同" if item.get("ai_assisted") is True else "人工开发" if item.get("ai_assisted") is False else "未注明",
                "来源": item.get("source", ""), "备注": item.get("note", ""),
            } for item in history_items]), hide_index=True, width="stretch")
        else:
            st.info("暂无匹配的历史记录；这不影响模型或规则模板各自提供结果。")
        for limitation in history_reference.get("limitations") or []:
            st.caption(limitation)
        st.caption("历史实际工时属于当时的需求范围和开发条件，仅供对照，不直接套入本条需求，也不计入本条模型工时。")

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
        st.markdown('<div class="xz-foot">每个数据项定位到 系统 · 数据域 · 接口 · 时效（来自系统目录，换成公司真实目录即生效）；盘中 / 下单前的需求会自动检查时效够不够。</div>', unsafe_allow_html=True)
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
            '<div class="xz-foot">在原页面上改；不相干的菜单可点但进不去。</div>',
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
            '<div class="xz-foot">没给参考页面：按需求生成原型（橙色表头是新增的列）。</div>',
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
