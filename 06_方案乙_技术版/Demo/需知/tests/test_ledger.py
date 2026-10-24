"""一本账：状态流转、对账回写历史需求库（估算飞轮）、看板汇总、审计导出。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from xuzhi import config, knowledge
from xuzhi.ledger import STATUSES, Ledger
from xuzhi.llm import LLM
from xuzhi.pipeline import analyze
from xuzhi.pipeline.estimate import Estimate
from xuzhi.pipeline.tasks import AI_REVIEW, Task
from xuzhi.service import to_dict


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
    # 这是财务汇总测试的已知输入，不把 mock 流水线产生的“未估时”冒充预测。
    a1.estimate = _known_estimate(4, 2)
    a2.estimate = _known_estimate(6, 4)
    return a1, ledger.log_analysis(a1), a2, ledger.log_analysis(a2)


def _known_estimate(before=4.0, after=2.0):
    return Estimate(
        method="llm", status="ready", model="test-fixture", basis="code_excerpt", code_files=["R1/report.py"],
        summary="测试夹具：用于验证汇总和序列化，不是模型实测结果", confidence="低",
        mid=before, low=before / 2, high=before * 2,
        ai_mid=after, ai_low=after / 2, ai_high=after * 2,
        saving_pct=100 * (1 - after / before), assumptions=["测试固定输入"],
        tasks=[Task(
            "T1", "核对报送计算", "开发", AI_REVIEW, 1, after / before, "测试任务",
            trad_days=before, ai_days=after, trad_low_hours=before * 4, trad_high_hours=before * 16,
            ai_low_hours=after * 4, ai_high_hours=after * 16,
            before_reason="以前人工核对调用链", after_reason="现在模型辅助定位，保留人工复核",
            evidence=[{"file": "R1/report.py", "quote": "calculate_exposure"}],
        )],
    )


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
    assert d["estimated_count"] == 2 and d["unestimated_count"] == 0 and d["legacy_count"] == 0
    assert (d["trad_days"], d["ai_days"], d["saved_days"], d["saving_pct"]) == (10, 6, 4, 40)
    assert d["by_status"]["已确认"] == 1 and d["by_status"]["已对账"] == 1
    assert d["avg_rounds"] == 1.5 and d["thumbs_up"] == 1
    assert d["saved_days"] == pytest.approx(d["trad_days"] - d["ai_days"], abs=0.11) and d["saving_pct"] > 0
    assert d["closed"] == d["comparable_count"] == 1 and d["mape_trad"] == 0 and d["mape_ai"] is None
    rows = ledger.export_rows()
    assert len(rows) == 2 and set(rows[0]) >= {"id", "dept", "status", "mid_days", "ai_days", "actual_days"}
    assert set(STATUSES) == {"受理", "已确认", "开发中", "已交付", "已对账"}


def test_estimate_snapshot_and_public_json_keep_both_reasons_and_evidence(ledger):
    analysis, req_id, _, _ = _two(ledger)
    snapshot = json.loads(ledger.get(req_id)["payload"])["estimate"]
    public = to_dict(analysis)["estimate"]

    for result in (snapshot, public):
        assert result["method"] == "llm" and result["status"] == "ready"
        assert result["basis"] == "code_excerpt" and result["code_files"] == ["R1/report.py"]
        assert result["before"] == {"label": "以前", "unit": "小时", "low": 16, "mid": 32, "high": 64}
        assert result["after"] == {"label": "现在", "unit": "小时", "low": 8, "mid": 16, "high": 32}
        task = result["tasks"][0]
        assert task["before_reason"] == "以前人工核对调用链"
        assert task["after_reason"] == "现在模型辅助定位，保留人工复核"
        assert task["evidence"] == [{"file": "R1/report.py", "quote": "calculate_exposure"}]
        assert task["before"]["mid"] == 32 and task["after"]["mid"] == 16


def test_reestimate_updates_current_row_and_keeps_previous_snapshot(ledger):
    _, req_id, _, other_id = _two(ledger)
    other_before = ledger.get(other_id)
    first_snapshot = json.loads(ledger.get(req_id)["payload"])
    ledger.update_estimate(req_id, _known_estimate(3, 1.5))
    assert ledger.get(req_id)["mid_days"] == 3 and ledger.get(req_id)["ai_days"] == 1.5

    failed = Estimate(status="failed", error="模型请求失败")
    ledger.update_estimate(req_id, failed)

    row = ledger.get(req_id)
    assert row["mid_days"] is None and row["ai_days"] is None and row["confidence"] == "未估时"
    current = json.loads(row["payload"])
    assert current["card"] == first_snapshot["card"]
    assert current["estimate"]["status"] == "failed" and current["estimate"]["tasks"] == []
    assert current["estimate"]["before"]["mid"] is None
    events = [json.loads(event["detail"]) for event in ledger.events_of(req_id) if event["action"] == "估量重算"]
    assert events[0]["previous"] == first_snapshot["estimate"]
    assert events[-1]["previous"]["mid"] == 3 and events[-1]["current"]["mid"] is None
    assert ledger.get(other_id) == other_before
    dashboard = ledger.dashboard()
    assert dashboard["estimated_count"] == 1 and dashboard["unestimated_count"] == 1
    assert dashboard["trad_days"] == 6 and dashboard["ai_days"] == 4


def test_actual_time_without_estimate_is_saved_without_fake_deviation_or_history(ledger):
    analysis = analyze("现有报表需要调整收益口径", llm=LLM(mode="mock"))
    req_id = ledger.log_analysis(analysis)
    before = len(knowledge.history())

    result = ledger.reconcile(req_id, actual_days=2, ai_assisted=True)

    assert result == {"estimate": None, "actual": 2, "deviation": None, "history_id": ""}
    assert ledger.get(req_id)["actual_days"] == 2 and ledger.get(req_id)["status"] == "已对账"
    assert len(knowledge.history()) == before
    dashboard = ledger.dashboard()
    assert dashboard["closed"] == 1 and dashboard["mape"] is None
    assert dashboard["comparable_count"] == 0
    assert dashboard["estimated_count"] == 0 and dashboard["unestimated_count"] == 1
    assert all(dashboard[key] is None for key in ("trad_days", "ai_days", "saved_days", "saving_pct"))


def test_legacy_rule_estimate_is_preserved_but_excluded_from_model_forecast(ledger):
    _, req_id, _, _ = _two(ledger)
    row = ledger.get(req_id)
    payload = json.loads(row["payload"])
    payload["estimate"] = {"mid": 4, "ai_mid": 2, "method": "incremental"}
    encoded = json.dumps(payload, ensure_ascii=False)
    ledger.conn.execute("UPDATE requirements SET payload=? WHERE id=?", (encoded, req_id))
    ledger.conn.commit()

    dashboard = ledger.dashboard()

    assert dashboard["estimated_count"] == 1 and dashboard["legacy_count"] == 1
    assert dashboard["trad_days"] == 6 and dashboard["ai_days"] == 4
    assert ledger.get(req_id)["payload"] == encoded
