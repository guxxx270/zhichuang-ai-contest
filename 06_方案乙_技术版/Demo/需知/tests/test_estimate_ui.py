"""估量页面契约：实际按钮、双轨展示、证据，以及同一条台账的成功/失败更新。"""
from __future__ import annotations

import json
import re
from collections import deque
from pathlib import Path
from threading import Event, Lock, local
from types import SimpleNamespace

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from test_intake_requirements import EQUITY_REQUIREMENT
from test_llm_estimate import CODE_QUOTE, FakeLLM, _code_response, _repo, _response
from xuzhi import config
from xuzhi.ledger import Ledger
from xuzhi.llm import LLM


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
BEFORE = "以前（人工开发）"
AFTER = "现在（AI协同）"
# st.status 只会用这三种图标；AppTest 把带 icon 的 st.expander（如「模型与参考材料（可选）」）也归进 app.status。
STATUS_ICONS = {"spinner", ":material/check:", ":material/error:"}


class CallGate:
    """由测试显式放行模型调用；不通过 sleep 猜测后台执行时序。"""

    def __init__(self):
        self.entered = Event()
        self.release = Event()
        self.exited = Event()


class ControlledLLM(FakeLLM):
    note = ""

    def __init__(self):
        super().__init__()
        self._thread = local()
        self._lock = Lock()
        self._pending_gates = deque()
        self.gates = []
        self.purposes = []

    @property
    def current_purpose(self):
        return getattr(self._thread, "purpose", "")

    @current_purpose.setter
    def current_purpose(self, purpose):
        self._thread.purpose = purpose

    def block_next_call(self):
        gate = CallGate()
        with self._lock:
            self._pending_gates.append(gate)
            self.gates.append(gate)
        return gate

    def _enter_call(self):
        with self._lock:
            self.purposes.append(self.current_purpose)
            gate = self._pending_gates.popleft() if self._pending_gates else None
        if gate:
            gate.entered.set()
            try:
                assert gate.release.wait(10), "测试必须显式释放模型调用，不能同步阻塞整页"
            finally:
                gate.exited.set()

    def chat_json(self, system, user):
        self._enter_call()
        if self.current_purpose == "估时分析":
            return super().chat_json(system, user)
        if self.error:
            raise self.error
        return {}

    def chat(self, system, user, **kwargs):
        self._enter_call()
        if self.error:
            raise self.error
        return ""


def _by_label(elements, label):
    matches = [element for element in elements if element.label == label]
    assert len(matches) == 1, f"应只有一个控件：{label}"
    return matches[0]


def _model_statuses(app):
    """只取真正的 st.status 状态块，不把带图标的设置折叠区当成模型状态。"""
    return [block for block in app.status if block.icon in STATUS_ICONS]


def _run(app):
    app.run()
    assert not app.exception, [error.message for error in app.exception]
    return app


def _state(app, key, default=None):
    return app.session_state[key] if key in app.session_state else default


def _current_job(app):
    job = _state(app, "_model_job")
    if job is not None and job not in app._test_jobs:
        app._test_jobs.append(job)
    return job


def _finish_job(app, job=None):
    job = job or _current_job(app)
    if job is not None:
        job.wait(timeout=5)
        assert job.done.is_set(), "后台模型任务应已完成"
    # AppTest 不会自动触发 fragment 的计时器；显式 rerun 等价于一次轮询。
    return _run(app)


def _estimate_tab(app):
    return next(tab for tab in app.tabs if tab.label.endswith("估量"))


def _assert_fast_results_visible(app):
    analysis = app.session_state["analysis"]
    tab = _estimate_tab(app)
    reference = _by_label(tab.expander, "规则模板参考 · 六维复杂度、公式与任务")
    assert "8.5" in _visible_text(reference)
    assert _by_label(tab.expander, "历史记录参考 · 当时估算与实际工时") is not None
    spec_tab = next(tab for tab in app.tabs if tab.label.endswith("写单"))
    assert analysis.spec.business_md in [block.value for block in spec_tab.markdown]
    prototype_tab = next(tab for tab in app.tabs if tab.label.endswith("出样"))
    assert analysis.prototype_html.strip()
    assert prototype_tab.get("iframe") or prototype_tab.get("component_instance"), "原型已生成时应已挂载可见预览"


