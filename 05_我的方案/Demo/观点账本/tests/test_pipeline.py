from datetime import date
from pathlib import Path

import pytest

from ledger import config
from ledger.backtest import evaluate, evaluate_all
from ledger.extract import rule_extract, read_text_file
from ledger.market import PriceBook
from ledger.privacy import Redactor
from ledger.report import leaderboard, morning_brief, symbol_experts
from ledger.schema import OpinionCard
from ledger.store import Ledger

SAMPLES = config.DATA_DIR / "sample_reports"


def test_rule_extract_samples():
    got = {}
    for p in sorted(SAMPLES.glob("*.md")):
        cards = rule_extract(read_text_file(p), source=p.name)
        got[p.name] = {(c.analyst, c.symbol, c.direction, c.horizon_days) for c in cards}
    assert got["2026-07-01_黑色系周报_张研.md"] == {("张研", "RB", "空", 20), ("张研", "I", "震荡", 5), ("张研", "J", "空", 20)}
    assert got["2026-07-15_有色晨会纪要.md"] == {("李铜", "CU", "多", 20), ("王金", "AU", "多", 60),
                                              ("周铝", "AL", "震荡", 5), ("李铜", "NI", "空", 20)}
    assert got["2026-08-01_农产品与能源策略_赵豆.md"] == {("赵豆", "M", "多", 5), ("赵豆", "SC", "空", 20), ("赵豆", "P", "震荡", 20)}


def test_backtest_rules():
    prices = PriceBook.from_csv()
    card = OpinionCard(analyst="t", symbol="CU", direction="多", horizon_days=20, published_on=date(2026, 7, 15))
    o = evaluate(card, prices)
    assert o.hit is not None and o.end_date is not None
    far = OpinionCard(analyst="t", symbol="AU", direction="多", horizon_days=60, published_on=date(2026, 7, 15))
    assert evaluate(far, prices).hit is None  # 未到期
    unknown = OpinionCard(analyst="t", symbol="ZZ", direction="多", published_on=date(2026, 7, 15))
    assert evaluate(unknown, prices).hit is None


def test_reports_and_store(tmp_path: Path):
    ledger = Ledger(tmp_path / "t.sqlite3")
    cards = []
    for p in sorted(SAMPLES.glob("*.md")):
        cards += rule_extract(read_text_file(p), source=p.name)
    assert ledger.add(cards) == 10
    assert ledger.add(cards) == 0          # 去重
    outcomes = evaluate_all(ledger.all(), PriceBook.from_csv())
    lb = leaderboard(outcomes)
    assert set(lb["研究员"]) <= {"张研", "李铜", "周铝", "赵豆", "王金"}
    assert not symbol_experts(outcomes).empty
    brief = morning_brief(outcomes)
    assert "研判晨报" in brief and "胜率" in brief


def test_privacy_roundtrip():
    text = "客户：王建国先生（手机 13912345678，资金账号 8812345678901）浮亏 1,250,000 元；邮箱 wjg@example.com"
    r = Redactor().redact(text)
    assert "13912345678" not in r.text and "8812345678901" not in r.text and "王建国" not in r.text
    assert "<手机_1>" in r.text and "<客户_1>" in r.text
    assert Redactor.restore(r.text, r.mapping) == text
