"""需知 · Demo 界面（Streamlit）。启动：streamlit run app.py"""
from __future__ import annotations

import html
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from xuzhi import config, knowledge
from xuzhi.ledger import Ledger
from xuzhi.llm import LLM
from xuzhi.pipeline import analyze
from xuzhi.pipeline.prototype import render as render_proto

B = config.BRAND


def _iframe(html_src: str, height: int) -> None:
    """新旧 Streamlit 兼容：≥1.60 用 st.iframe，否则 components.html。"""
    if hasattr(st, "iframe"):
        st.iframe(html_src, height=height)
    else:
        components.html(html_src, height=height, scrolling=True)

st.set_page_config(page_title=f"{config.PRODUCT_NAME} · 需求分析智能体", page_icon="📋", layout="wide")
st.markdown(f"""
<style>
:root {{ --xz-navy:{B['navy']}; --xz-accent:{B['accent']}; --xz-paper:{B['paper']}; --xz-ink:{B['ink']}; --xz-mist:{B['mist']}; }}
.stApp {{ background: var(--xz-paper); color: var(--xz-ink); }}
.xz-hero {{ background:linear-gradient(135deg, var(--xz-navy) 0%, #1d4aa3 60%, #2a5fc4 100%); color:#fff; border-radius:18px;
           padding:22px 28px; margin-bottom:14px; box-shadow:0 8px 24px rgba(20,54,122,.18); }}
.xz-hero h1 {{ margin:0; font-size:30px; letter-spacing:2px; }}
.xz-hero .slogan {{ font-size:15px; opacity:.92; margin-top:4px; }}
.xz-hero .tag {{ display:inline-block; background:rgba(255,255,255,.14); border:1px solid rgba(255,255,255,.35); border-radius:999px;
                 padding:2px 10px; font-size:12px; margin-right:6px; margin-top:8px; }}
.xz-kpi {{ background:#fff; border:1px solid var(--xz-mist); border-left:5px solid var(--xz-accent); border-radius:12px; padding:12px 16px; }}
.xz-kpi .n {{ font-size:24px; font-weight:700; color:var(--xz-navy); }}
.xz-kpi .l {{ font-size:12px; color:#6b7280; }}
.xz-chip {{ display:inline-block; background:#eef3fb; color:var(--xz-navy); border-radius:999px; padding:1px 9px; font-size:12px; margin:2px 4px 2px 0; }}
.xz-chip.hi {{ background:#fee2e2; color:#b91c1c; }} .xz-chip.md {{ background:#fef3c7; color:#92400e; }} .xz-chip.lo {{ background:#dcfce7; color:#166534; }}
.xz-chip.fut {{ background:#ecfeff; color:#0e7490; border:1px solid #a5f3fc; }}
.xz-q {{ background:#fff; border:1px solid var(--xz-mist); border-left:4px solid var(--xz-accent); border-radius:10px; padding:10px 14px; margin-bottom:8px; }}
.xz-q .why {{ color:#6b7280; font-size:12px; }}
.xz-card {{ background:#fff; border:1px solid var(--xz-mist); border-radius:12px; padding:14px 16px; margin-bottom:10px; }}
.xz-foot {{ color:#6b7280; font-size:12px; margin-top:6px; }}
div[data-testid="stTabs"] button {{ font-size:15px; }}
</style>""", unsafe_allow_html=True)


@st.cache_resource
def get_ledger() -> Ledger:
    return Ledger()


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
        )
    except Exception as e:
        st.warning(f"模型初始化失败，已退回 mock：{e}")
        return LLM(mode="mock")


def _preset_row(p: tuple) -> tuple[str, str, str, str, str]:
    if len(p) >= 5:
        return p[0], p[1], p[2], p[3], p[4]
    label = p[0]
    mode = p[1] if len(p) > 1 else "mock"
    model = p[2] if len(p) > 2 else ""
    return label, mode, "legacy", "", model


def _api_presets() -> list[tuple[str, str, str, str, str]]:
    rows = [_preset_row(tuple(p)) for p in getattr(config, "LLM_PRESETS", [])]
    return [p for p in rows if p[1] == "api"]


