"""隐盾 · 网页版。启动：streamlit run app.py
左边贴原文 → 右边得到脱敏文本（复制去问大模型）→ 把大模型的回答贴回来 → 一键还原。全程本地规则，不联网。"""
from __future__ import annotations

import html
import re

import streamlit as st

from yindun.privacy import Redactor

NAVY, ACCENT, PAPER, MIST = "#14367A", "#E07A1F", "#F6F4EF", "#E5E7EB"
st.set_page_config(page_title="隐盾 · 贴给大模型前先脱敏", page_icon="🛡️", layout="wide")
st.markdown(f"""
<style>
.stApp {{ background:{PAPER}; }}
.yd-hero {{ background:linear-gradient(135deg,{NAVY},#2a5fc4); color:#fff; border-radius:16px; padding:18px 24px; margin-bottom:12px; }}
.yd-hero h1 {{ margin:0; font-size:26px; letter-spacing:2px; }}
.yd-hero .s {{ opacity:.9; font-size:14px; margin-top:4px; }}
.yd-kpi {{ background:#fff; border:1px solid {MIST}; border-left:5px solid {ACCENT}; border-radius:10px; padding:10px 14px; }}
.yd-kpi .n {{ font-size:22px; font-weight:700; color:{NAVY}; }} .yd-kpi .l {{ font-size:12px; color:#6b7280; }}
.yd-box {{ background:#fff; border:1px solid {MIST}; border-radius:10px; padding:12px 14px; line-height:1.8; white-space:pre-wrap; }}
.yd-tag {{ background:#fff7e0; color:#92400e; border:1px solid #fcd34d; border-radius:6px; padding:0 5px; font-weight:600; }}
.yd-foot {{ color:#6b7280; font-size:12px; }}
</style>""", unsafe_allow_html=True)

st.markdown("""<div class="yd-hero"><h1>🛡️ 隐盾</h1><div class="s">贴给大模型前先脱敏，用完一键还原 · 本地规则，不联网 · 手机号 / 账号 / 证件号 / 邮箱 / 内网地址 / 密钥 / 金额 / 客户与机构名</div></div>""", unsafe_allow_html=True)

DEMO = ("客户王建国先生（手机 13912345678，资金账号 8812345678901，邮箱 wjg@example.com）今日反馈其螺纹钢空单浮亏 1,250,000 元；"
        "机构：华东某某投资公司 的对接人也问了同样问题。服务器 192.168.10.21，配置里 api_key=sk-abc123456789。请研究员给出后市判断。")

with st.sidebar:
    st.markdown("**自定义名单**（可选，一行一个）")
    names = st.text_area("客户 / 员工姓名", height=90, placeholder="张三\n李四")
    orgs = st.text_area("机构名", height=90, placeholder="某某投资公司")
    st.markdown('<div class="yd-foot">名单只在本机内存里，刷新即清空。同一实体全文映射到同一标签，多轮对话保持一致。</div>', unsafe_allow_html=True)

if "mapping" not in st.session_state:
    st.session_state.mapping = {}

tab1, tab2 = st.tabs(["① 脱敏（贴原文）", "② 还原（贴大模型的回答）"])

with tab1:
    l, r = st.columns(2)
    with l:
        src = st.text_area("原文", value=st.session_state.get("src", ""), height=260, placeholder="把要问大模型的内容贴到这里……", key="src_in")
        c1, c2 = st.columns(2)
        if c1.button("🛡️ 脱敏", type="primary", width="stretch") and src.strip():
            red = Redactor(names=[n.strip() for n in names.splitlines() if n.strip()], orgs=[o.strip() for o in orgs.splitlines() if o.strip()])
            res = red.redact(src)
            st.session_state.result, st.session_state.mapping, st.session_state.src = res, res.mapping, src
        if c2.button("试试示例", width="stretch"):
            st.session_state.src = DEMO
            st.rerun()
    with r:
        res = st.session_state.get("result")
        if res:
            k = st.columns(3)
            k[0].markdown(f'<div class="yd-kpi"><div class="n">{res.total}</div><div class="l">脱敏处</div></div>', unsafe_allow_html=True)
            k[1].markdown(f'<div class="yd-kpi"><div class="n">{len(res.counts)}</div><div class="l">敏感类型</div></div>', unsafe_allow_html=True)
            k[2].markdown(f'<div class="yd-kpi"><div class="n">本机</div><div class="l">数据边界 · 未联网</div></div>', unsafe_allow_html=True)
            shown = html.escape(res.text)
            shown = re.sub(r"(&lt;[^&]+?_\d+&gt;)", r'<span class="yd-tag">\1</span>', shown)
            st.markdown(f'<div class="yd-box">{shown}</div>', unsafe_allow_html=True)
            st.code(res.text, language=None)
            st.caption("上面代码框右上角可一键复制；把它贴给大模型即可。")
            with st.expander(f"映射表（{len(res.mapping)} 条，仅本机可见）"):
                st.table([{"标签": k, "原文": v} for k, v in res.mapping.items()])
            st.markdown("**类型统计**：" + "，".join(f"{k} {v}" for k, v in res.counts.items()), unsafe_allow_html=True)
        else:
            st.info("左边贴原文，点「脱敏」。示例里有手机号、账号、邮箱、内网地址、密钥、金额和客户 / 机构名。")

with tab2:
    ans = st.text_area("大模型的回答（含〈客户_1〉这类标签）", height=220, placeholder="把大模型返回的内容贴到这里……")
    if st.button("🔓 还原", type="primary") and ans.strip():
        if not st.session_state.mapping:
            st.warning("还没有映射表：先在①里脱敏一次。")
        else:
            out = Redactor.restore(ans, st.session_state.mapping)
            st.code(out, language=None)
            st.caption(f"已按 {len(st.session_state.mapping)} 条映射还原；映射表只在本机。")
st.markdown('<div class="yd-foot">隐盾 · 个人赛工具赛道 · 规则清单见 README；同一套规则已嵌入团队作品「研伴」「需知」。</div>', unsafe_allow_html=True)
