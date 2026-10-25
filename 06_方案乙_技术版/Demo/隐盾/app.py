"""隐盾 · 网页版。启动：streamlit run app.py（或双击 启动隐盾.command / 启动隐盾.bat）
① 贴原文 → 脱敏 → 复制去问大模型；② 把大模型的回答贴回来 → 一键还原。全程本地规则，不联网。
同一会话里多次脱敏共用一张映射表：同一客户始终同一标签，任何一轮的回答都能还原。"""
from __future__ import annotations

import html
import json
import re
import time
from datetime import datetime

import streamlit as st

from yindun import __version__
from yindun.files import SUFFIXES, read_bytes
from yindun.privacy import KINDS, PROMPT_HINT, Redactor, Session, restore_detail, suspects
from yindun.samples import EXAMPLES

st.set_page_config(page_title="隐盾 · 贴给大模型前先脱敏", page_icon="🛡️", layout="wide")

# 主题色在 .streamlit/config.toml；这里只管自绘的品牌区、标签高亮与对照框
st.html("""
<style>
.stApp { background:
    radial-gradient(1000px 480px at 6% -10%, rgba(52,211,153,.13), transparent 62%),
    radial-gradient(900px 480px at 96% -6%, rgba(34,211,238,.12), transparent 60%),
    linear-gradient(rgba(96,165,250,.04) 1px, transparent 1px) 0 0 / 34px 34px,
    linear-gradient(90deg, rgba(96,165,250,.04) 1px, transparent 1px) 0 0 / 34px 34px,
    #070B16; }
header[data-testid="stHeader"] { background: transparent; }
.block-container { padding-top: 2rem; }
[data-testid="stHeaderActionElements"] { display: none; }
.yd-hero { border-radius:18px; padding:18px 24px; margin-bottom:6px; color:#E6ECF8;
  background: linear-gradient(135deg, rgba(20,31,56,.92), rgba(12,19,36,.92)) padding-box,
              linear-gradient(120deg, #34D399, #22D3EE 50%, #4361EE) border-box;
  border:1px solid transparent; box-shadow:0 18px 48px rgba(3,7,18,.55), 0 0 50px rgba(52,211,153,.10); }
.yd-top { display:flex; justify-content:space-between; align-items:center; gap:16px; flex-wrap:wrap; }
.yd-brand { display:flex; align-items:center; gap:14px; }
.yd-logo { width:46px; height:46px; border-radius:13px; display:flex; align-items:center; justify-content:center; font-size:23px; font-weight:800;
  color:#04121F; background:linear-gradient(135deg,#6EE7B7,#34D399 50%,#22D3EE); box-shadow:0 0 22px rgba(52,211,153,.35); }
.yd-name { font-size:29px; font-weight:800; letter-spacing:3px; line-height:1.1;
  background:linear-gradient(90deg,#E6ECF8,#A7F3D0 60%,#9BE7F7); -webkit-background-clip:text; background-clip:text; color:transparent; }
.yd-en { font-size:12px; color:#8A97B4; letter-spacing:1.5px; margin-top:3px; }
.yd-slogan { font-size:15px; color:#C7D2E8; margin-top:10px; }
.yd-chips { display:flex; gap:8px; flex-wrap:wrap; }
.yd-chip { display:inline-flex; align-items:center; gap:7px; padding:5px 12px; border-radius:999px; font-size:12.5px;
  background:rgba(15,23,42,.7); border:1px solid #22304F; color:#C7D2E8; white-space:nowrap; }
.yd-chip i { width:8px; height:8px; border-radius:50%; background:#34D399; box-shadow:0 0 10px #34D399; display:inline-block; }
.yd-box { background:rgba(13,20,38,.75); border:1px solid #22304F; border-radius:12px; padding:14px 16px; line-height:1.95;
  white-space:pre-wrap; overflow-wrap:anywhere; color:#E6ECF8; font-size:15px; max-height:440px; overflow:auto; }
.yd-tag { background:rgba(52,211,153,.16); color:#6EE7B7; border:1px solid rgba(52,211,153,.45); border-radius:6px; padding:0 4px; font-weight:600; }
.yd-hit { background:rgba(248,113,113,.16); color:#FCA5A5; border-bottom:2px solid #F87171; border-radius:3px; padding:0 2px; }
.yd-back { background:rgba(34,211,238,.14); color:#9BE7F7; border-bottom:2px solid #22D3EE; border-radius:3px; padding:0 2px; }
.yd-bad { background:rgba(251,191,36,.16); color:#FCD34D; border:1px dashed #FBBF24; border-radius:6px; padding:0 4px; }
.yd-tag, .yd-hit, .yd-back, .yd-bad { word-break:keep-all; }   /* 姓名、号码不在中间折行；超长令牌仍按 overflow-wrap 断开 */
.yd-cap { color:#8A97B4; font-size:12.5px; margin:2px 0 6px; }
.yd-foot { color:#5B6785; font-size:12px; margin-top:14px; }
[data-testid="stBaseButton-primary"] { background:linear-gradient(120deg,#059669,#0D9488 50%,#0891B2) !important; border:0 !important;
  box-shadow:0 6px 22px rgba(16,185,129,.30); font-weight:650; letter-spacing:.5px; }
[data-testid="stBaseButton-primary"]:hover { box-shadow:0 8px 28px rgba(34,211,238,.40); }
[data-testid="stMetric"] { background:rgba(17,26,46,.72); border:1px solid #22304F; border-radius:12px; padding:10px 14px; }
[data-testid="stMetricValue"] { color:#6EE7B7; }
div[data-testid="stTabs"] [role="tab"] p { font-size:15.5px; font-weight:600; }
div[data-testid="stTabs"] [role="tab"][aria-selected="true"] p { color:#A7F3D0; }
</style>""")

