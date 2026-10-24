""".env 配的是自定义网关（如公司 AI 平台）时，页面默认选「自定义网关」并带出网关、模型与 Key。"""
from __future__ import annotations

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from xuzhi import config

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
GATEWAY = "https://ai-platform.example.internal/v1"


def _run(app: AppTest) -> AppTest:
    app.run()
    assert not app.exception, [error.message for error in app.exception]
    return app


@pytest.fixture
def fresh_resources():
    st.cache_resource.clear()
    yield
    st.cache_resource.clear()


def test_env_custom_gateway_is_preselected_and_prefilled(monkeypatch, fresh_resources):
    monkeypatch.setattr(config, "LLM_API_BASE", GATEWAY)
    monkeypatch.setattr(config, "LLM_MODEL", "company-model-1")
    monkeypatch.setattr(config, "LLM_API_KEY", "sk-test-not-real")
    monkeypatch.setattr(config, "LLM_MODE", "auto")   # conftest 默认 mock；这里模拟正常部署
    app = _run(AppTest.from_file(str(APP_PATH), default_timeout=20))

    assert app.session_state["model_preset"].startswith("自定义网关")
    assert app.session_state["custom_api_base_custom"] == GATEWAY
    assert app.session_state["custom_model_custom"] == "company-model-1"
    # Key 只在服务端用：页面控件里不出现（控件状态会下发到浏览器），但页面已按配置好的模型运行
    assert not app.session_state["api_key_input_custom"]
    assert all(box.value != "sk-test-not-real" for box in app.text_input), "Key 不能出现在任何页面控件里"
    assert any("模型已配置" in element.value for element in app.markdown)


def test_page_edits_win_over_env_after_first_render(monkeypatch, fresh_resources):
    monkeypatch.setattr(config, "LLM_API_BASE", GATEWAY)
    monkeypatch.setattr(config, "LLM_MODEL", "company-model-1")
    monkeypatch.setattr(config, "LLM_API_KEY", "sk-test-not-real")
    monkeypatch.setattr(config, "LLM_MODE", "auto")   # conftest 默认 mock；这里模拟正常部署
    app = _run(AppTest.from_file(str(APP_PATH), default_timeout=20))
    [model_box] = [box for box in app.text_input if box.key == "custom_model_custom"]
    model_box.input("company-model-2")
    _run(app)

    assert app.session_state["custom_model_custom"] == "company-model-2"


def test_env_gateway_that_matches_a_preset_keeps_that_preset(monkeypatch, fresh_resources):
    # 不给 Key：带 Key 时硅基流动会实时拉模型目录，测试里不联网
    monkeypatch.setattr(config, "LLM_API_BASE", "https://api.siliconflow.cn/v1")
    monkeypatch.setattr(config, "LLM_MODEL", "some-model")
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    app = _run(AppTest.from_file(str(APP_PATH), default_timeout=20))

    assert app.session_state["model_preset"] == "硅基流动"
    assert "custom_api_base_custom" not in app.session_state


def test_no_env_gateway_keeps_first_preset_and_rule_engine(fresh_resources):
    app = _run(AppTest.from_file(str(APP_PATH), default_timeout=20))

    assert not app.session_state["model_preset"].startswith("自定义网关")
    assert not any("模型已配置" in element.value for element in app.markdown)


def test_llm_mode_mock_does_not_prefill_the_key(monkeypatch, fresh_resources):
    monkeypatch.setattr(config, "LLM_API_BASE", GATEWAY)
    monkeypatch.setattr(config, "LLM_MODEL", "company-model-1")
    monkeypatch.setattr(config, "LLM_API_KEY", "sk-test-not-real")
    monkeypatch.setattr(config, "LLM_MODE", "mock")
    app = _run(AppTest.from_file(str(APP_PATH), default_timeout=20))

    assert not app.session_state["api_key_input_custom"]
    assert not any("模型已配置" in element.value for element in app.markdown)


def test_env_key_is_not_sent_to_another_gateway(monkeypatch, fresh_resources):
    monkeypatch.setattr(config, "LLM_API_BASE", GATEWAY)
    monkeypatch.setattr(config, "LLM_MODEL", "company-model-1")
    monkeypatch.setattr(config, "LLM_API_KEY", "sk-test-not-real")
    monkeypatch.setattr(config, "LLM_MODE", "auto")
    app = _run(AppTest.from_file(str(APP_PATH), default_timeout=20))
    [base_box] = [box for box in app.text_input if box.key == "custom_api_base_custom"]
    base_box.input("https://other-gateway.example.com/v1")
    _run(app)

    assert not any("模型已配置" in element.value for element in app.markdown)