def _render_model_preset() -> tuple[str, str, str, str, str]:
    """选用模型（不含 Key）。须在 form 外，换模型才能立刻展开对应网关字段。"""
    presets = _api_presets()
    labels = [p[0] for p in presets]
    if not labels:
        return "mock", "mock", "mock", "", ""
    prev = st.session_state.get("model_preset")
    if prev not in labels:
        st.session_state.model_preset = labels[0]
    preset_label = st.selectbox("选用模型", labels, key="model_preset")
    return next(p for p in presets if p[0] == preset_label)


def _render_model_secrets(
    mode_sel: str, provider_id: str, api_base_sel: str, model_sel: str
) -> tuple[str, str, str, str, dict[str, str]]:
    """Key / 自定义网关 / Qoder。放进 form 里，避免敲 Key 就整页刷新。"""
    extra: dict[str, str] = {}
    backend_sel = "openai"
    api_key_sel = ""
    if mode_sel != "api":
        return api_key_sel, api_base_sel, model_sel, backend_sel, extra

    api_key_sel = st.text_input(
        f"API Key（{provider_id}）",
        type="password",
        key=f"api_key_input_{provider_id}",
        placeholder="Qoder 请填 PAT（pt-…）；其它网关填 sk-…",
        help="不会写入磁盘或仓库。点开工（且已打开自动润色）或点「模型润色」才会调用。",
    ).strip()
    st.session_state.api_keys[provider_id] = api_key_sel

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
                placeholder="例如 deepseek-ai/DeepSeek-V3",
            ).strip()
    elif provider_id not in ("qoder-cloud", "qoder-cloud-intl"):
        st.caption(f"网关：`{api_base_sel}`　·　模型：`{model_sel}`")

    if provider_id in ("qoder-cloud", "qoder-cloud-intl"):
        backend_sel = "qoder-cloud"
        from xuzhi.qoder_cloud import CUSTOM_MODEL_SENTINEL, fallback_models
        catalog = fallback_models(api_base_sel)
        label_map = dict(catalog)
        options = [mid for mid, _ in catalog] + [CUSTOM_MODEL_SENTINEL]
        label_map[CUSTOM_MODEL_SENTINEL] = "自定义模型 ID"
        chosen = st.selectbox(
            "Qoder 模型",
            options,
            index=0,
            key="qoder_model_pick",
            format_func=lambda mid: label_map.get(mid, mid),
        )
        if chosen == CUSTOM_MODEL_SENTINEL:
            model_sel = st.text_input("模型 ID", key="qoder_model_custom", placeholder="例如 qwen3.7-plus / ultimate").strip()
        else:
            model_sel = chosen
        e1, e2 = st.columns(2)
        extra["environment_id"] = e1.text_input("Environment ID（可空=自动）", key="qoder_env_id", placeholder="env_…").strip()
        extra["agent_id"] = e2.text_input("Agent ID（可空=自动）", key="qoder_agent_id", placeholder="agent_…").strip()
    return api_key_sel, api_base_sel, model_sel, backend_sel, extra


ledger = get_ledger()
samples = sorted(config.SAMPLES_DIR.glob("*.md"))
stats = ledger.stats()
if "api_keys" not in st.session_state:
    st.session_state.api_keys = {}

preset_label = "mock · 规则引擎（不调 API）"
mode_sel, provider_id, api_base_sel, model_sel = "mock", "mock", "", ""
backend_sel, extra, api_key_sel = "openai", {}, ""

_auto_hint = bool(st.session_state.get("auto_polish"))
_picked = st.session_state.get("model_preset") or ""
if _auto_hint and _picked:
    _status = f"🟠 已打开自动润色 · 开工将调用 {_picked}"
elif _auto_hint:
    _status = "🟠 已打开自动润色 · 请选用模型"
else:
    _status = "🟡 开工只跑规则，不调模型"
