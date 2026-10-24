"""版本对比页面：样例 ① 答两题重算 → 结果顶上「本次变更」、一本账「版本记录（2 版）」；需求变更开关沿用编号与已答口径。"""
from __future__ import annotations

from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from test_intake_requirements import EQUITY_REQUIREMENT
from test_llm_estimate import FakeLLM, _response
from xuzhi import config
from xuzhi.ledger import Ledger
from xuzhi.llm import LLM

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
SAMPLE_LABEL = "选一个样例（或在下面粘贴）"
RECOMPUTE = "🔁 按业务答复重算（写单 / 估量 / 定架同步更新）"
ANSWERS = {"T01": "结算后为准，夜盘算下一交易日", "T05": "上一交易日结算价，不复权"}
ADDED = "另外再加一列累计净值，盘中也要能看。"


def _widget(elements, label: str):
    matches = [element for element in elements if element.label == label]
    assert len(matches) == 1, f"应只有一个控件：{label}，实际 {len(matches)} 个"
    return matches[0]


def _run(app: AppTest) -> AppTest:
    app.run()
    assert not app.exception, [error.message for error in app.exception]
    return app


def _toggle_label(req_id: int) -> str:
    return f"作为当前需求 #{req_id} 的新版本（需求变更）"


def _banner(app: AppTest) -> list[str]:
    return [m.value for m in app.markdown if "本次变更" in m.value]


def _ledger_tab(app: AppTest):
    return next(tab for tab in app.tabs if tab.label.endswith("一本账"))


def _start_sample1(app: AppTest) -> int:
    sample = next(o for o in _widget(app.selectbox, SAMPLE_LABEL).options if o.startswith("01_"))
    _widget(app.selectbox, SAMPLE_LABEL).select(sample)
    _run(app)
    _widget(app.button, "需知，开工").click()
    _run(app)
    return int(app.session_state["req_id"])


def _answer_and_recompute(app: AppTest, answers: dict[str, str]) -> None:
    for qid, text in answers.items():
        app.text_input(key=f"ans_{qid}").input(text)
    _run(app)
    _widget(app.button, RECOMPUTE).click()
    _run(app)


@pytest.fixture
def ui(tmp_path, monkeypatch):
    # conftest 已强制 mock 模型；这里再把一本账指到本测试独占的临时库
    monkeypatch.setattr(config, "LEDGER_DB", tmp_path / "versions-ui.sqlite3")
    monkeypatch.setattr(config, "HISTORY_LEARNED_PATH", tmp_path / "history_learned.json")
    st.cache_resource.clear()
    app = _run(AppTest.from_file(str(APP_PATH), default_timeout=30))
    yield app
    st.cache_resource.clear()


def test_recompute_with_answers_shows_this_change_and_two_versions(ui):
    assert not [t for t in ui.toggle if "需求变更" in t.label], "还没有分析时不出需求变更开关"
    rid = _start_sample1(ui)
    assert not _banner(ui), "只有一版时不提示本次变更"

    _answer_and_recompute(ui, ANSWERS)

    assert [v["label"] for v in Ledger().versions_of(rid)] == ["受理", "业务答复重算"]
    banner = _banner(ui)
    assert len(banner) == 1 and "v2 · 业务答复重算" in banner[0] and "已答 \\+2" in banner[0]
    detail = next(e for e in ui.expander if e.label.startswith("变更明细（v1 → v2）"))
    assert any("新答复" in m.value and "结算后为准" in m.value for m in detail.markdown)
    assert _widget(detail.download_button, "下载本次变更说明 .md")

    history = next(e for e in _ledger_tab(ui).expander if e.label.startswith("版本记录"))
    assert history.label == "版本记录（2 版）"
    assert list(history.dataframe[0].value["动作"]) == ["受理", "业务答复重算"]
    assert _widget(history.selectbox, "从版本").value == 1 and _widget(history.selectbox, "到版本").value == 2
    assert _widget(history.download_button, "下载变更说明 .md")
    assert any("相对 v1：已答 \\+2" in m.value for m in history.markdown)


def test_change_toggle_keeps_req_id_carries_answers_and_records_change(ui):
    rid = _start_sample1(ui)
    _answer_and_recompute(ui, {"T01": ANSWERS["T01"]})
    count = Ledger().stats()["count"]

    box = _widget(ui.text_area, "原话")
    box.input(box.value + ADDED)
    _run(ui)
    toggle = _widget(ui.toggle, _toggle_label(rid))
    assert toggle.value is False, "需求变更开关默认关"
    toggle.set_value(True)
    _run(ui)
    # 答复框里刚填、没点重算就直接开工：同一次点击里的新答复也要带上
    ui.text_input(key="ans_T05").input(ANSWERS["T05"])
    _widget(ui.button, "需知，开工").click()
    _run(ui)

    assert ui.session_state["req_id"] == rid, "需求变更沿用原编号"
    ledger = Ledger()
    assert ledger.stats()["count"] == count, "需求变更不新开一行账"
    assert [v["label"] for v in ledger.versions_of(rid)] == ["受理", "业务答复重算", "需求变更"]
    assert any(e["action"] == "需求变更" and e["detail"].startswith("记为 v3") for e in ledger.events_of(rid))
    analysis = ui.session_state["analysis"]
    assert analysis.raw_text.endswith(ADDED)
    answered = {q.id: q.answer for q in analysis.questions if q.answer}
    assert answered == ANSWERS, "仍在清单里的题带上已答口径（含刚填未重算的）"
    assert ui.text_input(key="ans_T01").value == ANSWERS["T01"] and ui.text_input(key="ans_T05").value == ANSWERS["T05"]
    banner = _banner(ui)
    assert len(banner) == 1 and "v3 · 需求变更" in banner[0] and "原话已改" in banner[0] and "需重新确认 3 项" in banner[0]
    assert _widget(ui.toggle, _toggle_label(rid)).value is False, "落账后开关复位，下一次开工默认还是新需求"
    history = next(e for e in _ledger_tab(ui).expander if e.label.startswith("版本记录"))
    assert history.label == "版本记录（3 版）"
    assert _widget(history.selectbox, "从版本").value == 2 and _widget(history.selectbox, "到版本").value == 3


