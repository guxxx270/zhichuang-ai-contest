"""需知 · Demo 界面（Streamlit）。启动：streamlit run app.py"""
from __future__ import annotations

import html
import re
from pathlib import Path

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from xuzhi import config, knowledge
from xuzhi.asr import correct_domain_speech
from xuzhi.drafts import (
    Draft,
    RepoBundle,
    draft_from_bytes,
    draft_usability_hints,
    load_urls,
    materials_report,
    summarize,
    visual_drafts,
)
from xuzhi.ledger import Ledger
from xuzhi.llm import LLM
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


def _iframe(html_src: str, height: int) -> None:
    """预览 HTML：必须用 components.html（srcdoc）。勿用 st.iframe——它把参数当 URL，
    原型里一点链接就会跳到需知本页。"""
    components.html(_sandbox_preview_html(html_src or ""), height=height, scrolling=True)

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
iframe[title="xuzhi_dictation"] {{ border: none !important; }}
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
        more_d, more_r, err = load_urls(entries=url_entries, git_only=True)
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
            }
        )
    return entries


def _render_draft_link_rows() -> None:
    rows = _ensure_draft_link_rows()
    st.caption("只填 Git 仓库。每条可单独带 Token（可选）；公有仓留空即可。")
    for idx, row in enumerate(list(rows)):
        rid = row["id"]
        c1, c2, c3, c4 = st.columns([3.2, 2.0, 1.5, 0.6])
        with c1:
            st.text_input(
                f"Git 仓库 {idx + 1}",
                key=f"draft_link_url_{rid}",
                placeholder="https://…/repo.git",
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
                "用户名（可选）",
                key=f"draft_link_user_{rid}",
                placeholder="多数可空",
                label_visibility="collapsed" if idx else "visible",
                help="Gitee / 部分自建仓需要；默认 oauth2 / x-access-token。",
            )
        with c4:
            label = "删" if len(rows) > 1 else " "
            if st.button(label, key=f"draft_link_del_{rid}", disabled=len(rows) <= 1):
                st.session_state.draft_link_rows = [r for r in rows if r["id"] != rid]
                for suffix in ("url", "tok", "user"):
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
samples = sorted(config.SAMPLES_DIR.glob("*.md"))
stats = ledger.stats()
if "api_keys" not in st.session_state:
    st.session_state.api_keys = {}

preset_label = ""
mode_sel, provider_id, api_base_sel, model_sel = "api", "", "", ""
backend_sel, extra, api_key_sel = "openai", {}, ""

_picked = st.session_state.get("model_preset") or ""
if _picked:
    _status = f"🟠 开工将调用模型 · {_picked}（未填 Key 则走规则）"
else:
    _status = "🟠 请选用模型并填 Key；未填 Key 时走规则引擎"
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

st.markdown("**① 收需求** — 微信、纪要、邮件、Word，原话扔进来就行，也可口述；开工调用下方模型，未填 Key 时自动走规则")
model_box = st.container(border=True)
with model_box:
    st.markdown("**选用模型**")
    preset_label, mode_sel, provider_id, api_base_sel, model_sel = _render_model_preset()
    st.caption("开工调用所选模型。未填 API Key 时自动用规则引擎出初稿，可稍后在「问清」补 Key 再润色。")
    api_key_sel, api_base_sel, model_sel, backend_sel, extra = _render_model_secrets(
        mode_sel, provider_id, api_base_sel, model_sel
    )

voice_l, voice_r = st.columns([2, 3])
with voice_l:
    st.caption("口述请用 Edge / Chrome，允许麦克风。说完点「结束口述」，接到原话框里现在剩下的文字后面。")
    st.caption("⚠️ 浏览器口述会把语音送到浏览器厂商的语音服务（Google / Microsoft），**含客户姓名、账号的内容请勿口述**；隐盾只脱敏文字，管不到语音。正式环境应接公司语音服务或关闭口述。")
with voice_r:
    heard = dictation_bar()
    if isinstance(heard, dict):
        ts = heard.get("ts")
        chunk = (heard.get("text") or "").strip()
        if ts and chunk and ts != st.session_state.get("speech_last_ts"):
            st.session_state.speech_last_ts = ts
            box = (heard.get("box") or "") if heard.get("has_box") else None
            _commit_dictation(chunk, box)
            st.rerun()

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