st.markdown(f"""
<div class="xz-hero">
  <h1>📋 {config.PRODUCT_NAME} <span style="font-size:14px;opacity:.8;letter-spacing:0">{config.PRODUCT_EN} · 需求分析智能体</span></h1>
  <div class="slogan">{config.PRODUCT_SLOGAN}</div>
  {''.join(f'<span class="tag">{n} · {d}</span>' for n, d in config.SKILLS)}<span class="tag">隐盾 · 进模型前脱敏</span>
  <span class="tag">{html.escape(_status)}</span>
</div>""", unsafe_allow_html=True)
k = st.columns(4)
for col, n, l in ((k[0], f"{stats['count']} 份", "已分析需求"), (k[1], f"{len(knowledge.probes())} 条", "期货追问知识库"),
                  (k[2], f"{len(knowledge.history())} 条 / {len(knowledge.systems())} 个", "历史需求库 / 系统目录"), (k[3], f"~{stats['minutes_saved'] // 60} 小时", "累计为技术部节省（估）")):
    col.markdown(f'<div class="xz-kpi"><div class="n">{n}</div><div class="l">{l}</div></div>', unsafe_allow_html=True)
st.write("")

opt_l, opt_r = st.columns([2, 3])
with opt_l:
    st.markdown("**① 收需求** — 微信、纪要、邮件、Word，原话扔进来就行")
    auto_polish = st.toggle(
        "开工时让模型自动润色（约 20～30 秒）",
        value=False,
        key="auto_polish",
        help="默认关闭：开工只跑规则。打开后开工时调用模型；成功后问清里不再出「模型润色」，避免调两次。不开则开工后到「问清」再润色。",
    )
with opt_r:
    if auto_polish:
        st.caption("已打开：点开工会调用下方模型。润色成功后，「问清」里不再出润色按钮。")
    else:
        st.caption("开工只跑规则。要调模型：打开左侧开关让开工商润色，或开工后到「问清」点「模型润色」（两处二选一）。")

model_box = None
if auto_polish:
    model_box = st.container(border=True)
    with model_box:
        st.markdown("**选用模型**")
        preset_label, mode_sel, provider_id, api_base_sel, model_sel = _render_model_preset()

with st.form("xuzhi_go"):
    if auto_polish and model_box is not None:
        with model_box:
            api_key_sel, api_base_sel, model_sel, backend_sel, extra = _render_model_secrets(
                mode_sel, provider_id, api_base_sel, model_sel
            )

    c1, c2 = st.columns([2, 3])
    with c1:
        choice = st.selectbox("选一个样例（或在右边粘贴）", ["（粘贴自己的）"] + [p.stem for p in samples], key="sample")
        src = st.selectbox("来源（可不选）", ["自动识别", "企业微信", "会议纪要", "邮件", "需求单", "口述"], key="src")
        mobile = st.toggle("原型按手机版出", value=False, key="mobile")
        client = st.toggle("原型出客户版（脱敏）", value=False, key="client")
    with c2:
        default_text = next((p.read_text(encoding="utf-8") for p in samples if p.stem == choice), "")
        text = st.text_area("原话", value=default_text, height=190, placeholder="例如：净值日报能不能加一列……")
        if auto_polish:
            st.caption("已打开自动润色：点开工会调用上方模型（约 20～30 秒）。选了样例会用该样例原文。")
        else:
            st.caption("开工只跑规则，不调模型。选了样例会用该样例原文；要自己改字请选「粘贴自己的」。")
        go = st.form_submit_button("📋 需知，开工", type="primary")

llm = None
if go:
    if choice != "（粘贴自己的）":
        loaded = next((p.read_text(encoding="utf-8") for p in samples if p.stem == choice), "")
        if loaded:
            text = loaded
    if not text.strip():
        st.warning("请粘贴原话，或选一个样例后再点开工。")
    else:
        run_mode, run_model, run_base, run_key = mode_sel, model_sel, api_base_sel, api_key_sel
        if not auto_polish:
            run_mode = "mock"
        if run_mode == "api" and (not run_model or run_model == "__custom__"):
            st.info("未填模型 ID，本次按 mock 运行。")
            run_mode = "mock"
        if run_mode == "api" and not run_base:
            st.info("未填 API Base URL，本次按 mock 运行。")
            run_mode = "mock"
        if run_mode == "api" and not run_key:
            st.info("未填 Key → 本次按规则引擎运行（仍可稍后点「模型润色」）。")
            run_mode = "mock"
        llm = build_llm(run_mode, run_model, run_base, run_key, backend=backend_sel, extra=extra)
        do_polish = bool(auto_polish) and llm.mode == "api"
        spin = "模型正在润色，约 20～30 秒……" if do_polish else "规则引擎正在拆需求、估工、出原型……"
        with st.spinner(spin):
            a = analyze(text, "" if src == "自动识别" else src, llm, answers=None, mobile=mobile, client_view=client, llm_polish=do_polish)
        st.session_state.analysis = a
        st.session_state.answers = {}
        st.session_state.req_id = ledger.log_analysis(a)
        st.session_state.last_llm = {
            "mode": llm.mode, "model": llm.model, "api_base": run_base, "backend": backend_sel,
            "provider_id": provider_id,
        }
        if do_polish:
            st.session_state.just_polished = a.engine != "规则"
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
    st.info("选一个样例或粘贴一段话，点「需知，开工」。默认只跑规则；要调模型可打开「开工时自动润色」，或开工后在「问清」再润色。所有样例与数据均为虚构。")
    st.stop()

