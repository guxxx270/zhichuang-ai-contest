"""晨读评测：用真实公开研报 + 人工标注的金标准，报告立场识别准确率与追问命中率。
    python eval_morning.py            # mock（规则引擎）
    LLM_API_KEY=... python eval_morning.py   # 接公司 AI 平台后对比
金标准在 data/eval/morning_gold.csv（doc_id, symbol, stance）和 data/eval/ask_gold.csv（question, expect_publisher, expect_keyword）。
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

from yanban import config
from yanban.docs import load_docs
from yanban.llm import LLM
from yanban.skills.morning import ask, extract_points

ROOT = Path(__file__).parent
REAL = ROOT / "data" / "real_docs"
GOLD = ROOT / "data" / "eval" / "morning_gold.csv"
ASK = ROOT / "data" / "eval" / "ask_gold.csv"


def main() -> int:
    llm = LLM()
    docs = {d.id: d for d in load_docs(REAL)}
    gold = list(csv.DictReader(GOLD.open(encoding="utf-8")))
    print(f"{config.PRODUCT_NAME} 晨读评测 ｜ 引擎：{llm.mode} ｜ 材料 {len(docs)} 份 ｜ 标注 {len(gold)} 条\n")

    # ---- 立场识别 ----
    hit = strict = 0
    wrong = []
    for row in gold:
        d = docs.get(row["doc_id"])
        if not d:
            wrong.append((row["doc_id"], row["symbol"], row["stance"], "材料缺失"))
            continue
        pts, _ = extract_points(d, [row["symbol"]], llm)
        got = pts[0].stance if pts else "未明确"
        exp = row["stance"]
        ok_strict = got == exp
        # 宽松口径：多空方向不错就算对（中性 vs 未明确、中性 vs 谨慎偏多 不算严重错误；偏多 vs 偏空 才算反向）
        ok_loose = ok_strict or {got, exp} <= {"中性", "未明确"} or ("偏多" not in {got, exp} or "偏空" not in {got, exp})
        strict += ok_strict
        hit += ok_loose
        if not ok_strict:
            wrong.append((d.publisher, row["symbol"], exp, got))
    n = len(gold)
    print(f"立场识别：严格准确率 {strict}/{n} = {strict / n:.0%}；方向不反准确率 {hit}/{n} = {hit / n:.0%}")
    for w in wrong:
        print(f"  ✗ {w[0]} {w[1]}：标注 {w[2]} → 识别 {w[3]}")

    # ---- 追问命中 ----
    qs = list(csv.DictReader(ASK.open(encoding="utf-8")))
    q_hit = 0
    for q in qs:
        _, hits = ask(q["question"], list(docs.values()), llm)
        ok = any(q["expect_publisher"] in src and q["expect_keyword"] in s for src, s in hits[:2])
        q_hit += ok
        print(f"  {'✓' if ok else '✗'} {q['question']} → {hits[0][0] if hits else '无'}")
    print(f"追问命中率（前 2 条含期望来源与关键词）：{q_hit}/{len(qs)} = {q_hit / len(qs):.0%}")
    return 0 if strict / n >= 0.8 else 1


if __name__ == "__main__":
    sys.exit(main())