_LABEL_HTML = re.compile(r"&lt;([^\s&<>_]{1,8})_(\d+)&gt;")
_LABEL_TXT = re.compile(r"<([^\s<>_]{1,8})_(\d+)>")

ss = st.session_state
ss.setdefault("mapping", {})          # 会话映射表：标签 → 原文，只在本机内存
ss.setdefault("last", None)           # 最近一次脱敏：原文、结果、用时、新增条数
ss.setdefault("restored", None)       # 最近一次还原
ss.setdefault("extra_accounts", [])   # 用户确认要换的「疑似账号」
ss.setdefault("rounds", 0)
ss.setdefault("src", "")
ss.setdefault("ans", "")
ss.setdefault("kinds", list(KINDS))
ss.setdefault("hint", True)
ss.setdefault("example", None)
ss.setdefault("view", "脱敏结果")    # 结果区视图：脱敏结果 / 原文对照 / 本次映射


def _lines(key: str) -> list[str]:
    return [x.strip() for x in ss.get(key, "").splitlines() if x.strip()]


def _redactor() -> Redactor:
    return Redactor(names=_lines("names"), orgs=_lines("orgs"), keep=_lines("keep"), kinds=ss.kinds,
                    terms={"账号": ss.extra_accounts})


def run_redact() -> None:
    src = ss.src
    if not src.strip():
        ss.last = None
        return
    before = len(ss.mapping)
    session = Session(_redactor(), ss.mapping)
    t0 = time.perf_counter()
    res = session.redact(src)
    ms = (time.perf_counter() - t0) * 1000
    ss.mapping = session.mapping
    ss.rounds += 1
    ss.last = {"src": src, "res": res, "ms": ms, "new": len(ss.mapping) - before, "sus": suspects(res.text)}
    ss.view = "脱敏结果"   # 新结果先看标签


def load_example(name: str) -> None:
    ss.src = EXAMPLES[name]["text"]
    ss.example = name
    run_redact()


def accept_suspects() -> None:
    ss.extra_accounts = list(dict.fromkeys(ss.extra_accounts + (ss.last or {}).get("sus", [])))
    run_redact()


def load_upload() -> None:
    f = ss.get("upload")
    if f is None:
        return
    try:
        ss.src = read_bytes(f.name, f.getvalue())
        ss.upload_error = ""
    except (ValueError, KeyError, OSError) as e:   # KeyError：docx 里没有正文部件
        ss.upload_error = f"{f.name} 读不了：{e}"


def new_session() -> None:
    ss.mapping, ss.last, ss.restored, ss.extra_accounts, ss.rounds = {}, None, None, [], 0
    ss.src, ss.ans, ss.example = "", "", None


def fill_answer() -> None:
    ss.ans = EXAMPLES[ss.example]["answer"] if ss.example else ""


def run_restore() -> None:
    ans = ss.ans
    if not ans.strip() or not ss.mapping:
        ss.restored = {"empty": True}
        return
    plain = restore_detail(ans, ss.mapping)
    marked = restore_detail(html.escape(ans), ss.mapping,
                            wrap=lambda orig, label: f'<span class="yd-back" title="{html.escape(label)}">{html.escape(orig)}</span>')
    ss.restored = {"res": plain, "html": marked.text}


