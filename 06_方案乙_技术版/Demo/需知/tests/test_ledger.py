"""一本账：状态流转、对账回写历史需求库（估算飞轮）、看板汇总、审计导出。"""
from __future__ import annotations

from pathlib import Path

import pytest

from xuzhi import config, knowledge
from xuzhi.ledger import STATUSES, Ledger
from xuzhi.llm import LLM
from xuzhi.pipeline import analyze


@pytest.fixture
def ledger(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(config, "HISTORY_LEARNED_PATH", tmp_path / "history_learned.json")
    knowledge.history.cache_clear()
    yield Ledger(tmp_path / "ledger.sqlite3")
    knowledge.history.cache_clear()


def _two(ledger: Ledger):
    llm = LLM(mode="mock")
    ps = sorted(config.SAMPLES_DIR.glob("*.md"))
    a1 = analyze(ps[0].read_text(encoding="utf-8"), llm=llm)
    a2 = analyze(ps[2].read_text(encoding="utf-8"), llm=llm)
    return a1, ledger.log_analysis(a1), a2, ledger.log_analysis(a2)


def test_status_and_reconcile_write_back(ledger: Ledger):
    a1, r1, a2, r2 = _two(ledger)
    assert ledger.get(r1)["status"] == "受理" and ledger.get(r1)["dept"]
    ledger.set_status(r1, "开发中")
    assert ledger.get(r1)["status"] == "开发中"
    with pytest.raises(ValueError):
        ledger.set_status(r1, "随便")
    before = len(knowledge.history())
    res = ledger.reconcile(r1, actual_days=a1.estimate.mid * 1.25, ai_assisted=True, ai_share=0.6, note="多做了客户版")
    assert res["actual"] > res["estimate"] and res["history_id"].startswith("L")
    row = ledger.get(r1)
    assert row["status"] == "已对账" and row["actual_days"] and row["ai_assisted"] == 1
    hist = knowledge.history()
    assert len(hist) == before + 1
    added = next(h for h in hist if h.id == res["history_id"])
    assert added.actual_days == pytest.approx(res["actual"]) and added.ai_assisted and added.keywords
    # 再对账一次不重复追加
    ledger.reconcile(r1, actual_days=a1.estimate.mid, ai_assisted=False)
    assert len(knowledge.history()) == before + 1
    assert any(e["action"] == "对账" for e in ledger.events_of(r1))


def test_dashboard_aggregates(ledger: Ledger):
    assert ledger.dashboard()["count"] == 0
    a1, r1, a2, r2 = _two(ledger)
    ledger.event(r1, "业务答复重算", "2 条")
    ledger.update_answers(r1, 2, 1)
    ledger.feedback(r1, "整体", 1)
    ledger.reconcile(r2, actual_days=a2.estimate.mid, write_history=False)
    d = ledger.dashboard()
    assert d["count"] == 2 and sum(d["by_dept"].values()) == 2 and sum(d["by_type"].values()) == 2
    assert d["by_status"]["已确认"] == 1 and d["by_status"]["已对账"] == 1
    assert d["avg_rounds"] == 1.5 and d["thumbs_up"] == 1
    assert d["saved_days"] == pytest.approx(d["trad_days"] - d["ai_days"], abs=0.11) and d["saving_pct"] > 0
    assert d["closed"] == 1 and d["mape_trad"] == 0 and d["mape_ai"] is None
    rows = ledger.export_rows()
    assert len(rows) == 2 and set(rows[0]) >= {"id", "dept", "status", "mid_days", "ai_days", "actual_days"}
    assert set(STATUSES) == {"受理", "已确认", "开发中", "已交付", "已对账"}