def _assert_running_status(app, job):
    statuses = [status for status in _model_statuses(app) if "模型正在分析" in status.label]
    assert len(statuses) == 1
    status = statuses[0]
    assert status.state == "running"
    assert job.snapshot()["stage"] in status.label
    assert "已等待" in status.label and "秒" in status.label


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


def _start_sample(app, prefix):
    box = _by_label(app.selectbox, "选一个样例（或在下面粘贴）")
    box.select(next(option for option in box.options if option.startswith(prefix)))
    _run(app)
    _by_label(app.button, "需知，开工").click()
    _run(app)


def _toasts(app):
    return [toast.value for toast in app.toast]


def _configure_fake_model(app):
    # 自定义来源不拉远端模型列表；所有 API 客户端也已由 fixture 替换为 fake。
    custom = next(option for option in _by_label(app.selectbox, "模型来源").options if "自定义" in option)
    _by_label(app.selectbox, "模型来源").select(custom)
    _run(app)
    _by_label(app.text_input, "API Key").input("ui-test-placeholder")
    _by_label(app.text_input, "API Base URL").input("https://example.invalid/v1")
    _by_label(app.text_input, "模型 ID").input("test-estimator")
    _run(app)


def _analyze_estimate(app, label="大模型分析估时", *, finish=True):
    _by_label(_estimate_tab(app).button, label).click()
    _run(app)
    job = _current_job(app)
    if finish:
        _finish_job(app, job)
    return job