def load_map_file() -> None:
    f = ss.get("map_upload")
    if f is None:
        return
    try:
        data = json.loads(f.getvalue().decode("utf-8"))
        if not isinstance(data, dict) or not all(_LABEL_TXT.fullmatch(k) for k in data):
            raise ValueError("不是隐盾导出的映射表")
        ss.mapping = {**data, **ss.mapping}
        ss.map_msg = f"已载入 {len(data)} 条映射"
    except (ValueError, UnicodeDecodeError) as e:
        ss.map_msg = f"载入失败：{e}"


def tagged_html(text: str) -> str:
    return _LABEL_HTML.sub(r'<span class="yd-tag">&lt;\1_\2&gt;</span>', html.escape(text))


def source_html(res) -> str:
    """原文里被换掉的内容标红：把脱敏结果按映射表还原，还原处套上标红的样式。"""
    return restore_detail(html.escape(res.text), res.mapping,
                          wrap=lambda orig, label: f'<span class="yd-hit" title="{html.escape(label)}">{html.escape(orig)}</span>').text


def _label_order(item: tuple[str, str]) -> tuple[int, int]:
    kind, n = _LABEL_TXT.fullmatch(item[0]).groups()
    return (KINDS.index(kind) if kind in KINDS else len(KINDS), int(n))


def label_hits(text: str) -> list[str]:
    return [m.group(0) for m in _LABEL_TXT.finditer(text)]


# ---------- 侧边栏：会话映射表、名单、类型开关 ----------
with st.sidebar:
    st.subheader("会话映射表", anchor=False)
    st.metric("映射条数", f"{len(ss.mapping)} 条", help="同一会话里多次脱敏共用这张表：同一客户始终同一标签，任何一轮的回答都能还原。")
    st.caption("只在本机内存里；关掉页面或点「新会话」即清空。")
    with st.container(horizontal=True):
        st.download_button("导出", data=json.dumps(ss.mapping, ensure_ascii=False, indent=2),
                           file_name=f"隐盾映射表_{datetime.now():%Y%m%d_%H%M}.map.json", mime="application/json",
                           icon=":material/download:", disabled=not ss.mapping,
                           help="映射表里是原文，只存本机，不要外发。隔天还原时在「② 还原」里载入。")
        st.button("新会话", on_click=new_session, icon=":material/restart_alt:", help="清空映射表、原文与回答，开新话题", key="btn_new")
    st.divider()
    st.subheader("自定义名单", anchor=False)
    st.text_area("客户 / 员工姓名（一行一个）", key="names", height=80, placeholder="张三\n李四",
                 help="没有「客户」「先生 / 女士」上下文的裸姓名，规则认不出，加到这里")
    st.text_area("机构名（一行一个）", key="orgs", height=68, placeholder="某某投资公司")
    st.text_area("不脱敏白名单（一行一个）", key="keep", height=68, placeholder="95521\n公司官网邮箱",
                 help="公司客服热线、自家公司名等公开信息，不换")
    st.divider()
    st.pills("脱敏类型", KINDS, selection_mode="multi", key="kinds",
             help="默认全开。要让模型算比例、看金额时，可以临时关掉「金额」")
    st.toggle("加一句「请原样保留标签」", key="hint",
              help="提醒模型别改写 <客户_1> 这类标签；就算改了写法，② 还原也认得")

# ---------- 品牌区 ----------
st.html(f"""
<div class="yd-hero"><div class="yd-top">
  <div class="yd-brand"><div class="yd-logo">盾</div>
    <div><div class="yd-name">隐盾</div><div class="yd-en">YINDUN · 贴给大模型前先脱敏 · v{__version__}</div></div></div>
  <div class="yd-chips"><span class="yd-chip"><i></i>本机运行 · 不联网</span>
    <span class="yd-chip">{len(KINDS)} 类敏感信息</span><span class="yd-chip">多轮一致 · 容错还原</span></div>
</div>
<div class="yd-slogan">客户姓名、手机、账号、证件、内网地址、密钥、金额…… 换成 <b style="color:#6EE7B7">&lt;客户_1&gt;</b> 这样的语义标签再发给大模型；拿到回答，一键换回原文。</div>
</div>""")

tab1, tab2, tab3 = st.tabs(["① 脱敏 · 贴原文", "② 还原 · 贴大模型的回答", "规则与边界"])

