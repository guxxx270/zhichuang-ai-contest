"""命令行一键跑通整条链路（不需要界面）：
    python run_demo.py            # 用 data/sample_reports 与 data/sample_prices.csv
    python run_demo.py --reset    # 先清空账本
"""
from __future__ import annotations

import argparse
from pathlib import Path

from ledger import config
from ledger.backtest import evaluate_all
from ledger.extract import extract_opinions, read_text_file
from ledger.llm import LLM
from ledger.market import PriceBook
from ledger.privacy import Redactor
from ledger.report import leaderboard, morning_brief, outcomes_df, symbol_experts
from ledger.store import Ledger


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--reports", default=str(config.DATA_DIR / "sample_reports"))
    args = ap.parse_args()

    llm = LLM()
    print(f"LLM 模式：{llm.mode}（{'已接入 ' + config.LLM_MODEL if llm.mode == 'api' else '未配置 key，规则抽取兜底'}）")
    ledger = Ledger()
    if args.reset:
        ledger.clear()

    redactor = Redactor()
    total = 0
    for p in sorted(Path(args.reports).glob("*")):
        if p.suffix.lower() not in (".md", ".txt", ".pdf"):
            continue
        text = read_text_file(p)
        red = redactor.redact(text)                      # 进模型前先脱敏
        cards, engine = extract_opinions(red.text, source=p.name, llm=llm)
        n = ledger.add(cards)
        total += n
        print(f"  {p.name}: 抽取 {len(cards)} 条（{engine}），新增入账 {n} 条，脱敏 {sum(red.counts.values())} 处")
    print(f"账本共 {ledger.count()} 条观点\n")

    prices = PriceBook.from_csv()
    outcomes = evaluate_all(ledger.all(), prices)
    print("== 回溯明细 ==")
    print(outcomes_df(outcomes)[["研究员", "品种", "方向", "发布日", "到期日", "涨跌幅", "结果"]].to_string(index=False))
    print("\n== 研判胜率榜 ==")
    print(leaderboard(outcomes).to_string(index=False))
    print("\n== 品种专家图谱 ==")
    print(symbol_experts(outcomes).to_string(index=False))
    print("\n== 晨报 ==")
    print(morning_brief(outcomes, llm=llm))


if __name__ == "__main__":
    main()