class BackgroundLLM(FakeLLM):
    """估时返回可通过校验的固定结果，其余润色调用返回空：只看版本怎么落账，不看模型文字。"""
    note = ""

    def chat_json(self, system, user):
        return super().chat_json(system, user) if self.current_purpose == "估时分析" else {}

    def chat(self, system, user, **kwargs):
        return ""


def _settle(app: AppTest) -> None:
    job = app.session_state["_model_job"] if "_model_job" in app.session_state else None
    if job is not None:
        assert job.wait(10), "后台模型任务应已结束"
    _run(app)   # AppTest 不跑 fragment 计时器，手动重跑一次等于轮询归并


def test_background_model_results_become_versions_and_failures_do_not(ui, monkeypatch):
    fake = BackgroundLLM()
    monkeypatch.setattr("xuzhi.llm.LLM", lambda *a, **k: fake if k.get("mode") == "api" else LLM(*a, **k))
    custom = next(o for o in _widget(ui.selectbox, "模型来源").options if "自定义" in o)
    _widget(ui.selectbox, "模型来源").select(custom)
    _run(ui)
    _widget(ui.text_input, "API Key").input("ui-test-placeholder")
    _widget(ui.text_input, "API Base URL").input("https://example.invalid/v1")
    _widget(ui.text_input, "模型 ID").input("test-estimator")
    _widget(ui.text_area, "原话").input(EQUITY_REQUIREMENT)
    _run(ui)
    _widget(ui.button, "需知，开工").click()
    _run(ui)
    rid = int(ui.session_state["req_id"])
    _settle(ui)

    ledger = Ledger()
    assert [v["label"] for v in ledger.versions_of(rid)] == ["受理", "模型增强"]
    assert ledger.versions_of(rid)[-1]["snapshot"]["estimate"]["model"]["before"]["mid"] == 9
    banner = _banner(ui)
    assert len(banner) == 1 and "v2 · 模型增强" in banner[0] and "新出模型估时" in banner[0]

    # 单独重估、数字变了 → 记「模型估时」一版；模型与模型比
    response = _response()
    for task in response["tasks"]:
        for value in ("low", "mid", "high"):
            task["before"][value] *= 2
    fake.response = response
    estimate_tab = next(tab for tab in ui.tabs if tab.label.endswith("估量"))
    _widget(estimate_tab.button, "重新分析估时").click()
    _run(ui)
    _settle(ui)
    assert [v["label"] for v in ledger.versions_of(rid)] == ["受理", "模型增强", "模型估时"]
    banner = _banner(ui)
    assert len(banner) == 1 and "v3 · 模型估时" in banner[0] and "模型估时（以前）9→18 小时" in banner[0]

    # 重估失败：不另存一版；屏上已不是存过的版本，「本次变更」先不出，免得和估量页对不上
    fake.error = RuntimeError("simulated model failure")
    estimate_tab = next(tab for tab in ui.tabs if tab.label.endswith("估量"))
    _widget(estimate_tab.button, "重新分析估时").click()
    _run(ui)
    _settle(ui)
    assert ui.session_state["analysis"].estimate.status == "failed"
    assert len(ledger.versions_of(rid)) == 3 and not _banner(ui)
    history = next(e for e in _ledger_tab(ui).expander if e.label.startswith("版本记录"))
    assert history.label == "版本记录（3 版）"


def test_change_of_a_row_gone_from_ledger_is_booked_as_new(ui):
    """页面开着时重置过演示数据：原编号已不在账上，需求变更退回按新需求受理并提示，不报错。"""
    rid = _start_sample1(ui)
    ledger = Ledger()
    with ledger.conn:
        ledger.conn.execute("DELETE FROM requirements WHERE id=?", (rid,))
        ledger.conn.execute("DELETE FROM versions WHERE req_id=?", (rid,))
    _widget(ui.toggle, _toggle_label(rid)).set_value(True)
    _run(ui)
    _widget(ui.button, "需知，开工").click()
    _run(ui)

    new_rid = int(ui.session_state["req_id"])
    assert new_rid != rid and any(f"#{rid} 已不在一本账里" in w.value for w in ui.warning)
    assert [v["label"] for v in Ledger().versions_of(new_rid)] == ["受理"] and not _banner(ui)


def test_start_without_toggle_still_opens_a_new_requirement(ui):
    rid = _start_sample1(ui)
    assert _widget(ui.toggle, _toggle_label(rid)).value is False
    _widget(ui.button, "需知，开工").click()
    _run(ui)

    new_rid = int(ui.session_state["req_id"])
    assert new_rid != rid and Ledger().stats()["count"] == 2
    assert [v["label"] for v in Ledger().versions_of(new_rid)] == ["受理"] and not _banner(ui)
    assert _widget(ui.toggle, _toggle_label(new_rid)).value is False
