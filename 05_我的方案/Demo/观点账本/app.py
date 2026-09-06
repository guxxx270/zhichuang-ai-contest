"""观点账本 · Demo 界面（Streamlit）。启动：streamlit run app.py"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from ledger import config
from ledger.backtest import evaluate_all
from ledger.extract import extract_opinions, read_text_file
from ledger.llm import LLM
from ledger.market import PriceBook
from ledger.privacy import Redactor
from ledger.report import leaderboard, morning_brief, outcomes_df, symbol_experts
from ledger.store import Ledger

st.set_page_config(page_title="观点账本 · 研判追踪与准确率回溯", page_icon="📒", layout="wide")


@st.cache_resource
def get_llm() -> LLM:
    return LLM()


def get_ledger() -> Ledger:
    if "ledger" not in st.session_state:
        st.session_state.ledger = Ledger()
    return st.session_state.ledger


llm, ledger = get_llm(), get_ledger()

with st.sidebar:
    st.title("📒 观点账本")
    st.caption("研判追踪与准确率回溯 Agent · Demo")
    if llm.mode == "api":
        st.success(f"LLM：已接入（{config.LLM_MODEL}）")
    else:
        st.warning("LLM：mock 模式（未配置 .env 里的 key，规则抽取兜底）")
    st.metric("账本观点数", ledger.count())
    if st.button("清空账本", type="secondary"):
        ledger.clear()
        st.rerun()
    st.divider()
    st.caption("数据不出域 · 进模型前脱敏 · 关键节点人工确认")

tab1, tab2, tab3, tab4, tab5 = st.tabs(["① 抽取观点", "② 观点账本", "③ 回溯与榜单", "④ 晨报", "⑤ 脱敏演示"])

# ---------- ① 抽取 ----------
with tab1:
    st.subheader("把研报 / 晨会纪要变成观点卡")
    col_a, col_b = st.columns([2, 1])
    with col_a:
        sample_dir = config.DATA_DIR / "sample_reports"
        samples = sorted(p.name for p in sample_dir.glob("*") if p.suffix in (".md", ".txt", ".pdf"))
        picked = st.multiselect("选择示例材料（虚构示例）", samples, default=samples)
        uploads = st.file_uploader("或上传自己的材料（md / txt / pdf，可多选）", accept_multiple_files=True)
    with col_b:
        default_analyst = st.text_input("默认作者（材料未署名时使用）", value="")
        pub = st.date_input("发布日期（材料里找不到时使用）", value=date.today())
        do_redact = st.checkbox("进模型前先脱敏", value=True)
    if st.button("开始抽取", type="primary"):
        texts: list[tuple[str, str]] = []
        for name in picked:
            texts.append((name, read_text_file(sample_dir / name)))
        for up in uploads or []:
            tmp = Path(st.session_state.get("tmpdir", "/tmp")) / up.name
            tmp.write_bytes(up.getvalue())
            texts.append((up.name, read_text_file(tmp)))
        redactor = Redactor()
        all_cards = []
        for name, text in texts:
            if do_redact:
                red = redactor.redact(text)
                text = red.text
                if red.counts:
                    st.info(f"{name}：进模型前已脱敏 {sum(red.counts.values())} 处（{', '.join(f'{k}×{v}' for k, v in red.counts.items())}）")
            cards, engine = extract_opinions(text, source=name, default_analyst=default_analyst or None,
                                             published_on=None, llm=llm)
            st.caption(f"{name}：抽取 {len(cards)} 条（引擎：{'大模型' if engine == 'llm' else '规则兜底'}）")
            all_cards.extend(cards)
        st.session_state.pending = all_cards
    if st.session_state.get("pending"):
        cards = st.session_state.pending
        df = pd.DataFrame([c.model_dump() for c in cards])
        st.dataframe(df[["analyst", "symbol_name", "direction", "horizon_days", "confidence", "rationale", "published_on", "source"]]
                     .rename(columns={"analyst": "研究员", "symbol_name": "品种", "direction": "方向", "horizon_days": "期限(日)",
                                      "confidence": "置信度", "rationale": "核心逻辑", "published_on": "发布日", "source": "来源"}),
                     use_container_width=True)
        st.caption("人工确认节点：核对无误后再入账（Human-in-the-Loop）。")
        if st.button("确认无误，写入账本"):
            n = ledger.add(cards)
            st.success(f"入账 {n} 条（重复观点自动跳过）")
            st.session_state.pending = []
            st.rerun()

# ---------- ② 账本 ----------
with tab2:
    st.subheader("观点账本（可溯源）")
    cards = ledger.all()
    if not cards:
        st.info("账本为空，先去 ① 抽取。")
    else:
        df = pd.DataFrame([c.model_dump() for c in cards])
        st.dataframe(df.rename(columns={"analyst": "研究员", "symbol": "代码", "symbol_name": "品种", "direction": "方向",
                                        "horizon_days": "期限(日)", "confidence": "置信度", "rationale": "核心逻辑",
                                        "published_on": "发布日", "source": "来源", "source_quote": "原文依据"}),
                     use_container_width=True)

# ---------- ③ 回溯 ----------
with tab3:
    st.subheader("到期回溯：谁说对了")
    prices = PriceBook.from_csv()
    st.caption(f"行情来源：data/sample_prices.csv（示例随机行情，{len(prices.symbols())} 个品种，至 {max(prices.last_date(s) for s in prices.symbols())}）。正式演示替换为真实日线。")
    cards = ledger.all()
    if cards:
        outcomes = evaluate_all(cards, prices)
        st.session_state.outcomes = outcomes
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**研判胜率榜**")
            st.dataframe(leaderboard(outcomes), use_container_width=True, hide_index=True)
        with c2:
            st.markdown("**品种专家图谱**")
            st.dataframe(symbol_experts(outcomes), use_container_width=True, hide_index=True)
        st.markdown("**逐条回溯明细**")
        st.dataframe(outcomes_df(outcomes), use_container_width=True, hide_index=True)
        st.caption("判定口径：多/空以到期涨跌幅超过 ±1% 判定；震荡以 |涨跌幅| ≤ 2% 判定。口径可配置、可追溯。")
    else:
        st.info("账本为空。")

# ---------- ④ 晨报 ----------
with tab4:
    st.subheader("带胜率的研判晨报")
    if st.session_state.get("outcomes"):
        if st.button("生成晨报", type="primary"):
            st.session_state.brief = morning_brief(st.session_state.outcomes, llm=llm)
        if st.session_state.get("brief"):
            st.markdown(st.session_state.brief)
            st.download_button("下载晨报 .md", st.session_state.brief, file_name="研判晨报.md")
    else:
        st.info("先在 ③ 完成回溯。")

# ---------- ⑤ 脱敏 ----------
with tab5:
    st.subheader("贴给大模型前自动脱敏（个人赛工具赛道雏形）")
    demo_text = ("客户：王建国先生（手机 13912345678，资金账号 8812345678901）今日反馈其螺纹钢空单持仓浮亏 1,250,000 元，"
                 "希望研究员给出后市判断。我们认为螺纹钢中期仍偏空，建议客户逢高加空。")
    src = st.text_area("原文（示例为虚构数据）", value=demo_text, height=120)
    names = st.text_input("额外指定的客户/人名（逗号分隔，可空）", value="")
    if st.button("脱敏"):
        r = Redactor(names=[n.strip() for n in names.split(",") if n.strip()]).redact(src)
        st.session_state.red = r
    if st.session_state.get("red"):
        r = st.session_state.red
        st.markdown("**送入大模型的版本（语义保持）**")
        st.code(r.text, language=None)
        st.markdown("**本地映射表（不出域）**")
        st.json(r.mapping)
        st.markdown("**模型回答后本地还原（示意）**")
        st.code(Redactor.restore(r.text, r.mapping), language=None)
