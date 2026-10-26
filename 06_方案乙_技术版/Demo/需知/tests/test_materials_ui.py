"""Git 材料加载失败不能在 rerun 后消失，也不能静默启动空材料模型分析。"""
from __future__ import annotations

import pytest

from test_estimate_ui import (
    _assert_fast_results_visible,
    _by_label,
    _configure_fake_model,
    _current_job,
    _run,
    _visible_text,
    ui,
)
from test_intake_requirements import EQUITY_REQUIREMENT
from test_llm_estimate import _repo


REPO_URL = "https://example.invalid/team/exposure.git"
LOAD_ERROR = "未读到：https://example.invalid/team/exposure.git — 仓库访问被拒绝"
REPORT_LABEL = "📦 本次已读材料（Git / HTML）"


def _prepare_git(app):
    # AppTest 尚无 segmented_control 专用控件；用同一个 widget key 选择 Git。
    app.session_state["mat_mode"] = "Git 仓库"
    _run(app)
    _by_label(app.text_input, "仓库地址").input(REPO_URL)
    _by_label(app.text_input, "分支").input("release-test")
    _by_label(app.text_input, "Token").input("test-only-git-token")
    _by_label(app.text_area, "原话").input(EQUITY_REQUIREMENT)


def _start(app):
    _by_label(app.button, "需知，开工").click()
    _run(app)


def _report(app):
    report = _by_label(app.expander, REPORT_LABEL)
    assert report.proto.expanded, "失败或部分失败的材料报告应自动展开"
    return report


@pytest.mark.parametrize("errors", [[LOAD_ERROR], []], ids=["load-error", "empty-result"])
def test_failed_git_persists_after_rerun_and_does_not_start_model(ui, monkeypatch, errors):
    app = ui.app
    calls = []

    def fail_load(*args, **kwargs):
        calls.append(kwargs["entries"])
        return [], [], list(errors)

    monkeypatch.setattr("xuzhi.drafts.load_urls", fail_load)
    _configure_fake_model(app)
    _prepare_git(app)
    _start(app)

    assert app.session_state["analysis"].repos == []
    assert app.session_state["analysis"].estimate.status == "unavailable"
    assert app.session_state["materials_git_failed"] is True
    assert app.session_state["materials_errors"] == errors
    assert _current_job(app) is None
    assert ui.fake.purposes == [], "未读到指定仓库时不能开始问清、估时等模型调用"
    _assert_fast_results_visible(app)
    assert "本次未加载" in _visible_text(_report(app))
    assert "请修复上方仓库材料后，再点「需知，开工」" in _visible_text(app)
    if errors:
        assert LOAD_ERROR in _visible_text(_report(app))

    _run(app)
    assert _current_job(app) is None
    assert ui.fake.purposes == []
    assert len(calls) == 1, "普通 rerun 不应重新拉仓库"
    assert _by_label(app.text_input, "仓库地址").value == REPO_URL
    assert _by_label(app.text_input, "分支").value == "release-test"
    assert _by_label(app.text_input, "Token").value == "test-only-git-token"
    assert _by_label(app.text_input, "API Key").value == "ui-test-placeholder"
    if errors:
        assert LOAD_ERROR in _visible_text(_report(app))


def test_partial_load_keeps_successful_materials_and_failure_visible(ui, monkeypatch):
    app = ui.app
    repo = _repo()
    partial_error = "未读到：https://example.invalid/team/other.git — 分支不存在"
    monkeypatch.setattr("xuzhi.drafts.load_urls", lambda *args, **kwargs: ([], [repo], [partial_error]))
    _configure_fake_model(app)
    _prepare_git(app)
    gate = ui.fake.block_next_call()
    _start(app)

    job = _current_job(app)
    assert job is not None, "有成功仓库时仍可按实际已读材料继续分析"
    assert gate.entered.wait(5)
    assert app.session_state["materials_git_failed"] is False
    assert app.session_state["analysis"].repos == [repo]
    report = _report(app)
    assert repo.url in _visible_text(report)
    assert partial_error in _visible_text(report)

    _run(app)
    assert partial_error in _visible_text(_report(app))
    assert repo.files[0].path in _visible_text(_report(app))


def test_successful_retry_replaces_old_error_without_changing_inputs(ui, monkeypatch):
    app = ui.app
    outcomes = iter([([], [], [LOAD_ERROR]), ([], [_repo()], [])])
    monkeypatch.setattr("xuzhi.drafts.load_urls", lambda *args, **kwargs: next(outcomes))
    _prepare_git(app)
    _start(app)
    first_id = app.session_state["req_id"]
    assert LOAD_ERROR in _visible_text(_report(app))

    _start(app)
    assert app.session_state["req_id"] != first_id
    assert app.session_state["materials_errors"] == []
    assert app.session_state["materials_git_failed"] is False
    assert "已拉取仓库" in _visible_text(_report(app))
    assert LOAD_ERROR not in _visible_text(app)
    assert "未自动启动模型分析" not in _visible_text(app)
    assert _by_label(app.text_input, "仓库地址").value == REPO_URL
    assert _by_label(app.text_input, "Token").value == "test-only-git-token"


def test_start_without_materials_clears_previous_git_failure(ui, monkeypatch):
    app = ui.app
    monkeypatch.setattr("xuzhi.drafts.load_urls", lambda *args, **kwargs: ([], [], [LOAD_ERROR]))
    _prepare_git(app)
    _start(app)
    assert LOAD_ERROR in _visible_text(_report(app))

    app.session_state["mat_mode"] = "不用"
    _run(app)
    _start(app)

    assert app.session_state["materials_errors"] == []
    assert app.session_state["materials_git_failed"] is False
    assert LOAD_ERROR not in _visible_text(app)
    assert all(expander.label != REPORT_LABEL for expander in app.expander)