with st.form("xuzhi_go"):
    c1, c2 = st.columns([2, 3])
    with c1:
        choice = st.selectbox("选一个样例（或在右边粘贴）", ["（粘贴自己的）"] + [p.stem for p in samples], key="sample")
        src = st.selectbox("来源（可不选）", ["自动识别", "企业微信", "会议纪要", "邮件", "需求单", "口述"], key="src")
        mobile = st.toggle("原型按手机版出", value=False, key="mobile")
        client = st.toggle("原型出客户版（脱敏）", value=False, key="client")
    with c2:
        default_text = next((p.read_text(encoding="utf-8") for p in samples if p.stem == choice), "")
        if choice == "（粘贴自己的）":
            default_text = st.session_state.get("raw_composed", "") or default_text
        text = st.text_area(
            "原话",
            value=default_text,
            height=190,
            placeholder="例如：净值日报能不能加一列……也可点上方「开始口述」",
            key=f"raw_text_{st.session_state.get('speech_rev', 0)}",
        )
        st.caption("点开工会调用上方模型；未填 Key 则走规则。选了样例会用该样例原文；口述或手改请选「粘贴自己的」。")
        go = st.form_submit_button("📋 需知，开工", type="primary")

llm = None
if go:
    if choice == "（粘贴自己的）":
        st.session_state.raw_composed = text
    if choice != "（粘贴自己的）":
        loaded = next((p.read_text(encoding="utf-8") for p in samples if p.stem == choice), "")
        if loaded:
            text = loaded
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
        with st.spinner(spin):
            a = analyze(
                text, "" if src == "自动识别" else src, llm, answers=None,
                mobile=mobile, client_view=client, llm_polish=do_polish,
                drafts=drafts, repos=repos,
            )
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
    st.info("选一个样例或粘贴一段话，选用模型并填 Key 后点「需知，开工」。未填 Key 时自动走规则引擎。所有样例与数据均为虚构。")
    st.stop()

card, est, arch = a.card, a.estimate, a.architecture
_mat = st.session_state.get("materials_report") or materials_report(
    getattr(a, "drafts", None) or st.session_state.get("drafts"),
    getattr(a, "repos", None) or st.session_state.get("repos"),
)
if _mat:
    with st.expander("📦 本次已读材料（Git / HTML）", expanded=bool(st.session_state.get("repos") or getattr(a, "repos", None))):
        for line in _mat:
            st.markdown(line)

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
            polish_box = st.container(border=True)
            with polish_box:
                st.markdown("**模型润色** — 开工未调到模型（多半是没填 Key 或调用失败）。请在上方补齐 PAT/模型后点润色。")
            with st.form("xuzhi_polish"):
                polish_clicked = st.form_submit_button(
                    "✨ 模型润色：补追问、润色标题 / 功能点 / 需求单"
                )
            if polish_clicked:
                polish_key = (api_key_sel or st.session_state.api_keys.get(provider_id, "")).strip()
                polish_llm = build_llm(mode_sel, model_sel, api_base_sel, polish_key, backend=backend_sel, extra=extra)
                if polish_llm.mode != "api":
                    st.warning("请先在上方选用模型并填写 API Key，再点润色。")
                else:
                    with st.spinner(f"{polish_llm.model or '模型'} 正在读……"):
                        a2 = analyze(
                            a.raw_text, "" if src == "自动识别" else src, polish_llm,
                            answers={k: v for k, v in st.session_state.get("answers", {}).items() if v.strip()},
                            mobile=mobile, client_view=client, llm_polish=True,
                            drafts=getattr(a, "drafts", None) or st.session_state.get("drafts") or [],
                            repos=getattr(a, "repos", None) or st.session_state.get("repos") or [],
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
                    drafts=getattr(a, "drafts", None) or st.session_state.get("drafts") or [],
                    repos=getattr(a, "repos", None) or st.session_state.get("repos") or [],
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
    if getattr(arch, "draft_names", None):
        st.caption("本需求已绑定页面底稿：" + "、".join(arch.draft_names))
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
    st.markdown("**三道线**：① 需求原话先脱敏再进模型；② 不接生产数据库；代码仓库仅在你主动粘贴链接时浅读摘要，不扫内网；③ 每次分析、答复、决策留痕，可导出审计。")
