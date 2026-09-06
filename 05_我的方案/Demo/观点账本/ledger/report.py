"""榜单与晨报：高手榜、品种专家图谱、带胜率的晨报文本。"""
from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd

from .llm import LLM
from .schema import Outcome


def outcomes_df(outcomes: list[Outcome]) -> pd.DataFrame:
    rows = []
    for o in outcomes:
        c = o.card
        rows.append({"研究员": c.analyst, "品种": f"{c.symbol_name}({c.symbol})", "方向": c.direction,
                     "期限(日)": c.horizon_days, "发布日": c.published_on, "到期日": o.end_date,
                     "起始价": o.start_price, "到期价": o.end_price,
                     "涨跌幅": None if o.pct is None else f"{o.pct:+.2%}",
                     "结果": "✅ 对" if o.hit else ("❌ 错" if o.hit is False else f"⏳ {o.note or '未到期'}"),
                     "收益": o.pnl, "依据": c.rationale, "来源": c.source})
    return pd.DataFrame(rows)


def leaderboard(outcomes: list[Outcome], min_n: int = 1) -> pd.DataFrame:
    done = [o for o in outcomes if o.hit is not None]
    if not done:
        return pd.DataFrame(columns=["研究员", "观点数", "已到期", "说对", "胜率", "平均收益"])
    df = pd.DataFrame([{"研究员": o.card.analyst, "hit": int(o.hit), "pnl": o.pnl} for o in done])
    total = pd.Series({o.card.analyst: 1 for o in outcomes}).groupby(level=0).size()
    g = df.groupby("研究员").agg(已到期=("hit", "size"), 说对=("hit", "sum"), 平均收益=("pnl", "mean"))
    g["观点数"] = [sum(1 for o in outcomes if o.card.analyst == a) for a in g.index]
    g["胜率"] = g["说对"] / g["已到期"]
    g = g[g["已到期"] >= min_n].sort_values(["胜率", "平均收益"], ascending=False)
    g["胜率"] = g["胜率"].map(lambda x: f"{x:.0%}")
    g["平均收益"] = g["平均收益"].map(lambda x: f"{x:+.2%}")
    return g.reset_index()[["研究员", "观点数", "已到期", "说对", "胜率", "平均收益"]]


def symbol_experts(outcomes: list[Outcome]) -> pd.DataFrame:
    done = [o for o in outcomes if o.hit is not None]
    if not done:
        return pd.DataFrame(columns=["品种", "最准的人", "胜率", "样本"])
    df = pd.DataFrame([{"品种": f"{o.card.symbol_name}({o.card.symbol})", "研究员": o.card.analyst, "hit": int(o.hit)} for o in done])
    g = df.groupby(["品种", "研究员"]).agg(样本=("hit", "size"), 说对=("hit", "sum")).reset_index()
    g["胜率"] = g["说对"] / g["样本"]
    best = g.sort_values(["品种", "胜率", "样本"], ascending=[True, False, False]).groupby("品种").head(1)
    best["胜率"] = best["胜率"].map(lambda x: f"{x:.0%}")
    return best.rename(columns={"研究员": "最准的人"})[["品种", "最准的人", "胜率", "样本"]].reset_index(drop=True)


def morning_brief(outcomes: list[Outcome], as_of: Optional[date] = None, llm: Optional[LLM] = None) -> str:
    """晨报：把最新观点 + 各研究员近期胜率放在一起。api 模式让模型润色，mock 模式用模板。"""
    as_of = as_of or date.today()
    lb = leaderboard(outcomes)
    latest = sorted(outcomes, key=lambda o: o.card.published_on, reverse=True)[:8]
    win = {r["研究员"]: r["胜率"] for _, r in lb.iterrows()}
    lines = [f"# 研判晨报（{as_of.isoformat()}）", "", "## 最新研判（附研究员近期胜率）"]
    for o in latest:
        c = o.card
        lines.append(f"- {c.symbol_name}：**{c.direction}**（{c.horizon_days} 个交易日）— {c.analyst}"
                     f"（近期胜率 {win.get(c.analyst, '暂无')}）｜{c.rationale}")
    lines += ["", "## 研判胜率榜"]
    for _, r in lb.iterrows():
        lines.append(f"- {r['研究员']}：{r['胜率']}（{r['说对']}/{r['已到期']}），平均收益 {r['平均收益']}")
    lines += ["", "> 胜率口径：多/空以到期涨跌幅超过 ±1% 判定，震荡以 |涨跌幅| ≤ 2% 判定；仅供内部研判参考，不构成投资建议。"]
    draft = "\n".join(lines)
    llm = llm or LLM()
    if llm.mode == "api":
        try:
            polished = llm.chat(
                "你是期货公司研究所的晨报编辑。把给定的结构化内容改写成简洁、专业的晨报，保留全部数字与署名，"
                "不新增任何未出现的判断，不超过 400 字，使用 Markdown。",
                draft,
            )
            if polished.strip():
                return polished
        except Exception:
            pass
    return draft
