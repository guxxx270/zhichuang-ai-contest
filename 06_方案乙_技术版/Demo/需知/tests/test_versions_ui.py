"""版本对比页面：样例 ① 答两题重算 → 结果顶上「本次变更」、一本账「版本记录（2 版）」；需求变更开关沿用编号与已答口径。"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from test_intake_requirements import EQUITY_REQUIREMENT
from test_llm_estimate import FakeLLM, _response
from xuzhi import config, versions
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


class RefineLLM(BackgroundLLM):
    """像真模型那样润色：改写功能点 / 标题 / 一句话、补两道题（一道高影响）。gate 关上时润色卡住，可在模型跑时看页面。"""

    def __init__(self):
        super().__init__()
        self.gate = threading.Event()
        self.gate.set()

    def chat_json(self, system, user):
        if self.current_purpose != "问清润色":
            return super().chat_json(system, user)
        assert self.gate.wait(10), "测试没放行模型润色"
        return {"title": "权益敞口口径调整", "goal": "按52W_beta市值重算集团报送的权益敞口",
                "features": ["权益敞口改取52W_beta市值", "母产品按含beta子产品加总", "价格比值口径按说明处理"],
                "extra_questions": [{"question": "报送截止时间是几点？", "impact": "高", "default": "T+1 上午 10 点"},
                                    {"question": "历史数据要不要回溯重算？", "impact": "中"}]}


def _use_model(app: AppTest, monkeypatch, fake) -> None:
    monkeypatch.setattr("xuzhi.llm.LLM", lambda *a, **k: fake if k.get("mode") == "api" else LLM(*a, **k))
    custom = next(o for o in _widget(app.selectbox, "模型来源").options if "自定义" in o)
    _widget(app.selectbox, "模型来源").select(custom)
    _run(app)
    _widget(app.text_input, "API Key").input("ui-test-placeholder")
    _widget(app.text_input, "API Base URL").input("https://example.invalid/v1")
    _widget(app.text_input, "模型 ID").input("test-estimator")


def test_with_a_model_this_change_compares_like_with_like(ui, monkeypatch):
    """配了模型：答复重算先存规则初稿、模型补全后再存一版。「本次变更」和上一份同类结果比，
    不把「模型改写 ↔ 规则抽取」的说法差别当成删了功能点去找业务确认。"""
    fake = RefineLLM()
    _use_model(ui, monkeypatch, fake)
    _widget(ui.text_area, "原话").input(EQUITY_REQUIREMENT)
    _run(ui)
    _widget(ui.button, "需知，开工").click()
    _run(ui)
    rid = int(ui.session_state["req_id"])
    _settle(ui)
    banner = _banner(ui)
    assert len(banner) == 1 and "v2 · 模型增强" in banner[0] and "需重新确认 1 项" in banner[0], banner
    detail = next(e for e in ui.expander if e.label.startswith("变更明细（v1 → v2）"))
    assert any(c.value == versions.MIXED_NOTE for c in detail.caption)
    assert not any("功能点不见了" in m.value for m in detail.markdown), "模型改写功能点不是业务删了需求"

    qids = [q.id for q in ui.session_state["analysis"].questions if not q.id.startswith("L")][:2]
    fake.gate.clear()   # 重算后模型补全先卡住：页面上是规则初稿 v3
    for qid in qids:
        ui.text_input(key=f"ans_{qid}").input(f"答复 {qid}")
    _run(ui)
    _widget(ui.button, RECOMPUTE).click()
    _run(ui)
    assert [v["label"] for v in Ledger().versions_of(rid)] == ["受理", "模型增强", "业务答复重算"]
    banner = _banner(ui)
    assert len(banner) == 1 and "v3 · 业务答复重算" in banner[0] and "相对 v1：已答 \\+2" in banner[0], banner
    assert any("这里和上一份规则初稿 v1 比" in c.value for c in ui.caption)

    fake.gate.set()
    _settle(ui)
    assert [v["label"] for v in Ledger().versions_of(rid)] == ["受理", "模型增强", "业务答复重算", "模型增强"]
    banner = _banner(ui)
    assert len(banner) == 1 and "v4 · 模型增强" in banner[0] and banner[0].endswith("相对 v2：已答 \\+2"), banner
    assert any("v3（业务答复重算）是模型补全前的规则初稿" in c.value for c in ui.caption)
    assert next(e for e in ui.expander if e.label.startswith("变更明细（v2 → v4）"))
    history = next(e for e in _ledger_tab(ui).expander if e.label.startswith("版本记录"))
    assert history.label == "版本记录（4 版）"   # 中间的规则初稿照样留在版本记录里，可任选两版对比
    assert list(history.dataframe[0].value["结果"]) == ["规则初稿", "含模型结果", "规则初稿", "含模型结果"]
    assert any(c.value == versions.MIXED_NOTE for c in history.caption)   # 默认最近两版（v3 → v4）隔类，注明缘由


def test_change_toggle_resets_when_another_sample_is_picked(ui):
    """开关开着时改选了别的样例：多半是另一条需求，开关复位，开工按新需求受理。"""
    rid = _start_sample1(ui)
    _widget(ui.toggle, _toggle_label(rid)).set_value(True)
    _run(ui)
    other = next(o for o in _widget(ui.selectbox, SAMPLE_LABEL).options if o.startswith("02_"))
    _widget(ui.selectbox, SAMPLE_LABEL).select(other)
    _run(ui)
    assert _widget(ui.toggle, _toggle_label(rid)).value is False
    _widget(ui.button, "需知，开工").click()
    _run(ui)
    assert int(ui.session_state["req_id"]) != rid and [v["label"] for v in Ledger().versions_of(rid)] == ["受理"]


def test_change_keeps_answers_recalled_from_memory_as_recalled(ui):
    """样例 ⑤ 沿用了追问记忆（🧠）：需求变更后照样按记忆沿用，不当成业务新答复，也不算「改答」。"""
    _start_sample1(ui)
    _answer_and_recompute(ui, ANSWERS)   # 记进追问记忆
    sample5 = next(o for o in _widget(ui.selectbox, SAMPLE_LABEL).options if o.startswith("05_"))
    _widget(ui.selectbox, SAMPLE_LABEL).select(sample5)
    _run(ui)
    _widget(ui.button, "需知，开工").click()
    _run(ui)
    rid = int(ui.session_state["req_id"])
    recalled = {q.id: q.answer for q in ui.session_state["analysis"].questions if q.recalled}
    assert recalled, "样例 ⑤ 应沿用样例 ① 的口径"

    box = _widget(ui.text_area, "原话")
    box.input(box.value + "另外导出 Excel 时也要这一列。")
    _run(ui)
    _widget(ui.toggle, _toggle_label(rid)).set_value(True)
    _run(ui)
    _widget(ui.button, "需知，开工").click()
    _run(ui)

    assert ui.session_state["req_id"] == rid
    after = ui.session_state["analysis"]
    assert {q.id: q.answer for q in after.questions if q.recalled} == recalled
    assert all(ui.text_input(key=f"ans_{qid}").value == answer for qid, answer in recalled.items())
    found = Ledger().versions_of(rid)
    assert [v["label"] for v in found] == ["受理", "需求变更"]
    d = versions.diff(found[0]["snapshot"], found[1]["snapshot"], 1, 2)
    assert d.text_changed and not d.questions["answered"] and not d.questions["changed"]


def test_jira_mode_ledger_shows_versions_for_free_requirements_only(ui):
    """Jira 模式里打开一本账：零散需求照样有版本记录，Jira 工单走「Jira 重新评估」不出版本记录。"""
    from test_jira import STORY
    from xuzhi import service
    from xuzhi.jira import parse_issue_text
    from xuzhi.memory import Memory
    from xuzhi.pipeline.assess import assess, calibrate

    ledger, memory = Ledger(), Memory()
    text = next(config.SAMPLES_DIR.glob("01_*.md")).read_text(encoding="utf-8")
    rid = service.run(text, ledger=ledger, memory=memory).req_id
    service.run(text, answers=ANSWERS, req_id=rid, ledger=ledger, memory=memory)
    jira_rid, _ = ledger.log_jira(assess(parse_issue_text(STORY), calibrate([])), "粘贴")
    ui.session_state["app_mode"] = "jira"
    _run(ui)
    ui.toggle(key="jira_show_ledger").set_value(True)
    _run(ui)
    assert ui.selectbox(key="ledger_pick").value == jira_rid
    assert not [e for e in ui.expander if e.label.startswith("版本记录")]
    ui.selectbox(key="ledger_pick").select(rid)
    _run(ui)
    history = next(e for e in ui.expander if e.label.startswith("版本记录"))
    assert history.label == "版本记录（2 版）" and any("相对 v1：已答 \\+2" in m.value for m in history.markdown)


def test_version_history_states_for_old_single_and_same_pick(ui):
    """首页打开一本账：早于版本功能的老需求、只有一版的需求、从 / 到选了同一版，各给一句说明，不报错。"""
    from xuzhi import service
    from xuzhi.memory import Memory

    ledger, memory = Ledger(), Memory()
    text = next(config.SAMPLES_DIR.glob("01_*.md")).read_text(encoding="utf-8")
    old = service.run(text, ledger=ledger, memory=memory).req_id
    with ledger.conn:
        ledger.conn.execute("DELETE FROM versions WHERE req_id=?", (old,))   # 早于版本功能的老需求
    one = service.run(text, ledger=ledger, memory=memory).req_id
    two = service.run(text, ledger=ledger, memory=memory).req_id
    service.run(text, answers=ANSWERS, req_id=two, ledger=ledger, memory=memory)
    ui.toggle(key="home_ledger").set_value(True)
    _run(ui)

    def history():
        return next(e for e in ui.expander if e.label.startswith("版本记录"))

    ui.selectbox(key="ledger_pick").select(old)
    _run(ui)
    assert history().label == "版本记录（0 版）" and any("早于版本功能" in c.value for c in history().caption)
    ui.selectbox(key="ledger_pick").select(one)
    _run(ui)
    assert history().label == "版本记录（1 版）" and any("目前只有一版" in c.value for c in history().caption)
    ui.selectbox(key="ledger_pick").select(two)
    _run(ui)
    _widget(history().selectbox, "从版本").select(2)
    _run(ui)
    assert any("选的是同一版" in c.value for c in history().caption) and not history().download_button


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
