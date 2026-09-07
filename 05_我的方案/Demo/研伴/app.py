"""研伴 · Demo 界面（Streamlit）。启动：streamlit run app.py"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from yanban import config
from yanban.docs import load_docs, parse_doc, read_text
from yanban.llm import LLM
from yanban.memory import Memory
from yanban.privacy import Redactor
from yanban.profile import load_profile, save_profile
from yanban.skills.morning import ask, build_brief
from yanban.skills.rulebook import RuleBook
from yanban.skills.writer import compliance_check, load_template, weekly_report
from yanban.symbols import SYMBOL_NAMES, name_of

B = config.BRAND
st.set_page_config(page_title=f"{config.PRODUCT_NAME} · 投研个人智能体", page_icon="🌅", layout="wide")

st.markdown(f"""
<style>
:root {{ --yb-blue:{B['blue']}; --yb-gold:{B['gold']}; --yb-paper:{B['paper']}; --yb-ink:{B['ink']}; --yb-mist:{B['mist']}; }}
.stApp {{ background: var(--yb-paper); color: var(--yb-ink); }}
.yb-hero {{ background: linear-gradient(135deg, var(--yb-blue) 0%, #12509f 60%, #1b62b8 100%); color:#fff; border-radius:18px;
           padding:22px 28px; margin-bottom:14px; box-shadow:0 8px 24px rgba(11,61,145,.18); }}
.yb-hero h1 {{ margin:0; font-size:30px; letter-spacing:2px; }}
.yb-hero .slogan {{ font-size:15px; opacity:.92; margin-top:4px; }}
.yb-hero .tag {{ display:inline-block; background:rgba(255,255,255,.14); border:1px solid rgba(255,255,255,.35); border-radius:999px;
                 padding:2px 10px; font-size:12px; margin-right:6px; margin-top:8px; }}
.yb-kpi {{ background:#fff; border:1px solid var(--yb-mist); border-left:5px solid var(--yb-gold); border-radius:12px; padding:12px 16px; }}
.yb-kpi .n {{ font-size:26px; font-weight:700; color:var(--yb-blue); }}
.yb-kpi .l {{ font-size:12px; color:#6b7280; }}
.yb-card {{ background:#fff; border:1px solid var(--yb-mist); border-radius:12px; padding:14px 16px; margin-bottom:10px; }}
.yb-chip {{ display:inline-block; background:#eef3fb; color:var(--yb-blue); border-radius:999px; padding:1px 9px; font-size:12px; margin-right:4px; }}
.yb-div {{ background:#fff7e0; border-left:4px solid var(--yb-gold); padding:8px 12px; border-radius:8px; margin:6px 0; }}
.yb-foot {{ color:#6b7280; font-size:12px; margin-top:6px; }}
div[data-testid="stTabs"] button {{ font-size:15px; }}
</style>
""", unsafe_allow_html=True)


@st.cache_resource
def get_llm() -> LLM:
    return LLM()


def get_mem() -> Memory:
    if "mem" not in st.session_state:
        st.session_state.mem = Memory()
    return st.session_state.mem


llm, mem = get_llm(), get_mem()
if "profile" not in st.session_state:
    st.session_state.profile = load_profile()
profile = st.session_state.profile
if "docs" not in st.session_state:
    st.session_state.docs = load_docs()
    st.session_state.redacted = 0

# ---------- Hero ----------
stats = mem.stats()
st.markdown(f"""
<div class="yb-hero">
  <h1>🌅 {config.PRODUCT_NAME} <span style="font-size:14px;opacity:.8;letter-spacing:0">YanBan · 投研个人智能体</span></h1>
  <div class="slogan">{config.PRODUCT_SLOGAN}</div>
  <span class="tag">会读 · 晨读</span><span class="tag">会查 · 问典</span><span class="tag">会写 · 代笔</span><span class="tag">守得住 · 隐盾</span>
  <span class="tag">{'🟢 已接入 ' + config.LLM_MODEL if llm.mode == 'api' else '🟡 mock 模式（未配置 key，规则引擎兜底）'}</span>
</div>""", unsafe_allow_html=True)
k1, k2, k3, k4 = st.columns(4)
for col, n, l in ((k1, f"{profile.name} · {profile.role}", "我的画像"), (k2, f"{len(st.session_state.docs)} 份", "已读材料"),
                  (k3, f"~{stats['minutes_today']} 分钟", "今日为你节省"), (k4, "不出域", "数据边界 · 本机脱敏")):
    col.markdown(f'<div class="yb-kpi"><div class="n">{n}</div><div class="l">{l}</div></div>', unsafe_allow_html=True)
st.write("")

tab_m, tab_r, tab_w, tab_p, tab_me = st.tabs(["🌅 晨读", "📚 问典", "✍️ 代笔", "🛡️ 隐盾", "⚙️ 我的"])

# ---------- 晨读 ----------
with tab_m:
    left, right = st.columns([3, 2])
    with left:
        st.markdown(f"**关注品种**：" + " ".join(f'<span class="yb-chip">{n}</span>' for n in profile.watch_names()), unsafe_allow_html=True)
        ups = st.file_uploader("导入今天的研报 / 公告 / 纪要（md / txt / pdf，可多选）", accept_multiple_files=True, key="m_up")
        c1, c2, c3 = st.columns(3)
        if c1.button("加载示例材料", key="m_sample"):
            st.session_state.docs = load_docs()
            st.session_state.redacted = 0
        if c2.button("导入上传文件", key="m_import") and ups:
            red = Redactor()
            n_red = 0
            for up in ups:
                tmp = Path("/tmp") / up.name
                tmp.write_bytes(up.getvalue())
                d = parse_doc(read_text(tmp), up.name)
                r = red.redact(d.text)
                d.text, n_red = r.text, n_red + sum(r.counts.values())
                st.session_state.docs.append(d)
            st.session_state.redacted += n_red
            st.success(f"导入 {len(ups)} 份，进模型前已脱敏 {n_red} 处")
        if c3.button("🌅 生成晨读", type="primary", key="m_go"):
            with st.spinner("研伴正在读……"):
                brief = build_brief(st.session_state.docs, profile, llm=llm)
            st.session_state.brief = brief
            mem.log("晨读", "生成", inp=str(len(st.session_state.docs)), out=brief.markdown, engine=brief.engine,
                    redacted=st.session_state.redacted, minutes=brief.minutes_saved)
            st.rerun()
        if st.session_state.get("brief"):
            b = st.session_state.brief
            with st.container(border=True):
                st.markdown(b.markdown)
            f1, f2, f3 = st.columns([1, 1, 4])
            if f1.button("👍 有用", key="m_up_"):
                mem.feedback("晨读", b.as_of.isoformat(), 1)
                st.toast("已记录，研伴会记住你的偏好")
            if f2.button("👎 没用", key="m_down_"):
                mem.feedback("晨读", b.as_of.isoformat(), -1)
                st.toast("已记录")
            st.download_button("下载晨读 .md", b.markdown, file_name=f"晨读_{b.as_of}.md")
    with right:
        st.markdown("**追问研伴**")
        q = st.text_input("例如：看多螺纹钢的那家理由是什么", key="m_q")
        if st.button("问", key="m_ask") and q:
            ans, hits = ask(q, st.session_state.docs, llm)
            mem.log("晨读", "追问", inp=q, out=ans, engine=llm.mode, minutes=3)
            st.markdown(f'<div class="yb-card">{ans}</div>', unsafe_allow_html=True)
        st.markdown("**已读材料**")
        st.dataframe(pd.DataFrame([{"日期": d.published_on, "类型": d.doc_type, "标题": d.title, "机构": d.publisher,
                                    "品种": "、".join(name_of(s) for s in d.symbols[:5])} for d in st.session_state.docs]),
                     use_container_width=True, hide_index=True, height=260)

# ---------- 问典 ----------
with tab_r:
    rb = RuleBook()
    st.markdown("**问品种规则，答案带出处，数字由程序计算。** 参数与条款均为示例，正式使用替换为交易所公告原文。")
    examples = ["沪铜 2609 结算价 78500 今天涨跌停多少", "螺纹钢 按 3200 价格 开 100 手 需要多少保证金",
                "沪铜 交割月前一月 持有 800 手 超没超限仓", "沪铜 2609 最后交易日是哪天", "什么情况下会被强行平仓"]
    ex = st.selectbox("示例问题", ["（自己输入）"] + examples, key="r_ex")
    q = st.text_input("问题", value="" if ex == "（自己输入）" else ex, key="r_q")
    if st.button("查", type="primary", key="r_go") and q:
        a = rb.answer(q, llm)
        mem.log("问典", a.intent, inp=q, out=a.text, engine=a.engine, minutes=3)
        st.markdown(f'<div class="yb-card"><span class="yb-chip">{a.intent}</span><br><br>{a.text.replace(chr(10), "<br>")}</div>', unsafe_allow_html=True)
        if a.calc:
            st.json({k: v for k, v in a.calc.items() if k != "出处"})
        if a.citations:
            st.markdown("**出处**：" + "；".join(a.citations))
    with st.expander("规则库里有什么（示例）"):
        st.dataframe(pd.DataFrame([{"条款": c.title, "出处": c.source, "涉及品种": "、".join(name_of(s) for s in c.symbols)} for c in rb.clauses]),
                     use_container_width=True, hide_index=True)
        st.dataframe(pd.DataFrame([{"品种": v["name"], "交易所": v["exchange"], "乘数": f"{v['multiplier']}{v['unit']}", "涨跌停": f"{v['limit_pct']:.0%}",
                                    "保证金": f"{v['margin_pct']:.0%}", "一般月份限仓": v["position_limit"]["一般月份"], "出处": v["source"]}
                                   for k, v in rb.params.items() if not k.startswith("_")]), use_container_width=True, hide_index=True)

# ---------- 代笔 ----------
with tab_w:
    st.markdown("**模板 + 本周晨读要点 + 数据 → 周报初稿；合规自检；人改定签发。**")
    notes = st.text_area("补充备注（可空）", value="下周关注保证金调整后的持仓成本", height=80, key="w_notes")
    with st.expander("查看 / 修改模板"):
        tpl = st.text_area("模板", value=load_template(profile.weekly_template), height=260, key="w_tpl")
    if st.button("✍️ 生成周报初稿", type="primary", key="w_go"):
        draft, eng = weekly_report(profile, st.session_state.get("brief"), notes=notes, template=st.session_state.get("w_tpl"), llm=llm)
        st.session_state.draft = draft
        mem.log("代笔", "周报初稿", out=draft, engine=eng, minutes=40)
    if st.session_state.get("draft"):
        edited = st.text_area("初稿（可直接修改）", value=st.session_state.draft, height=420, key="w_draft")
        findings = compliance_check(edited)
        if findings:
            st.markdown("**合规自检**")
            st.dataframe(pd.DataFrame([f.model_dump() for f in findings]).rename(
                columns={"kind": "类型", "term": "内容", "suggestion": "建议", "severity": "级别"}), use_container_width=True, hide_index=True)
        else:
            st.success("合规自检通过")
        st.download_button("下载周报 .md", edited, file_name="投研周报.md")

# ---------- 隐盾 ----------
with tab_p:
    st.markdown("**任何内容进模型前先脱敏；映射表只在本机；模型回答后本地还原。**")
    demo_text = ("客户：王建国先生（手机 13912345678，资金账号 8812345678901）今日反馈其螺纹钢空单持仓浮亏 1,250,000 元，"
                 "希望研究员给出后市判断。我们认为螺纹钢中期仍偏空，建议客户逢高加空。")
    src = st.text_area("原文（示例为虚构数据）", value=demo_text, height=110, key="p_src")
    names = st.text_input("额外指定的客户 / 机构名（逗号分隔，可空）", value="", key="p_names")
    if st.button("🛡️ 脱敏", type="primary", key="p_go"):
        r = Redactor(names=[n.strip() for n in names.split(",") if n.strip()]).redact(src)
        st.session_state.red = r
        mem.log("隐盾", "脱敏", inp=src, out=r.text, redacted=sum(r.counts.values()), minutes=2)
    if st.session_state.get("red"):
        r = st.session_state.red
        a, b_ = st.columns(2)
        a.markdown("**送入模型的版本（语义保持）**")
        a.code(r.text, language=None)
        b_.markdown("**本地映射表（不出域）**")
        b_.json(r.mapping)
        st.markdown("**模型回答后本地还原（示意）**")
        st.code(Redactor.restore(r.text, r.mapping), language=None)
    st.markdown("""<div class="yb-card"><b>渐进授权</b>：首次询问 → 会话内允许 → 长期信任 → 高危动作永远拦截（外发、删除、改系统）。<br>
<b>留痕</b>：每次调用只记摘要哈希、脱敏计数与引擎，不存原文。</div>""", unsafe_allow_html=True)
    st.dataframe(pd.DataFrame(mem.recent(10), columns=["时间", "技能", "动作", "脱敏处", "引擎", "备注"]), use_container_width=True, hide_index=True)

# ---------- 我的 ----------
with tab_me:
    st.markdown("**技能公共，伙伴私有。** 研伴按这里的画像给你不同的晨读与周报。")
    c1, c2 = st.columns(2)
    name = c1.text_input("姓名", profile.name, key="me_name")
    role = c1.selectbox("角色", ["研究员", "投资经理", "资管产品经理", "风控合规"], index=["研究员", "投资经理", "资管产品经理", "风控合规"].index(profile.role) if profile.role in ["研究员", "投资经理", "资管产品经理", "风控合规"] else 0, key="me_role")
    dept = c1.text_input("部门", profile.department, key="me_dept")
    watch = c2.multiselect("关注品种", options=list(SYMBOL_NAMES.keys()), default=profile.watch, format_func=lambda c: f"{name_of(c)}（{c}）", key="me_watch")
    horizon = c2.selectbox("关注周期", ["短期", "中期", "中长期"], index=["短期", "中期", "中长期"].index(profile.horizon), key="me_h")
    notes_txt = st.text_area("研伴记住的偏好（每行一条）", "\n".join(profile.notes), height=90, key="me_notes")
    if st.button("保存画像", type="primary", key="me_save"):
        profile.name, profile.role, profile.department, profile.watch, profile.horizon = name, role, dept, watch, horizon
        profile.notes = [n.strip() for n in notes_txt.splitlines() if n.strip()]
        save_profile(profile)
        st.session_state.profile = profile
        st.success("已保存。下次晨读按新画像生成。")
        st.rerun()
    if st.button("清空记忆与留痕（演示前重置）", key="me_clear"):
        mem.clear()
        st.session_state.pop("brief", None); st.session_state.pop("draft", None); st.session_state.pop("red", None)
        st.rerun()
    st.markdown(f'<div class="yb-foot">画像文件：data/profile.json ｜ 记忆与留痕：data/memory.sqlite3 ｜ 调用 {stats["calls"]} 次，累计节省 ~{stats["minutes_total"]} 分钟，反馈 {stats["feedback_n"]} 条</div>', unsafe_allow_html=True)
