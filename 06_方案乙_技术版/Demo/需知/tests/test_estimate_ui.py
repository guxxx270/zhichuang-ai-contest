"""估量页面契约：实际按钮、双轨展示、证据，以及同一条台账的成功/失败更新。"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from test_intake_requirements import EQUITY_REQUIREMENT
from test_llm_estimate import CODE_QUOTE, FakeLLM, _code_response, _repo
from xuzhi import config
from xuzhi.ledger import Ledger
from xuzhi.llm import LLM


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
BEFORE = "以前（人工开发）"
AFTER = "现在（AI协同）"


def _by_label(elements, label):
    matches = [element for element in elements if element.label == label]
    assert len(matches) == 1, f"应只有一个控件：{label}"
    return matches[0]


def _run(app):
    app.run()
    assert not app.exception, [error.message for error in app.exception]
    return app


def _estimate_tab(app):
    return next(tab for tab in app.tabs if tab.label.endswith("估量"))


def _visible_text(block):
    values = []
    for kind in ("markdown", "caption", "text", "code", "info", "warning", "error"):
        values.extend(str(element.value) for element in getattr(block, kind))
    values.extend(str(metric.value) for metric in block.metric)
    return "\n".join(values)


def _start_without_model(app):
    _by_label(app.text_area, "原话").input(EQUITY_REQUIREMENT)
    _by_label(app.button, "需知，开工").click()
    _run(app)
    assert app.session_state["analysis"].estimate.status == "unavailable"


def _configure_fake_model(app):
    # 自定义来源不拉远端模型列表；所有 API 客户端也已由 fixture 替换为 fake。
    custom = next(option for option in _by_label(app.selectbox, "模型来源").options if "自定义" in option)
    _by_label(app.selectbox, "模型来源").select(custom)
    _run(app)
    _by_label(app.text_input, "API Key").input("ui-test-placeholder")
    _by_label(app.text_input, "API Base URL").input("https://example.invalid/v1")
    _by_label(app.text_input, "模型 ID").input("test-estimator")
    _run(app)


def _analyze_estimate(app, label="大模型分析估时"):
    _by_label(_estimate_tab(app).button, label).click()
    _run(app)


@pytest.fixture
def ui(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LEDGER_DB", tmp_path / "estimate-ui.sqlite3")
    monkeypatch.setattr(config, "HISTORY_LEARNED_PATH", tmp_path / "history_learned.json")
    fake = FakeLLM()

    def client_factory(*args, **kwargs):
        if kwargs.get("mode") == "api":
            return fake
        return LLM(*args, **kwargs)

    monkeypatch.setattr("xuzhi.llm.LLM", client_factory)
    st.cache_resource.clear()
    app = _run(AppTest.from_file(str(APP_PATH), default_timeout=20))
    yield SimpleNamespace(app=app, fake=fake)
    st.cache_resource.clear()


def test_no_model_keeps_model_unestimated_and_shows_separate_rule_reference(ui):
    _start_without_model(ui.app)
    tab = _estimate_tab(ui.app)

    assert any("尚无模型估时" in message.value for message in tab.info)
    assert not tab.metric, "规则参考不能冒充模型的工时指标"
    reference = _by_label(tab.expander, "规则模板参考 · 公式、任务与前提")
    assert "8.5" in _visible_text(reference)
    assert "15" in _visible_text(reference)
    assert "不是模型输出" in _visible_text(reference)
    assert any("模板工作项" in frame.value.columns for frame in reference.dataframe)
    assert "不自动加权混合" in _visible_text(tab)
    assert ui.fake.calls == []


def test_stale_primary_rule_total_is_not_treated_as_model_result(ui):
    _start_without_model(ui.app)
    analysis = ui.app.session_state["analysis"]
    analysis.estimate.method = "incremental"
    analysis.estimate.status = "ready"
    for field in ("low", "mid", "high", "ai_low", "ai_mid", "ai_high"):
        setattr(analysis.estimate, field, 9999 / 8)
    ui.app.session_state["analysis"] = analysis
    _run(ui.app)
    tab = _estimate_tab(ui.app)

    assert any("尚无独立模型结果" in message.value for message in tab.info)
    assert not tab.metric
    assert "9999" not in _visible_text(tab)
    assert "8.5" in _visible_text(_by_label(tab.expander, "规则模板参考 · 公式、任务与前提"))


def test_independent_button_shows_two_ranges_and_rough_basis_without_new_ledger_row(ui):
    app = ui.app
    _start_without_model(app)
    req_id = app.session_state["req_id"]
    ledger = Ledger()
    try:
        count_before = ledger.stats()["count"]
        raw_before = app.session_state["analysis"].raw_text
        _configure_fake_model(app)
        _analyze_estimate(app)
        tab = _estimate_tab(app)

        assert _by_label(tab.metric, BEFORE).value == "4～15 小时"
        assert _by_label(tab.metric, AFTER).value == "2.5～10.5 小时"
        comparison_columns = [column for column in tab.columns if any(m.label in (BEFORE, AFTER) for m in column.metric)]
        assert len(comparison_columns) == 2
        assert [column.metric[0].label for column in comparison_columns] == [BEFORE, AFTER]
        assert any("参考投入：9 小时" in caption.value for caption in tab.caption)
        assert any("参考投入：6 小时" in caption.value for caption in tab.caption)
        assert any("粗估" in message.value for message in tab.warning)
        assert any("test-estimator" in caption.value for caption in tab.caption)
        task_table = next(frame.value for frame in tab.dataframe if "工作项" in frame.value.columns)
        assert len(task_table) == 3
        assert {"以前的依据", "现在的依据"} <= set(task_table.columns)
        assert app.session_state["req_id"] == req_id
        assert app.session_state["analysis"].raw_text == raw_before
        assert ledger.stats()["count"] == count_before
        snapshot = json.loads(ledger.get(req_id)["payload"])["estimate"]
        assert snapshot["status"] == "ready" and snapshot["method"] == "llm"
        assert snapshot["references"]["rule"]["after"]["mid"] == 8.5
        assert ledger.get(req_id)["mid_days"] == pytest.approx(9 / 8)
        assert ledger.get(req_id)["ai_days"] == pytest.approx(6 / 8)
        assert len(ui.fake.calls) == 1, "独立估时按钮只调用估时模型，不重跑问清/写单"
    finally:
        ledger.conn.close()


def test_code_material_scope_and_exact_task_evidence_are_visible(ui):
    app = ui.app
    _start_without_model(app)
    # 模拟该条已完成材料读取；不通过网络下载仓库，也不绕过估时按钮。
    analysis = app.session_state["analysis"]
    analysis.repos = [_repo()]
    app.session_state["analysis"] = analysis
    ui.fake.response = _code_response
    _configure_fake_model(app)
    _analyze_estimate(app)
    tab = _estimate_tab(app)

    assert any("已参考所提供的代码摘录" in message.value for message in tab.info)
    assert not any("粗估" in message.value for message in tab.warning)
    scope = _by_label(tab.expander, "估算前提与材料范围")
    assert any("src/exposure.py" in block.value for block in scope.code)
    evidence = next(expander for expander in tab.expander if "修改计算与回归样本" in expander.label)
    assert any(block.value == CODE_QUOTE for block in evidence.code)
    assert any("code" in caption.value and "src/exposure.py" in caption.value for caption in evidence.caption)


def test_failed_reestimate_removes_old_ui_numbers_and_old_ledger_totals(ui):
    app = ui.app
    _start_without_model(app)
    _configure_fake_model(app)
    _analyze_estimate(app)
    assert _by_label(_estimate_tab(app).metric, BEFORE).value == "4～15 小时"
    req_id = app.session_state["req_id"]
    ledger = Ledger()
    try:
        count_before = ledger.stats()["count"]
        ui.fake.error = RuntimeError("simulated model failure")
        _analyze_estimate(app, "重新分析估时")
        tab = _estimate_tab(app)

        assert any("本次模型估时未成功" in message.value for message in tab.error)
        assert not tab.metric
        assert "4～15 小时" not in _visible_text(tab)
        assert "2.5～10.5 小时" not in _visible_text(tab)
        assert not any("工作项" in frame.value.columns for frame in tab.dataframe)
        assert app.session_state["req_id"] == req_id
        assert ledger.stats()["count"] == count_before
        row = ledger.get(req_id)
        snapshot = json.loads(row["payload"])["estimate"]
        assert row["mid_days"] is None and row["ai_days"] is None
        assert snapshot["status"] == "failed" and snapshot["tasks"] == []
        assert snapshot["references"]["rule"]["after"]["mid"] == 8.5
        assert "8.5" in _visible_text(_by_label(tab.expander, "规则模板参考 · 公式、任务与前提"))
        assert all(snapshot[field] is None for field in ("mid", "low", "high", "ai_mid", "ai_low", "ai_high"))
        assert len(ui.fake.calls) == 2
    finally:
        ledger.conn.close()
