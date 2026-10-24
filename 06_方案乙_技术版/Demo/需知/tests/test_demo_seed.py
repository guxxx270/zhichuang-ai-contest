"""演示数据预置：备份 → 清空 → 预置；不碰样例 ① 的估算与资管运营的追问记忆，不伪造模型调用审计。"""
from __future__ import annotations

import pytest

from tools import demo_seed
from xuzhi import config, knowledge
from xuzhi.audit import Audit
from xuzhi.ledger import Ledger
from xuzhi.llm import LLM
from xuzhi.memory import Memory
from xuzhi.pipeline import analyze
from xuzhi.reminders import build_digest


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LEDGER_DB", tmp_path / "ledger.sqlite3")
    monkeypatch.setattr(config, "HISTORY_LEARNED_PATH", tmp_path / "history_learned.json")
    knowledge.history.cache_clear()
    yield tmp_path
    knowledge.history.cache_clear()


def test_seed_fills_dashboard_without_touching_demo(tmp_db):
    sample1 = next(config.SAMPLES_DIR.glob("01_*.md")).read_text(encoding="utf-8")
    before = analyze(sample1, llm=LLM(mode="mock")).estimate.mid
    Ledger().event(0, "旧数据", "")                      # 旧库里有东西 → 先备份
    assert demo_seed.main([]) == 0
    assert list((tmp_db / "backup").glob("*/ledger.sqlite3"))
    d = Ledger().dashboard()
    assert d["count"] == len(demo_seed.PLAN) and d["depts"] >= 8 and all(d["by_status"].values())
    assert d["closed"] >= 5 and d["mape"] is None and d["avg_rounds"] > 1
    assert d["saved_days"] is None and d["estimated_count"] == 0
    assert d["unestimated_count"] == len(demo_seed.PLAN)
    assert "未注明" not in d["by_dept"]
    assert Audit().stats()["calls"] == 0                 # 审计只记真实调用
    assert not config.HISTORY_LEARNED_PATH.exists()      # 对账不写回历史库
    assert analyze(sample1, llm=LLM(mode="mock")).estimate.mid is before is None
    mem = Memory()
    assert mem.stats()["entries"] > 0 and not [r for r in mem.rows() if r["scope_key"].startswith("资管运营")]
    a = analyze(sample1, llm=LLM(mode="mock"), memory=mem)
    assert not [q for q in a.questions if q.recalled]   # 现场开工的样例 ① 是全新一条
    dg = build_digest(Ledger())
    assert dg.stale_confirm and dg.stale_reconcile and dg.week_total
    # 再跑一次：清掉重来，条数不翻倍
    assert demo_seed.main([]) == 0 and Ledger().dashboard()["count"] == len(demo_seed.PLAN)
    assert demo_seed.main(["--empty"]) == 0 and Ledger().dashboard()["count"] == 0
