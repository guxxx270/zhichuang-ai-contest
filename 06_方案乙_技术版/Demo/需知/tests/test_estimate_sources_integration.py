"""多来源参考独立保存；不得替代、混入或锚定模型估时。"""
import json

import pytest

from test_intake_requirements import EQUITY_REQUIREMENT
from test_llm_estimate import FakeLLM
from xuzhi import knowledge
from xuzhi.ledger import Ledger, estimate_snapshot
from xuzhi.llm import LLM
from xuzhi.pipeline import analyze
from xuzhi.pipeline.estimate import estimate
from xuzhi.pipeline.intake import extract_card
from xuzhi.service import to_dict


@pytest.mark.parametrize("state", ["unavailable", "failed", "ready"])
def test_rule_reference_survives_each_model_state_without_becoming_model_totals(state, monkeypatch):
    monkeypatch.setattr(knowledge, "history", lambda: [])
    model = None if state == "unavailable" else FakeLLM(error=RuntimeError("test") if state == "failed" else None)
    result = estimate(extract_card(EQUITY_REQUIREMENT), EQUITY_REQUIREMENT, [], llm=model)

    assert result.status == state
    rule = result.references["rule"]
    assert rule["status"] == "ready" and rule["method"] == "incremental"
    assert rule["before"]["mid"] == 15 and rule["after"]["mid"] == 8.5
    assert result.references["history"]["status"] == "unavailable"
    snapshot = estimate_snapshot(result)
    assert snapshot["references"] == result.references
    if state == "ready":
        assert snapshot["before"]["mid"] == 9
        assert snapshot["after"]["mid"] == 6
    else:
        assert snapshot["before"]["mid"] is None and snapshot["after"]["mid"] is None
        assert snapshot["tasks"] == []
    if model:
        payload = model.calls[0]["payload"]
        assert not {"references", "rule", "history", "fixed_hours"} & payload.keys()


def test_api_ledger_and_reestimate_keep_independent_sources_out_of_model_dashboard(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge, "history", lambda: [])
    analysis = analyze(EQUITY_REQUIREMENT, llm=LLM(mode="mock"))
    ledger = Ledger(tmp_path / "references.sqlite3")
    try:
        req_id = ledger.log_analysis(analysis)
        public = to_dict(analysis)["estimate"]
        original = json.loads(ledger.get(req_id)["payload"])["estimate"]
        assert public["references"] == original["references"] == analysis.estimate.references
        assert public["before"]["mid"] is None
        assert ledger.dashboard()["estimated_count"] == 0
        assert ledger.dashboard()["trad_days"] is None

        analysis.estimate = estimate(analysis.card, analysis.redacted_text, analysis.questions, llm=FakeLLM())
        ledger.update_estimate(req_id, analysis.estimate)
        ready = json.loads(ledger.get(req_id)["payload"])["estimate"]
        assert ledger.dashboard()["trad_days"] == pytest.approx(9 / 8)
        assert ledger.dashboard()["ai_days"] == pytest.approx(6 / 8)

        analysis.estimate = estimate(analysis.card, analysis.redacted_text, analysis.questions,
                                     llm=FakeLLM(error=RuntimeError("test")))
        ledger.update_estimate(req_id, analysis.estimate)
        current = json.loads(ledger.get(req_id)["payload"])["estimate"]
        assert current["status"] == "failed" and current["before"]["mid"] is None
        assert current["references"] == original["references"]
        assert to_dict(analysis)["estimate"]["references"] == current["references"]
        assert ledger.dashboard()["estimated_count"] == 0
        assert ledger.dashboard()["trad_days"] is None
        events = [json.loads(event["detail"]) for event in ledger.events_of(req_id) if event["action"] == "估量重算"]
        assert events[-1]["previous"] == ready
        assert events[-1]["current"] == current
    finally:
        ledger.conn.close()