with tab1:
    left, right = st.columns([1, 1], gap="large")
    with left:
        st.text_area("原文", key="src", height=300, placeholder="把要问大模型的内容贴到这里：微信原话、邮件、工单、日志、会议纪要……")
        st.file_uploader("或拖入文件", type=sorted(s.lstrip(".") for s in SUFFIXES), key="upload", on_change=load_upload,
                         help="Word 只取正文文字；表格类持仓明细本就不该贴给公网模型")
        if ss.get("upload_error"):
            st.error(ss.upload_error, icon=":material/error:")
        with st.container(horizontal=True):
            st.button("脱敏", type="primary", on_click=run_redact, icon=":material/shield:", key="btn_redact")
        st.caption("试试示例（虚构数据）：")
        with st.container(horizontal=True):
            for i, name in enumerate(EXAMPLES):
                st.button(name, on_click=load_example, args=(name,), key=f"ex_{i}", icon=":material/description:")
    with right:
        last = ss.last
        if not last:
            st.info("左边贴原文或点一个示例，点「脱敏」。结果在这里：标签高亮、一键复制、原文对照。", icon=":material/arrow_back:")
        else:
            res = last["res"]
            hits = label_hits(res.text)
            kinds_hit = {m.group(1) for m in _LABEL_TXT.finditer(res.text)}
            with st.container(horizontal=True):
                st.metric("本次换掉", f"{len(hits)} 处")
                st.metric("涉及类型", f"{len(kinds_hit)} 类")
                st.metric("会话映射", f"{len(ss.mapping)} 条", delta=f"+{last['new']} 新增" if ss.rounds > 1 else None,
                          delta_color="off")
                st.metric("用时", f"{last['ms']:.1f} ms")
            view = st.segmented_control("查看", ["脱敏结果", "原文对照", "本次映射"], key="view", label_visibility="collapsed")
            if view == "原文对照":
                st.html('<div class="yd-cap">原文里标红的内容，发出去之前已换成标签（鼠标停留可看对应标签）</div>'
                        f'<div class="yd-box">{source_html(res)}</div>')
            elif view == "本次映射":
                st.dataframe([{"标签": k, "原文": v} for k, v in sorted(res.mapping.items(), key=_label_order)], hide_index=True)
                st.caption("映射表只在本机；页面关闭即清空。")
            else:
                st.html(f'<div class="yd-box">{tagged_html(res.text)}</div>')
            out = (PROMPT_HINT + "\n\n" if ss.hint else "") + res.text
            st.caption("复制下面这段去问大模型（右上角复制按钮）：")
            st.code(out, language=None, wrap_lines=True)
            if last["sus"]:
                with st.container(border=True):
                    st.warning(f"疑似遗漏：{'、'.join(last['sus'])} 像账号 / 客户号，但前后没有「账号」之类的上下文，规则没敢动。",
                               icon=":material/help:")
                    st.button("确认是账号，一起脱敏", on_click=accept_suspects, icon=":material/done_all:", key="btn_sus")
            st.download_button("下载脱敏文本", data=out, file_name="脱敏文本.txt", icon=":material/download:", key="dl_txt")

with tab2:
    left, right = st.columns([1, 1], gap="large")
    with left:
        st.text_area("大模型的回答", key="ans", height=300, placeholder="把大模型返回的内容贴到这里（含 <客户_1> 这类标签）……")
        with st.container(horizontal=True):
            st.button("还原", type="primary", on_click=run_restore, icon=":material/lock_open:", key="btn_restore")
            if ss.example:
                st.button("填入模拟回答", on_click=fill_answer, icon=":material/smart_toy:", key="btn_fill",
                          help="演示用：一段手写的「模型回答」，故意把几个标签改了写法，看还原怎么容错")
        with st.expander("用导出的映射表还原（隔天 / 换了页面）", icon=":material/upload_file:"):
            st.file_uploader("载入 .map.json", type=["json"], key="map_upload", on_change=load_map_file)
            if ss.get("map_msg"):
                st.caption(ss.map_msg)
    with right:
        r = ss.restored
        if not r:
            st.info("贴回答、点「还原」。模型把标签改成〈客户_1〉【客户_1】\\<客户\\_1\\>、客户1 也认得。", icon=":material/arrow_back:")
        elif r.get("empty"):
            st.warning("先在 ① 里脱敏一次（或载入映射表），再贴回答还原。" if not ss.mapping else "回答是空的。",
                       icon=":material/info:")
        else:
            res = r["res"]
            with st.container(horizontal=True):
                st.metric("还原", f"{res.restored} 处")
                st.metric("纠正写法", f"{res.fixed} 处", help="模型改了标签写法（括号、转义、少了下划线），已按原标签还原")
                st.metric("对不上的标签", f"{len(res.unknown)} 个")
            st.html('<div class="yd-cap">青色为换回的原文（鼠标停留可看原标签）</div>'
                    f'<div class="yd-box">{r["html"]}</div>')
            if res.unknown:
                st.warning(f"映射表里没有：{'、'.join(res.unknown)}。多半是模型自己编的标签，请人工核对后再用。",
                           icon=":material/warning:")
            st.code(res.text, language=None, wrap_lines=True)

