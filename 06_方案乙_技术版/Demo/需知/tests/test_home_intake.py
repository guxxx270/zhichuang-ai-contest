"""首页录入回归：空需求不受理，切换样例保留各自未提交的草稿。"""
from __future__ import annotations

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from xuzhi.ledger import Ledger


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
SAMPLE_LABEL = "选一个样例（或在下面粘贴）"
CUSTOM_CHOICE = "（粘贴自己的）"


def _widget(elements, label: str):
    matches = [element for element in elements if element.label == label]
    assert len(matches) == 1, f"应只有一个可见控件：{label}，实际 {len(matches)} 个"
    return matches[0]


def _run(app: AppTest) -> AppTest:
    app.run()
    assert not app.exception, [error.message for error in app.exception]
    return app


def _choose(app: AppTest, choice: str) -> None:
    _widget(app.selectbox, SAMPLE_LABEL).select(choice)
    _run(app)


def _samples(app: AppTest) -> list[str]:
    return [choice for choice in _widget(app.selectbox, SAMPLE_LABEL).options if choice != CUSTOM_CHOICE]


@pytest.fixture
def home():
    # conftest.py 已清空模型凭据，并将台账、记忆与审计指向临时目录。
    # 清除资源缓存，避免复用其他测试曾绑定到另一临时数据库的实例。
    st.cache_resource.clear()
    app = _run(AppTest.from_file(str(APP_PATH), default_timeout=20))
    yield app
    st.cache_resource.clear()


@pytest.mark.parametrize("empty_text", ["", " \t\n  "], ids=["cleared", "whitespace"])
def test_empty_sample_input_is_rejected_without_creating_analysis_or_ledger(home, empty_text):
    _choose(home, _samples(home)[0])
    assert _widget(home.text_area, "原话").value.strip(), "样例应先填入原话"
    count_before = Ledger().stats()["count"]

    _widget(home.text_area, "原话").input(empty_text)
    _widget(home.button, "需知，开工").click()
    _run(home)

    assert home.warning, "空原话应显示可见的填写提示"
    assert any("原话" in warning.value for warning in home.warning)
    assert "analysis" not in home.session_state or home.session_state["analysis"] is None
    assert Ledger().stats()["count"] == count_before, "空需求不得新增台账记录"
    assert _widget(home.text_area, "原话").value == empty_text, "不得静默回填并提交样例"


def test_custom_draft_survives_switching_to_sample_and_back(home):
    draft = "运营希望净值日报增加累计收益率。\n请保留这段尚未提交的补充说明。"
    _choose(home, CUSTOM_CHOICE)
    _widget(home.text_area, "原话").input(draft)
    _run(home)

    _choose(home, _samples(home)[0])
    assert _widget(home.text_area, "原话").value != draft
    _choose(home, CUSTOM_CHOICE)

    assert _widget(home.text_area, "原话").value == draft


def test_edited_sample_draft_survives_switching_to_another_sample_and_back(home):
    samples = _samples(home)
    assert len(samples) >= 2, "此场景需要两个不同的样例"
    _choose(home, samples[0])
    draft = _widget(home.text_area, "原话").value + "\n补充：仅本部门可下载，暂不开放客户使用。"
    _widget(home.text_area, "原话").input(draft)
    _run(home)

    _choose(home, samples[1])
    assert _widget(home.text_area, "原话").value != draft
    _choose(home, samples[0])

    assert _widget(home.text_area, "原话").value == draft


def test_only_explicit_start_submits_the_current_text_and_source(home):
    count_before = Ledger().stats()["count"]
    _widget(home.text_area, "原话").input("运营希望净值日报增加累计收益率。")
    _run(home)
    draft = "运营希望净值日报增加累计收益率。\n补充：仅本部门使用，每个交易日更新。"
    _widget(home.text_area, "原话").input(draft)
    _run(home)
    _widget(home.selectbox, "来源（可不选）").select("会议纪要")
    _run(home)
    _widget(home.toggle, "原型按手机版出").set_value(True)
    _run(home)
    _widget(home.toggle, "原型出客户版（脱敏）").set_value(True)
    _run(home)

    assert _widget(home.text_area, "原话").value == draft
    assert "analysis" not in home.session_state or home.session_state["analysis"] is None
    assert Ledger().stats()["count"] == count_before, "编辑或切换选项不能自动受理需求"

    _widget(home.button, "需知，开工").click()
    _run(home)

    analysis = home.session_state["analysis"]
    assert analysis.raw_text == _widget(home.text_area, "原话").value == draft
    assert analysis.card.source == "会议纪要"
    assert Ledger().stats()["count"] == count_before + 1
    _run(home)
    assert Ledger().stats()["count"] == count_before + 1, "开工后的自动刷新不能重复记账"


@pytest.mark.parametrize(
    ("current_box", "expected"),
    [
        ("当前页面里保留的需求", "当前页面里保留的需求\n补充每日更新一次"),
        ("", "补充每日更新一次"),
    ],
    ids=["append-to-current-box", "cleared-box-stays-cleared"],
)
def test_dictation_uses_current_box_and_preserves_the_result_across_samples(
    home, monkeypatch, current_box, expected
):
    count_before = Ledger().stats()["count"]
    _widget(home.text_area, "原话").input("这是旧的自定义草稿，不应覆盖之后的口述。")
    _run(home)
    sample = _samples(home)[0]
    _choose(home, sample)
    _widget(home.text_area, "原话").input(current_box)
    _run(home)

    # 模拟组件返回当前框的真实内容；空字符串仍是有效 box，不能回退到旧草稿。
    component_value = {
        "text": "补充每日更新一次",
        "ts": 1,
        "box": current_box,
        "has_box": 1,
    }
    monkeypatch.setattr("xuzhi.speech.dictation_bar", lambda: component_value)
    _run(home)

    assert _widget(home.selectbox, SAMPLE_LABEL).value == CUSTOM_CHOICE
    assert _widget(home.selectbox, "来源（可不选）").value == "口述"
    assert _widget(home.text_area, "原话").value == expected
    _choose(home, sample)
    _choose(home, CUSTOM_CHOICE)

    assert _widget(home.text_area, "原话").value == expected, "切换样例不能恢复口述前的旧草稿"
    assert Ledger().stats()["count"] == count_before, "口述和切换样例都不能自动开工"


def test_web_start_logs_intake_event(home):
    _widget(home.text_area, "原话").input("运营希望净值日报增加累计收益率。")
    _widget(home.button, "需知，开工").click()
    _run(home)

    req_id = home.session_state["req_id"]
    assert [event["action"] for event in Ledger().events_of(req_id)][:1] == ["Web受理"]