@pytest.fixture
def ui(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LEDGER_DB", tmp_path / "estimate-ui.sqlite3")
    monkeypatch.setattr(config, "HISTORY_LEARNED_PATH", tmp_path / "history_learned.json")
    fake = ControlledLLM()

    def client_factory(*args, **kwargs):
        if kwargs.get("mode") == "api":
            return fake
        return LLM(*args, **kwargs)

    monkeypatch.setattr("xuzhi.llm.LLM", client_factory)
    st.cache_resource.clear()
    app = _run(AppTest.from_file(str(APP_PATH), default_timeout=20))
    app._test_jobs = []
    yield SimpleNamespace(app=app, fake=fake)
    for gate in fake.gates:
        gate.release.set()
    for job in app._test_jobs:
        job.wait(timeout=5)
    st.cache_resource.clear()


def test_no_model_keeps_model_unestimated_and_shows_separate_rule_reference(ui):
    _start_without_model(ui.app)
    tab = _estimate_tab(ui.app)

    assert "尚未调用模型" in _visible_text(ui.app)
    assert any("尚无模型估时" in message.value for message in tab.info)
    assert "模型正在分析" not in _visible_text(ui.app)
    assert not _model_statuses(ui.app)
    assert "暂无模型统计" in _visible_text(ui.app)
    assert _current_job(ui.app) is None
    assert not tab.metric, "规则参考不能冒充模型的工时指标"
    reference = _by_label(tab.expander, "规则模板参考 · 六维复杂度、公式与任务")
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
    assert "8.5" in _visible_text(_by_label(tab.expander, "规则模板参考 · 六维复杂度、公式与任务"))


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
        comparison = ui.fake.calls[0]["payload"]["comparison"]
        assert app.session_state["analysis"].estimate.comparison == comparison
        scope = _by_label(tab.expander, "估算前提与材料范围")
        scope_text = _visible_text(scope)
        assert "本次比较口径" in scope_text
        assert all(comparison[key] in scope_text for key in ("before", "after", "unit", "environment", "version"))
        task_table = next(frame.value for frame in tab.dataframe if "工作项" in frame.value.columns)
        assert len(task_table) == 3
        assert {"以前的依据", "现在的依据"} <= set(task_table.columns)
        assert app.session_state["req_id"] == req_id
        assert app.session_state["analysis"].raw_text == raw_before
        assert ledger.stats()["count"] == count_before
        snapshot = json.loads(ledger.get(req_id)["payload"])["estimate"]
        assert snapshot["status"] == "ready" and snapshot["method"] == "llm"
        assert snapshot["comparison"] == comparison
        assert snapshot["references"]["rule"]["after"]["mid"] == 8.5
        assert ledger.get(req_id)["mid_days"] == pytest.approx(9 / 8)
        assert ledger.get(req_id)["ai_days"] == pytest.approx(6 / 8)
        assert len(ui.fake.calls) == 1, "独立估时按钮只调用估时模型，不重跑问清/写单"
    finally:
        ledger.conn.close()


def test_old_estimate_without_comparison_does_not_inherit_current_conditions(ui):
    app = ui.app
    _start_without_model(app)
    _configure_fake_model(app)
    _analyze_estimate(app)
    analysis = app.session_state["analysis"]
    current_comparison = dict(analysis.estimate.comparison)
    analysis.estimate.comparison = {}
    app.session_state["analysis"] = analysis
    _run(app)

    tab = _estimate_tab(app)
    assert _by_label(tab.metric, BEFORE).value == "4～15 小时"
    assert app.session_state["analysis"].estimate.comparison == {}
    scope_text = _visible_text(_by_label(tab.expander, "估算前提与材料范围"))
    assert "本次比较口径" not in scope_text
    assert all(value not in scope_text for value in current_comparison.values())
    assert any("粗估" in message.value for message in tab.warning)
    assert len(ui.fake.calls) == 1, "显示旧估算不能自行重跑模型或改写其比较口径"


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
    # 证据来源显示中文：code → 代码，requirement → 原话，不露英文枚举
    assert any(caption.value.startswith("代码 · ") and "src/exposure.py" in caption.value for caption in evidence.caption)
    assert any(caption.value == "原话" for caption in evidence.caption)
    assert not any("code" in caption.value or "requirement" in caption.value for caption in evidence.caption)


def test_retry_without_key_clears_previous_completed_status(ui):
    app = ui.app
    _start_without_model(app)
    _configure_fake_model(app)
    _analyze_estimate(app)
    assert _state(app, "_model_last_status")["status"] == "completed"
    _by_label(app.text_input, "API Key").input("")
    _run(app)
    _analyze_estimate(app, "重新分析估时")

    assert app.session_state["analysis"].estimate.status == "unavailable"
    assert not _estimate_tab(app).metric
    assert not _model_statuses(app)
    assert _state(app, "_model_last_status") is None
    assert "模型分析已完成" not in _visible_text(app)
    assert "尚未调用模型" in _visible_text(app)
    assert len(ui.fake.calls) == 1
    _assert_fast_results_visible(app)


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
        fast_before = app.session_state["analysis"]
        spec_before = fast_before.spec.business_md
        prototype_before = fast_before.prototype_html
        ui.fake.error = RuntimeError("simulated model failure")
        gate = ui.fake.block_next_call()
        job = _analyze_estimate(app, "重新分析估时", finish=False)
        assert gate.entered.wait(3)
        assert job is not None and not job.done.is_set()
        assert "模型正在分析" in _visible_text(app)
        _assert_running_status(app, job)
        assert not _estimate_tab(app).metric, "重估中应清除上一轮模型数字"
        _assert_fast_results_visible(app)
        gate.release.set()
        _finish_job(app, job)
        tab = _estimate_tab(app)

        assert any("本次模型估时未成功" in message.value for message in tab.error)
        # 配好了模型却失败：卡片写失败原因的第一句，不再叫用户去配 Key
        error = app.session_state["analysis"].estimate.error
        assert error.split("。")[0] + "。" in [caption.value for caption in tab.caption]
        assert not any("需要配置 Key" in caption.value for caption in tab.caption)
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
        assert "8.5" in _visible_text(_by_label(tab.expander, "规则模板参考 · 六维复杂度、公式与任务"))
        assert all(snapshot[field] is None for field in ("mid", "low", "high", "ai_mid", "ai_low", "ai_high"))
        assert len(ui.fake.calls) == 2
        assert app.session_state["analysis"].spec.business_md == spec_before
        assert app.session_state["analysis"].prototype_html == prototype_before
        _assert_fast_results_visible(app)
        assert "模型正在分析" not in _visible_text(app)
    finally:
        ledger.conn.close()


def test_start_renders_fast_results_while_model_blocks_then_updates_same_row(ui):
    app = ui.app
    _configure_fake_model(app)
    gate = ui.fake.block_next_call()
    _by_label(app.text_area, "原话").input(EQUITY_REQUIREMENT)
    _by_label(app.button, "需知，开工").click()
    _run(app)

    assert gate.entered.wait(3), "应已在后台发起模型调用"
    job = _current_job(app)
    assert job is not None and not job.done.is_set(), "模型尚未完成时页面就应可用"
    assert job.kind == "analysis"
    req_id = app.session_state["req_id"]
    assert app.session_state["analysis"].estimate.status == "unavailable"
    _assert_fast_results_visible(app)
    _assert_running_status(app, job)
    running_text = _visible_text(app)
    assert "模型正在分析" in running_text
    assert "暂无模型统计" in running_text
    estimate_button = next(button for button in _estimate_tab(app).button if button.key == "estimate_with_model")
    assert estimate_button.disabled, "同条后台任务进行中应阻止重复发起估时"
    answer_button = next(button for button in app.button if "按业务答复重算" in button.label)
    assert answer_button.disabled, "同条模型运行中不应重复启动业务答复重算"
    jira_button = next(button for button in app.button if button.key == "to_jira")
    assert jira_button.disabled, "模型还在改写需求单时不能转 Jira"
    assert not _by_label(app.button, "需知，开工").disabled, "用户仍可开新需求"
    ledger = Ledger()
    try:
        count_before = ledger.stats()["count"]
        assert ledger.get(req_id)["mid_days"] is None
        gate.release.set()
        _finish_job(app, job)

        assert app.session_state["req_id"] == req_id
        assert ledger.stats()["count"] == count_before
        assert app.session_state["analysis"].estimate.status == "ready"
        assert _by_label(_estimate_tab(app).metric, BEFORE).value == "4～15 小时"
        assert _by_label(_estimate_tab(app).metric, AFTER).value == "2.5～10.5 小时"
        assert ledger.get(req_id)["mid_days"] == pytest.approx(9 / 8)
        assert ledger.get(req_id)["ai_days"] == pytest.approx(6 / 8)
        assert "模型正在分析" not in _visible_text(app)
        assert _state(app, "_model_job") is None
        # 后台归并后当前视口要有反馈：弹一条提示，问清页给出润色完成
        assert _toasts(app).count("模型分析已完成") == 1
        assert any("润色标题、追问和需求单" in message.value for message in app.success)
        assert not next(button for button in app.button if button.key == "to_jira").disabled
    finally:
        ledger.conn.close()


def test_full_background_failure_keeps_fast_outputs_and_shows_terminal_status(ui):
    app = ui.app
    _configure_fake_model(app)
    ui.fake.error = RuntimeError("simulated model failure")
    gate = ui.fake.block_next_call()
    _by_label(app.text_area, "原话").input(EQUITY_REQUIREMENT)
    _by_label(app.button, "需知，开工").click()
    _run(app)
    assert gate.entered.wait(3)
    job = _current_job(app)
    _assert_running_status(app, job)
    _assert_fast_results_visible(app)
    req_id = app.session_state["req_id"]
    seed = app.session_state["analysis"]
    spec_before = seed.spec.business_md
    prototype_before = seed.prototype_html

    gate.release.set()
    _finish_job(app, job)

    assert app.session_state["req_id"] == req_id
    assert app.session_state["analysis"].spec.business_md == spec_before
    assert app.session_state["analysis"].prototype_html == prototype_before
    assert app.session_state["analysis"].estimate.status == "failed"
    assert not _estimate_tab(app).metric
    assert not _model_statuses(app)
    assert _state(app, "_model_last_status")["status"] == "failed"
    assert "模型分析未完全成功" in _visible_text(app)
    assert "模型分析未完全成功，已保留规则结果" in _toasts(app)
    assert "模型正在分析" not in _visible_text(app)
    _assert_fast_results_visible(app)
    ledger = Ledger()
    try:
        assert ledger.stats()["count"] == 1
        assert ledger.get(req_id)["mid_days"] is None
        assert ledger.get(req_id)["ai_days"] is None
    finally:
        ledger.conn.close()


def test_old_background_job_cannot_overwrite_a_new_requirement(ui):
    app = ui.app
    _configure_fake_model(app)
    old_gate = ui.fake.block_next_call()
    _by_label(app.text_area, "原话").input(EQUITY_REQUIREMENT)
    _by_label(app.button, "需知，开工").click()
    _run(app)
    assert old_gate.entered.wait(3)
    old_job = _current_job(app)
    old_req_id = app.session_state["req_id"]
    old_revision = app.session_state["_analysis_revision"]

    new_text = EQUITY_REQUIREMENT + "\n这次验收范围仅限当前报送日。"
    new_gate = ui.fake.block_next_call()
    _by_label(app.text_area, "原话").input(new_text)
    _by_label(app.button, "需知，开工").click()
    _run(app)
    assert new_gate.entered.wait(3)
    new_job = _current_job(app)
    new_req_id = app.session_state["req_id"]
    assert new_req_id != old_req_id
    assert app.session_state["_analysis_revision"] != old_revision
    assert old_job.snapshot()["discarded"]
    assert new_job is not old_job
    assert app.session_state["analysis"].raw_text == new_text

    ledger = Ledger()
    try:
        new_gate.release.set()
        _finish_job(app, new_job)
        new_snapshot = json.loads(ledger.get(new_req_id)["payload"])["estimate"]
        assert new_snapshot["status"] == "ready"

        # 让较早的慢任务最后返回不同数字；它既不能改当前页面，也不能写入新台账行。
        old_response = _response()
        for task in old_response["tasks"]:
            for track in ("before", "after"):
                for value in ("low", "mid", "high"):
                    task[track][value] *= 10
        ui.fake.response = old_response
        old_gate.release.set()
        _finish_job(app, old_job)

        assert app.session_state["req_id"] == new_req_id
        assert app.session_state["analysis"].raw_text == new_text
        assert app.session_state["analysis"].estimate.mid == pytest.approx(9 / 8)
        assert json.loads(ledger.get(new_req_id)["payload"])["estimate"] == new_snapshot
        assert ledger.get(old_req_id)["mid_days"] is None, "失效任务不再补写旧需求的估算"
        assert ledger.stats()["count"] == 2
    finally:
        ledger.conn.close()


def test_estimate_click_without_key_warns_and_keeps_the_ledger_quiet(ui):
    app = ui.app
    _start_without_model(app)
    req_id = app.session_state["req_id"]
    ledger = Ledger()
    try:
        events_before = ledger.events_of(req_id)
        _by_label(_estimate_tab(app).button, "大模型分析估时").click()
        _run(app)

        warnings = [message.value for message in _estimate_tab(app).warning]
        assert "请先在上方「模型与参考材料」里选择模型并填写 API Key，再点「大模型分析估时」。" in warnings
        assert ledger.events_of(req_id) == events_before, "没调模型也没有旧模型结果要清，不记「估量重算」"
        assert "使用页面选择的模型，需要配置 Key。" in [caption.value for caption in _estimate_tab(app).caption]
        _run(app)
        assert not any("请先在上方" in message.value for message in _estimate_tab(app).warning), "提示只出一次"
        assert ui.fake.calls == []
    finally:
        ledger.conn.close()


def test_model_tasks_show_division_of_labour_and_extra_hours_read_as_increase(ui):
    app = ui.app
    _start_without_model(app)
    response = _response()
    for task in response["tasks"]:   # 模型判断 AI 协同反而更费时：节省比例为负
        task["before"], task["after"] = task["after"], task["before"]
    ui.fake.response = response
    _configure_fake_model(app)
    _analyze_estimate(app)
    tab = _estimate_tab(app)
    estimate = app.session_state["analysis"].estimate

    assert estimate.saving_pct < 0
    text = _visible_text(tab)
    assert f"按参考投入计算，预计人时增加 {abs(estimate.saving_pct):g}%" in text
    assert "减少 -" not in text
    task_table = next(frame.value for frame in tab.dataframe if "工作项" in frame.value.columns)
    assert list(task_table.columns[:3]) == ["工作项", "阶段", "分工"]
    assert list(task_table["分工"]) == [task.who for task in estimate.tasks]
    chips = next(block.value for block in tab.markdown if block.value.startswith("**分工**"))
    assert all(f"{name} {round(share * 100)}%" in chips for name, share in estimate.who.items())
    # 「以前与现在比较相同交付范围」是口径说明：只放 caption，不当成「建议交付方式」；真正的交付建议照常显示。
    # delivery 由测试指定，不依赖估时模块现在写的是口径句还是建议句
    scope_note = "以前与现在比较相同交付范围；两边均为预计投入工时，不含排期和等待时间。"
    advice = "技术部主做；AI 协同只省样板代码"
    for delivery, shown in ((scope_note, []), (advice, [f"**建议交付方式**：{advice}"])):
        analysis = app.session_state["analysis"]
        analysis.estimate.delivery = delivery
        app.session_state["analysis"] = analysis
        _run(app)
        tab = _estimate_tab(app)
        assert [block.value for block in tab.markdown if block.value.startswith("**建议交付方式**")] == shown
        assert scope_note in [caption.value for caption in tab.caption]


def test_complexity_rule_reference_shows_six_dimensions_without_model_numbers(ui):
    app = ui.app
    _start_sample(app, "01_")
    rule = app.session_state["analysis"].estimate.references["rule"]
    assert rule["method"] == "complexity"
    assert set(rule["dims"]) == {"功能点", "数据接入", "界面", "权限", "性能", "合规"}
    assert sum(rule["hour_parts"].values()) == pytest.approx(rule["before"]["mid"]), "各维柱子加总等于以前的参考小时"

    tab = _estimate_tab(app)
    reference = _by_label(tab.expander, "规则模板参考 · 六维复杂度、公式与任务")
    blocks = [block.value for block in reference.markdown]
    assert any(block.startswith("<svg") and 'aria-label="六维复杂度"' in block for block in blocks)
    assert any("<th>规则维度</th>" in block for block in blocks)
    assert reference.get("vega_lite_chart"), "分维工时柱图"
    assert "不是模型输出" in _visible_text(reference)
    assert not tab.metric and not _model_statuses(app), "规则参考不能冒充模型结果"

    # 规则模板是粗参考：10 小时以下留 1 位小数，以上取整，不出 64.64 这类两位小数
    tasks = next(frame.value for frame in reference.dataframe if "模板工作项" in frame.value.columns)
    assert "分工" in tasks.columns and set(tasks["分工"]) <= {"AI 能做", "AI 做人审", "人必须做"}
    hour_texts = [block for block in blocks if block.startswith(("**以前（规则模板）**", "**现在（规则模板）**"))]
    hour_texts += [block.value for block in tab.markdown if block.value.startswith(("以前：", "现在："))]
    hour_texts += list(tasks["以前（小时）"]) + list(tasks["现在（小时）"])
    assert len(hour_texts) >= 4
    for number in (n for text in hour_texts for n in re.findall(r"\d+(?:\.\d+)?", text)):
        assert re.fullmatch(r"\d+(\.\d)?", number) and ("." not in number or float(number) < 10), number


def test_background_settle_keeps_a_grid_status_save_made_in_the_same_run(ui):
    app = ui.app
    _start_without_model(app)
    first = app.session_state["req_id"]
    _configure_fake_model(app)
    gate = ui.fake.block_next_call()
    _by_label(app.text_area, "原话").input(EQUITY_REQUIREMENT + "\n另起一条：验收范围仅限当前报送日。")
    _by_label(app.button, "需知，开工").click()
    _run(app)
    assert gate.entered.wait(3)
    job = _current_job(app)
    gate.release.set()
    assert job.wait(timeout=5)

    # 用户在台账明细里改了第一条并点保存；后台结果恰好在这一轮开头归并
    grid = next(box for box in app.selectbox if str(box.key).startswith(f"ledger_grid_status_{first}_"))
    grid.select("开发中")
    app.button(key="ledger_save_top").click()
    _run(app)

    assert _state(app, "_model_job") is None and app.session_state["analysis"].estimate.status == "ready"
    ledger = Ledger()
    try:
        assert ledger.get(first)["status"] == "开发中", "后台归并不能让同一轮提交的状态修改作废"
    finally:
        ledger.conn.close()