with tab3:
    st.subheader("识别规则", anchor=False)
    st.dataframe([
        {"类型": "客户", "怎么认": "「客户 / 投资者」后的姓名、「姓 + 先生 / 女士」、紧挨在手机号或账号前的姓名；自定义名单", "例子": "客户王建国先生 → 客户<客户_1>先生"},
        {"类型": "机构", "怎么认": "「客户 / 机构 / 投资者」后的 XX 公司 / 集团 / 基金 / 资管 / 证券 / 期货 / 银行……；自定义名单", "例子": "机构：华东某某投资公司 → 机构：<机构_1>"},
        {"类型": "手机", "怎么认": "1 开头 11 位，含 +86、空格、短横分段写法", "例子": "139 1234 5678 → <手机_1>"},
        {"类型": "电话", "怎么认": "区号 - 号码（- 分机）", "例子": "021-61234567 → <电话_1>"},
        {"类型": "身份证", "怎么认": "18 位，校验出生日期，含 6-8-4 分段写法", "例子": "→ <身份证_1>"},
        {"类型": "账号", "怎么认": "10～19 位数字、4-4-4-4 银行卡；6～9 位要有「资金账号 / 客户号 / 卡号」上下文", "例子": "资金账号：88123456 → 资金账号：<账号_1>"},
        {"类型": "邮箱", "怎么认": "标准邮箱格式", "例子": "wangjg@example.com → <邮箱_1>"},
        {"类型": "内网地址", "怎么认": "10.x / 172.16～31.x / 192.168.x", "例子": "192.168.8.21 → <内网地址_1>"},
        {"类型": "密钥", "怎么认": "api_key / token / password / 密码 = …（只换值）、连接串口令、私钥块、sk- / ghp_ / AKIA / JWT 令牌", "例子": "api_key=sk-… → api_key=<密钥_1>"},
        {"类型": "金额", "怎么认": "千分位大数、带「元」的金额、¥ 开头；「万 / 亿」前要有权益、浮亏、资金等语境；单价（元/吨）不换", "例子": "浮亏 125 万元 → 浮亏 <金额_1>"},
    ], hide_index=True)
    st.subheader("设计要点", anchor=False)
    st.markdown(
        "- **语义保持**：标签带类型，模型仍知道这是一个客户、一个手机号，回答质量基本不受影响。\n"
        "- **多轮一致**：同一会话共用映射表，同一客户、同一号码（不同写法也算）始终同一标签。\n"
        "- **容错还原**：模型把标签改成〈客户_1〉、【客户_1】、\\<客户\\_1\\>、&lt;客户_1&gt;、客户1，照样还原；编出来的标签单独提示。\n"
        "- **端侧执行**：纯规则，不联网、不装模型；页面只在本机监听，映射表只在内存。")
    st.subheader("已知边界", anchor=False)
    st.markdown(
        "- 没有上下文的裸姓名、外号（「老张」）认不出：加到侧边栏的自定义名单。\n"
        "- 没有上下文的 6～9 位数字只提示「疑似遗漏」，不自动换，免得把日期、数量当账号。\n"
        "- 持仓明细表、财务报表不在处理范围：这类数据本就不该贴给公网模型，隐盾是「贴之前过一道门」，不是替你把报表脱敏后上传。\n"
        "- 规则会有漏网之鱼：发出去之前扫一眼「原文对照」，红色以外的内容就是会发出去的原样文字。")

st.html(f'<div class="yd-foot">隐盾 v{__version__} · 个人赛工具赛道 · 演示数据均为虚构 · 同源规则已在团队作品「需知」中作为安全模块使用</div>')