card, est, arch = a.card, a.estimate, a.architecture
tabs = st.tabs(["🗣️ 问清", "📄 写单", "📏 估量", "🏗️ 定架", "🖼️ 出样", "📒 台账", "🛡️ 隐盾"])

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
            if auto_polish:
                polish_clicked = st.button("✨ 模型润色：补追问、润色标题 / 功能点 / 需求单（约 20～30 秒）", key="polish")
            else:
                polish_box = st.container(border=True)
                with polish_box:
                    st.markdown("**模型润色** — 开工已出规则初稿。在这里选用模型后点按钮才调用。")
                    preset_label, mode_sel, provider_id, api_base_sel, model_sel = _render_model_preset()
                with st.form("xuzhi_polish"):
                    with polish_box:
                        api_key_sel, api_base_sel, model_sel, backend_sel, extra = _render_model_secrets(
                            mode_sel, provider_id, api_base_sel, model_sel
                        )
                    polish_clicked = st.form_submit_button(
                        "✨ 模型润色：补追问、润色标题 / 功能点 / 需求单（约 20～30 秒）"
                    )
            if polish_clicked:
                polish_key = (api_key_sel or st.session_state.api_keys.get(provider_id, "")).strip()
                polish_llm = build_llm(mode_sel, model_sel, api_base_sel, polish_key, backend=backend_sel, extra=extra)
                if polish_llm.mode != "api":
                    st.warning("请先选用模型并填写 API Key，再点润色。")
                else:
                    with st.spinner(f"{polish_llm.model or '模型'} 正在读……"):
                        a2 = analyze(
                            a.raw_text, "" if src == "自动识别" else src, polish_llm,
                            answers={k: v for k, v in st.session_state.get("answers", {}).items() if v.strip()},
                            mobile=mobile, client_view=client, llm_polish=True,
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
                    + " 润色按钮已收起，避免再调一次；改口径请填业务答复后点重算。"
                )
            else:
                st.caption(
                    ("已用模型润色" + (f"（{model_hint}）" if model_hint else "") + "，按钮已收起。")
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
        st.markdown(f"#### 待确认清单（{len(a.questions)} 条，按影响排序；期货问题带 🌾）")
        answers = st.session_state.get("answers", {})
        for q in a.questions:
            cls = {"高": "hi", "中": "md", "低": "lo"}[q.impact]
            fut = '<span class="xz-chip fut">🌾 期货</span>' if q.tag == "期货" else ""
            st.markdown(f'<div class="xz-q"><span class="xz-chip {cls}">影响{q.impact}</span>{fut}<span class="xz-chip">{q.category}</span> '
                        f'<b>{html.escape(q.question)}</b><div class="why">不问会怎样：{html.escape(q.why)}　｜　默认：{html.escape(q.default)}</div></div>', unsafe_allow_html=True)
            answers[q.id] = st.text_input("业务答复", value=answers.get(q.id, ""), key=f"ans_{q.id}", label_visibility="collapsed", placeholder="业务答复（留空 = 按默认假设）")
        st.session_state.answers = answers
        if st.button("🔁 按业务答复重算（写单 / 估量 / 定架同步更新）", type="primary"):
            with st.spinner("重算中……"):
                a2 = analyze(
                    a.raw_text, "" if src == "自动识别" else src, llm,
                    answers={k: v for k, v in answers.items() if v.strip()},
                    mobile=mobile, client_view=client,
                    llm_polish=(a.engine != "规则"),
                )
            st.session_state.analysis = a2
            ledger.event(st.session_state.get("req_id", 0), "业务答复重算", f"{sum(1 for v in answers.values() if v.strip())} 条")
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
    l, r = st.columns([1, 1])
    with l:
        st.markdown("**可复用的系统 / 组件**")
        st.dataframe(pd.DataFrame([{"系统": x.system, "命中能力": "、".join(x.matched), "负责": x.owner, "接入方式": x.integration, "成熟度": x.maturity} for x in arch.reuse]),
                     hide_index=True, width="stretch")
        st.markdown("**技术栈建议**")
        st.dataframe(pd.DataFrame([{"层": s[0], "建议": s[1]} for s in arch.stack]), hide_index=True, width="stretch")
    with r:
        st.markdown("**分层架构（自动画）**")
        _iframe(a.layers_html, height=330)
    st.markdown(f"#### 需要 IT 拍板的决策（{len(arch.decisions)} 项）")
    for d in arch.decisions:
        with st.expander(f"{d.id} {d.topic} — 建议：{d.recommended}", expanded=(d.id == "D1")):
            st.markdown(f"**问题**：{d.question}")
            st.dataframe(pd.DataFrame([{"方案": o[0], "优点": o[1], "代价": o[2], "建议": "✅" if o[0].startswith(d.recommended[:4]) else ""} for o in d.options]), hide_index=True, width="stretch")
            st.markdown(f"**理由**：{d.reason}　**影响**：{d.impact}")
    st.download_button("下载 ADR（架构决策记录）.md", arch.adr_md, file_name=f"ADR_{card.title}.md")

# ---------- 出样 ----------
with tabs[4]:
    t1, t2 = st.columns([1, 4])
    mob = t1.toggle("手机版", value=mobile, key="proto_mobile")
    cli = t1.toggle("客户版", value=client, key="proto_client")
    proto = render_proto(card, mobile=mob, client_view=cli)
    t1.download_button("下载原型 .html", proto, file_name=f"原型_{card.title}.html")
    t1.markdown('<div class="xz-foot">原型随需求卡片重画（假数据）。润色会更新功能点标签和引擎标记；对标指数同一查询日全表同一涨跌。</div>', unsafe_allow_html=True)
    with t2:
        _iframe(proto, height=640 if not mob else 760)

# ---------- 台账 ----------
with tabs[5]:
    rid = st.session_state.get("req_id", 0)
    f1, f2, f3 = st.columns([1, 1, 5])
    if f1.button("👍 有用", key="fb_up"):
        ledger.feedback(rid, "整体", 1)
        st.toast("已记录")
    if f2.button("👎 没用", key="fb_down"):
        ledger.feedback(rid, "整体", -1)
        st.toast("已记录")
    rows = ledger.recent(30)
    if rows:
        st.dataframe(pd.DataFrame(rows).rename(columns={"id": "编号", "ts": "时间", "title": "需求", "source": "来源", "req_type": "类型", "requester": "提出方", "engine": "引擎",
                                                        "n_questions": "问题数", "n_answered": "已答复", "mid_days": "传统(人天)", "ai_days": "AI 协同(人天)", "confidence": "置信度", "redacted": "脱敏处"}),
                     hide_index=True, width="stretch")
    st.markdown('<div class="xz-foot">台账 = 审计留痕 + 飞轮数据：谁提、AI 说了什么、业务怎么答、估了多少；上线后回写实际工时，估算越来越准。</div>', unsafe_allow_html=True)

# ---------- 隐盾 ----------
with tabs[6]:
    st.markdown(f"**进模型前脱敏 {a.redacted} 处**（手机号、账号、证件号、内网地址、密钥、客户姓名 / 机构名 → 语义标签；本地规则，不联网）")
    with st.container(border=True):
        st.text(a.redacted_text)
    st.markdown("**三道线**：① 需求原话先脱敏再进模型；② 不接生产数据库、不读代码仓库，复用发现只看系统目录；③ 每次分析、答复、决策留痕，可导出审计。")
