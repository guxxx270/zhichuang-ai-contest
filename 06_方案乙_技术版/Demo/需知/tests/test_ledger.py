"""一本账：状态流转、对账回写历史记录参考、看板汇总、审计导出。"""
from __future__ import annotations

import importlib
import json
import threading
from pathlib import Path

import pytest

from test_jira import SAMPLE, STORY
from xuzhi import config, knowledge
from xuzhi.jira import parse_export, parse_issue_text
from xuzhi.ledger import STATUSES, Ledger
from xuzhi.llm import LLM
from xuzhi.pipeline import analyze
from xuzhi.pipeline.assess import assess, assess_many, calibrate
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


def _sample_jira(key: str):
    results, _ = assess_many(parse_export(SAMPLE.read_bytes(), SAMPLE.name))
    return next(a for a in results if a.issue.key == key)


def _jira(text: str = STORY):
    return assess(parse_issue_text(text), calibrate([]))


def _learned() -> dict:
    return {item["id"]: item for item in json.loads(config.HISTORY_LEARNED_PATH.read_text(encoding="utf-8"))["items"]}


# ---------------- 看板口径 ----------------
def test_empty_dashboard_has_the_same_keys_as_a_full_one(ledger):
    empty = ledger.dashboard()
    _two(ledger)
    assert set(empty) == set(ledger.dashboard())


def test_reconciled_jira_rows_are_not_legacy_and_get_their_own_deviation(ledger):
    rid, _ = ledger.log_jira(_sample_jira("XZ-1005"), "导出")    # Jira 里已完成：按耗费时间直接对账
    row = ledger.get(rid)

    d = ledger.dashboard()

    assert row["status"] == "已对账" and d["closed"] == 1 and d["jira_closed"] == 1
    assert d["legacy_count"] == 0 and d["unestimated_count"] == 0 and d["estimated_count"] == 0
    assert d["mape"] is None and d["comparable_count"] == 0
    assert d["jira_mape"] == round(100 * abs(row["mid_days"] - row["actual_days"]) / row["actual_days"])


def test_rule_template_deviation_is_reported_apart_from_the_model_deviation(ledger):
    analysis = analyze(sorted(config.SAMPLES_DIR.glob("*.md"))[0].read_text(encoding="utf-8"), llm=LLM(mode="mock"))
    rule = analysis.estimate.references["rule"]
    assert analysis.estimate.status == "unavailable" and rule["status"] == "ready"
    rid = ledger.log_analysis(analysis)

    ledger.reconcile(rid, actual_days=2, write_history=False)
    d = ledger.dashboard()
    before = rule["before"]["mid"] / 8
    assert d["mape"] is None and d["comparable_count"] == 0 and d["rule_count"] == 1
    assert d["rule_mape"] == round(100 * abs(before - 2) / 2) and d["rule_bias"] == round(100 * (before - 2) / 2)

    ledger.reconcile(rid, actual_days=2, ai_assisted=True, write_history=False)   # 用了 AI 协同：比规则「现在」那一轨
    assert ledger.dashboard()["rule_mape"] == round(100 * abs(rule["after"]["mid"] / 8 - 2) / 2)


def test_history_learned_path_follows_the_ledger_db(tmp_path, monkeypatch):
    """换了一本账的位置，对账回写跟着走；对它跑重置也就不会删掉别的库的历史回写。"""
    saved = dict(vars(config))
    try:
        import dotenv
        monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)   # 不读本机 .env
    except ImportError:
        pass
    monkeypatch.setenv("XUZHI_LEDGER_DB", str(tmp_path / "elsewhere" / "ledger.sqlite3"))
    monkeypatch.delenv("XUZHI_HISTORY_LEARNED", raising=False)
    try:
        importlib.reload(config)
        assert config.HISTORY_LEARNED_PATH == tmp_path / "elsewhere" / "history_learned.json"
        monkeypatch.setenv("XUZHI_HISTORY_LEARNED", str(tmp_path / "learned.json"))
        importlib.reload(config)
        assert config.HISTORY_LEARNED_PATH == tmp_path / "learned.json"
    finally:
        vars(config).update(saved)   # 恢复 conftest 设好的临时库与 mock 配置


# ---------------- 对账与状态 ----------------
def test_moving_a_reconciled_row_back_undoes_the_reconcile(ledger):
    _, r1, _, _ = _two(ledger)
    hid = ledger.reconcile(r1, actual_days=5, ai_assisted=True, ai_share=0.6)["history_id"]
    assert ledger.dashboard()["closed"] == 1 and hid in _learned()

    ledger.set_status(r1, "开发中")

    row = ledger.get(r1)
    assert row["status"] == "开发中" and row["actual_days"] is None and row["closed_ts"] is None
    assert row["ai_assisted"] is None and row["ai_share"] is None
    d = ledger.dashboard()
    assert d["closed"] == 0 and d["by_status"]["已对账"] == 0 and d["comparable_count"] == 0
    assert hid not in _learned() and all(h.id != hid for h in knowledge.history())
    assert [e["action"] for e in ledger.events_of(r1)][-2:] == ["撤销对账", "状态"]
    ledger.set_status(r1, "已交付")                     # 不是从已对账改出来的：只改状态
    assert [e["action"] for e in ledger.events_of(r1)][-2:] == ["状态", "状态"]


