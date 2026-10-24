"""自定义网关目录仅由明确点击读取，选中后才更改手填模型。"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from xuzhi import config


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
BASE = "https://catalog.example.invalid/v1"
KEY = "test-catalog-key-placeholder"
MANUAL = "manual-original-model"


def _field(elements, label):
    matches = [element for element in elements if element.label == label]
    assert len(matches) == 1
    return matches[0]


def _run(app):
    app.run()
    assert not app.exception, [error.message for error in app.exception]
    return app


def _read(app):
    _field(app.button, "读取可用模型").click()
    return _run(app)


def _configure(app):
    _field(app.text_input, "API Key").input(KEY)
    _field(app.text_input, "API Base URL").input(BASE)
    _field(app.text_input, "模型 ID").input(MANUAL)
    return _run(app)


@pytest.fixture
def ui(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LEDGER_DB", tmp_path / "catalog-ui.sqlite3")
    monkeypatch.setattr(config, "HISTORY_LEARNED_PATH", tmp_path / "history_learned.json")
    catalog = SimpleNamespace(
        rows=[("available-model-a", "模型 A · available-model-a"), ("available-model-b", "模型 B · available-model-b")],
        note="模拟目录 · 2 个模型", error=None, calls=[], builds=[],
    )

    def list_models(base, key):
        catalog.calls.append((base, key))
        if catalog.error:
            raise catalog.error
        return catalog.rows, catalog.note

    def client_factory(*args, **kwargs):
        catalog.builds.append(kwargs)
        return SimpleNamespace(mode=kwargs.get("mode", "mock"), model=kwargs.get("model", ""), note="", last_model="")

    def no_network(*args, **kwargs):
        pytest.fail("目录 UI 测试不得外连")

    monkeypatch.setattr("xuzhi.llm.list_openai_models", list_models)
    monkeypatch.setattr("xuzhi.llm.LLM", client_factory)
    monkeypatch.setattr("xuzhi.llm.urlopen", no_network)
    st.cache_resource.clear()
    app = _run(AppTest.from_file(str(APP_PATH), default_timeout=10))
    source = _field(app.selectbox, "模型来源")
    source.select(next(option for option in source.options if "自定义" in option))
    _run(app)
    yield SimpleNamespace(app=app, catalog=catalog)
    st.cache_resource.clear()


def test_catalog_requires_click_and_reading_does_not_switch_manual_model(ui):
    app, catalog = ui.app, ui.catalog
    assert _field(app.button, "读取可用模型").disabled
    _configure(app)
    _run(app)
    assert catalog.calls == []
    assert _field(app.text_input, "模型 ID").value == MANUAL

    _read(app)
    assert catalog.calls == [(BASE, KEY)]
    assert _field(app.selectbox, "网关可用模型").value is None
    assert _field(app.text_input, "模型 ID").value == MANUAL
    assert catalog.builds[-1]["model"] == MANUAL
    _run(app)
    assert len(catalog.calls) == 1, "普通 rerun 不能再次请求目录"


def test_explicit_valid_selection_updates_model_and_later_manual_edit_wins(ui):
    app, catalog = ui.app, ui.catalog
    _configure(app)
    _read(app)
    _field(app.selectbox, "网关可用模型").select("available-model-b")
    _run(app)
    assert _field(app.text_input, "模型 ID").value == "available-model-b"
    assert catalog.builds[-1]["model"] == "available-model-b"
    assert len(catalog.calls) == 1

    _field(app.text_input, "模型 ID").input("manually-revised-model")
    _run(app)
    assert _field(app.text_input, "模型 ID").value == "manually-revised-model"
    assert _field(app.selectbox, "网关可用模型").value is None
    assert catalog.builds[-1]["model"] == "manually-revised-model"
    assert len(catalog.calls) == 1


@pytest.mark.parametrize("field,new_value", [("API Key", "another-test-key"), ("API Base URL", "https://other.example.invalid/v1")])
def test_account_or_gateway_change_invalidates_catalog_without_fetching(ui, field, new_value):
    app, catalog = ui.app, ui.catalog
    _configure(app)
    _read(app)
    _field(app.text_input, field).input(new_value)
    # 同一次交互还提交了旧目录的选择，回调也不能拿旧账号的目录改模型。
    _field(app.selectbox, "网关可用模型").select("available-model-a")
    _run(app)
    assert len(catalog.calls) == 1
    assert not [item for item in app.selectbox if item.label == "网关可用模型"]
    assert _field(app.text_input, "模型 ID").value == MANUAL

    catalog.rows = [("new-account-model", "新目录模型")]
    _read(app)
    assert len(catalog.calls) == 2
    expected = (BASE, new_value) if field == "API Key" else (new_value, KEY)
    assert catalog.calls[-1] == expected
    assert _field(app.selectbox, "网关可用模型").options == ["新目录模型"]
    assert _field(app.text_input, "模型 ID").value == MANUAL


@pytest.mark.parametrize("raises", [False, True])
def test_catalog_failure_keeps_manual_model_and_clears_stale_options(ui, raises):
    app, catalog = ui.app, ui.catalog
    _configure(app)
    _read(app)
    catalog.rows = []
    catalog.note = "模拟目录读取失败"
    if raises:
        catalog.error = RuntimeError("simulated catalog failure")
    _read(app)
    assert _field(app.text_input, "模型 ID").value == MANUAL
    assert catalog.builds[-1]["model"] == MANUAL
    assert not [item for item in app.selectbox if item.label == "网关可用模型"]
    assert any("当前模型 ID 保持不变" in message.value for message in app.warning)
    _run(app)
    assert len(catalog.calls) == 2