@pytest.mark.parametrize("share", [7.5, -0.1, float("nan")])
def test_reconcile_rejects_an_ai_share_outside_zero_to_one(ledger, share):
    _, r1, _, _ = _two(ledger)
    with pytest.raises(ValueError):
        ledger.reconcile(r1, actual_days=3, ai_assisted=True, ai_share=share)
    assert ledger.get(r1)["actual_days"] is None and ledger.get(r1)["status"] == "受理"


def test_history_estimate_uses_the_track_that_matches_the_actual(ledger):
    _, r1, _, _ = _two(ledger)                          # 传统 4 人天、AI 协同 2 人天
    hid = ledger.reconcile(r1, actual_days=3, ai_assisted=True, ai_share=0.5)["history_id"]
    item = _learned()[hid]
    assert item["estimate_days"] == ledger.get(r1)["ai_days"] == 2
    assert (item["estimate_trad_days"], item["estimate_ai_days"]) == (4, 2)
    ledger.reconcile(r1, actual_days=3)
    assert _learned()[hid]["estimate_days"] == 4


def test_unchanged_unestimated_recompute_does_not_flood_the_event_log(ledger):
    analysis = analyze("现有报表需要调整收益口径", llm=LLM(mode="mock"))
    rid = ledger.log_analysis(analysis)
    for _ in range(3):
        ledger.update_estimate(rid, analysis.estimate)          # 规则模式：每次答复重算都是「未估时」
    assert [e["action"] for e in ledger.events_of(rid)].count("估量重算") == 0

    ledger.update_estimate(rid, Estimate(status="failed", error="模型请求失败"))
    ledger.update_estimate(rid, Estimate(status="failed", error="模型请求失败"))
    ledger.update_estimate(rid, Estimate(status="failed", error="模型网关超时"))
    events = [json.loads(e["detail"]) for e in ledger.events_of(rid) if e["action"] == "估量重算"]
    assert [(e["previous"]["status"], e["current"]["status"]) for e in events] == [("unavailable", "failed"), ("failed", "failed")]
    assert events[-1]["current"]["error"] == "模型网关超时"


# ---------------- Jira 工单进账 ----------------
def test_reimporting_a_reconciled_jira_ticket_does_not_overwrite_it(ledger):
    rid, _ = ledger.log_jira(_sample_jira("XZ-1005"), "导出")
    keep = ("title", "payload", "mid_days", "ai_days", "coding_trad", "coding_ai", "actual_days", "status")
    before = {k: ledger.get(rid)[k] for k in keep}
    other = _jira("XZ-1005 报表导出增加备注列\n类型：故事　模块：报表平台\n描述：运营希望导出时带上备注。")

    assert ledger.log_jira(other, "粘贴") == (rid, False)

    assert {k: ledger.get(rid)[k] for k in keep} == before
    assert ledger.events_of(rid)[-1]["action"] == "Jira 重新评估（已对账，未覆盖）"


def test_ai_assisted_jira_actuals_are_not_calibration_samples(ledger):
    rid, _ = ledger.log_jira(_jira(), "粘贴")
    ledger.reconcile(rid, 2.0, ai_assisted=True, ai_share=0.5)
    assert ledger.jira_done_issues() == []
    ledger.reconcile(rid, 3.0)                          # 改回传统实际耗时
    assert [it.key for it in ledger.jira_done_issues()] == ["XZ-1024"]


def test_jira_text_is_redacted_before_it_is_stored(ledger):
    rid, _ = ledger.log_jira(_jira(STORY.replace("描述：", "描述：客户 王五（资金账号 12345678901）反馈，")), "粘贴")
    row = ledger.get(rid)
    assert "12345678901" not in row["payload"] and "王五" not in row["payload"] and "<账号_1>" in row["payload"]

    nokey = "持仓查询给客户 王五 13812345678 加字段\n类型：故事\n描述：客户要看交易日归属。"
    first, created = ledger.log_jira(_jira(nokey), "粘贴")
    again, created_again = ledger.log_jira(_jira(nokey), "粘贴")     # 没有单号：按脱敏后的摘要去重
    row = ledger.get(first)
    assert created and not created_again and again == first
    assert all("13812345678" not in row[k] and "王五" not in row[k] for k in ("title", "ext_key", "payload"))


# ---------------- 并发 ----------------
def test_one_ledger_can_be_shared_by_many_threads(ledger):
    analysis = analyze("风控部：每天结算后把各产品保证金占用汇总成一张表发给我。", llm=LLM(mode="mock"))
    errors: list[BaseException] = []

    def work():
        try:
            for _ in range(20):
                rid = ledger.log_analysis(analysis)
                ledger.event(rid, "并发测试", "")
        except BaseException as exc:   # noqa: BLE001  收集后断言，线程里的异常不会自己冒出来
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert ledger.dashboard()["count"] == 160
    assert ledger.conn.execute("SELECT COUNT(*) FROM events WHERE action='并发测试'").fetchone()[0] == 160
    assert ledger.conn.execute("SELECT COUNT(*) FROM versions").fetchone()[0] == 160
