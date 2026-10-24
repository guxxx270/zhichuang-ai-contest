"""一本账页面：对账提示、下拉与表单对齐、台账明细保存、看板口径、追问记忆清除、CSV 导出防公式注入、隐盾示例与本机监听。"""
from __future__ import annotations

import io
import json
import re
import tomllib
from pathlib import Path

import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from test_jira import STORY
from xuzhi import config, knowledge
from xuzhi.audit import Audit
from xuzhi.jira import parse_issue_text
from xuzhi.ledger import STATUSES, Ledger
from xuzhi.memory import Memory
from xuzhi.pipeline.assess import assess, calibrate

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
SAMPLE_LABEL = "选一个样例（或在下面粘贴）"
RECOMPUTE = "🔁 按业务答复重算（写单 / 估量 / 定架同步更新）"


def _run(app: AppTest) -> AppTest:
    app.run()
    assert not app.exception, [error.message for error in app.exception]
    return app


def _button(block, label: str):
    matches = [button for button in block.button if button.label == label]
    assert len(matches) == 1, f"应只有一个按钮：{label}，实际 {len(matches)} 个"
    return matches[0]


def _start(app: AppTest, prefix: str) -> int:
    box = next(element for element in app.selectbox if element.label == SAMPLE_LABEL)
    box.select(next(option for option in box.options if option.startswith(prefix)))
    _run(app)
    _button(app, "需知，开工").click()
    _run(app)
    return int(app.session_state["req_id"])


def _answer_first_question(app: AppTest, text: str = "按结算后口径") -> None:
    qid = app.session_state["analysis"].questions[0].id
    app.text_input(key=f"ans_{qid}").input(text)
    _run(app)


def _grid(app: AppTest, rid: int):
    """台账明细里某一行的状态下拉（键里带当前状态与刷新轮次，按前缀找）。"""
    return next(box for box in app.selectbox if str(box.key).startswith(f"ledger_grid_status_{rid}_"))


def _captions(app: AppTest) -> list[str]:
    return [caption.value for caption in app.caption]


def _jira_row(ledger: Ledger) -> int:
    rid, _ = ledger.log_jira(assess(parse_issue_text(STORY), calibrate([])), "粘贴")
    return rid


@pytest.fixture
def ui(tmp_path, monkeypatch):
    # conftest 已强制 mock 模型；一本账、记忆与审计指到本测试独占的临时库
    monkeypatch.setattr(config, "LEDGER_DB", tmp_path / "ui-ledger.sqlite3")
    monkeypatch.setattr(config, "HISTORY_LEARNED_PATH", tmp_path / "history_learned.json")
    st.cache_resource.clear()
    app = _run(AppTest.from_file(str(APP_PATH), default_timeout=30))
    yield app
    st.cache_resource.clear()
    knowledge.history.cache_clear()   # 对账可能写过临时历史库，别带进别的测试


def test_reconcile_result_is_shown_after_the_rerun(ui):
    rid = _start(ui, "01_")
    ui.number_input(key=f"rec_actual_{rid}").set_value(3)
    ui.button(key=f"rec_submit_{rid}").click()
    _run(ui)

    assert any("已记录实际 3.0 人天" in message.value for message in ui.success), "对账结果不能被 st.rerun() 吞掉"
    assert Ledger().get(rid)["actual_days"] == 3.0
    _run(ui)
    assert not any("已记录实际" in message.value for message in ui.success), "提示只出一次"


def test_reconcile_form_starts_empty_and_refuses_a_blank_submit(ui):
    rid = _start(ui, "01_")
    assert any(m.value == f"**对账 #{rid}：交付后回写实际人天**（尚无有效模型估时：只记实际人天）" for m in ui.markdown)
    ledger = Ledger()
    with ledger.conn:   # 模拟这条已有有效的两轨模型估时
        ledger.conn.execute("UPDATE requirements SET mid_days=1.5, ai_days=1.0 WHERE id=?", (rid,))
    _run(ui)

    assert ui.number_input(key=f"rec_actual_{rid}").value is None, "实际人天不能拿估算值预填"
    assert "估算参考：以前 1.5 人天 / 现在 1 人天" in _captions(ui)
    assert any(m.value.startswith(f"**对账 #{rid}：") and "有有效模型估时：计算偏差并进入历史记录参考" in m.value
               for m in ui.markdown)
    ui.button(key=f"rec_submit_{rid}").click()
    _run(ui)
    assert "请先填写实际人天" in [warning.value for warning in ui.warning]
    assert ledger.get(rid)["actual_days"] is None, "不填就提交不能记成偏差 0%"
    assert "对账" not in [event["action"] for event in ledger.events_of(rid)]


def test_picker_labels_stay_stable_and_the_form_follows_the_picked_row(ui):
    first = _start(ui, "01_")
    second = _start(ui, "02_")
    pick = ui.selectbox(key="ledger_pick")
    assert pick.value == second, "刚开工的那条默认选中"
    options = list(pick.options)
    assert not any(status in option for option in options for status in STATUSES), "下拉标签不带状态"
    pick.select(first)
    _run(ui)
    assert "当前状态：受理" in _captions(ui)

    ui.number_input(key=f"rec_actual_{first}").set_value(2.5)
    ui.button(key=f"rec_submit_{first}").click()
    _run(ui)
    pick = ui.selectbox(key="ledger_pick")
    assert pick.value == first and list(pick.options) == options, "对账改了状态，标签和选中项都不变"
    assert "当前状态：已对账" in _captions(ui)
    ui.button(key=f"fb_up_{first}").click()
    _run(ui)
    assert ui.selectbox(key="ledger_pick").value == first, "点👍后不跳行"
    ledger = Ledger()
    assert ledger.conn.execute("SELECT req_id, score FROM feedback").fetchall() == [(first, 1)]

    # 别处新记了一行（Jira 评估）：选项变了，下拉、标题与表单仍对准同一条
    jira_rid = _jira_row(ledger)
    _run(ui)
    pick = ui.selectbox(key="ledger_pick")
    assert pick.options[0].startswith(f"#{jira_rid} ")
    assert pick.value == first
    assert [box.key for box in ui.number_input if str(box.key).startswith("rec_actual_")] == [f"rec_actual_{first}"]
    assert any(m.value.startswith(f"**对账 #{first}：") for m in ui.markdown)


def test_grid_save_keeps_statuses_changed_elsewhere(ui):
    first = _start(ui, "01_")
    second = _start(ui, "02_")
    _answer_first_question(ui)
    _button(ui, RECOMPUTE).click()
    _run(ui)
    ledger = Ledger()
    assert ledger.get(second)["status"] == "已确认", "答复重算后受理 → 已确认"

    _grid(ui, first).select("开发中")
    ui.button(key="ledger_save_top").click()
    _run(ui)
    assert ledger.get(first)["status"] == "开发中"
    assert ledger.get(second)["status"] == "已确认", "保存别的行不能把重算后的状态改回去"
    assert ("状态", "受理") not in [(event["action"], event["detail"]) for event in ledger.events_of(second)]

    # 页面还没刷新，别处（另一个会话 / 企微）又改了状态：保存时以库里的为准
    ledger.set_status(first, "已交付")
    _grid(ui, second).select("开发中")
    ui.button(key="ledger_save_top").click()
    _run(ui)
    assert ledger.get(second)["status"] == "开发中"
    assert ledger.get(first)["status"] == "已交付"
    assert "已保存 1 条状态" in [toast.value for toast in ui.toast]


def test_recompute_without_changed_answers_is_not_another_round(ui):
    rid = _start(ui, "01_")
    ledger = Ledger()
    rounds = ledger.dashboard()["avg_rounds"]
    _button(ui, RECOMPUTE).click()
    _run(ui)

    assert "答复没有变化，未重算" in [message.value for message in ui.info]
    assert "业务答复重算" not in [event["action"] for event in ledger.events_of(rid)]
    assert [version["label"] for version in ledger.versions_of(rid)] == ["受理"]
    assert ledger.dashboard()["avg_rounds"] == rounds

    # 真改了答复才算一轮；重跑后在当前视口弹出结果提示
    _answer_first_question(ui)
    _button(ui, RECOMPUTE).click()
    _run(ui)
    assert [e["detail"] for e in ledger.events_of(rid) if e["action"] == "业务答复重算"] == ["1 条"]
    toasts = [toast.value for toast in ui.toast]
    assert "已按 1 条答复重算：确认消息、需求单、估量已更新" in toasts
    assert not any("未记入追问记忆" in toast for toast in toasts)


def test_recompute_tells_when_answers_were_kept_out_of_memory(ui, monkeypatch):
    real_learn = Memory.learn

    def learn(self, card, questions, req_id=0):
        written = real_learn(self, card, questions, req_id)
        self.last_skipped = 1   # 记忆模块跳过了含敏感信息的答复
        return written

    monkeypatch.setattr(Memory, "learn", learn)
    _start(ui, "01_")
    _answer_first_question(ui)
    _button(ui, RECOMPUTE).click()
    _run(ui)

    assert "1 条答复含手机号 / 账号等敏感信息，未记入追问记忆" in [toast.value for toast in ui.toast]


def test_recompute_after_a_demo_reset_books_the_requirement_again(ui):
    rid = _start(ui, "01_")
    ledger = Ledger()
    with ledger.conn:   # 页面开着时重置了演示数据
        for table in ("requirements", "versions", "events"):
            ledger.conn.execute(f"DELETE FROM {table}")
    _answer_first_question(ui)
    _button(ui, RECOMPUTE).click()
    _run(ui)

    new_rid = int(ui.session_state["req_id"])
    assert new_rid != rid and ledger.get(new_rid)["n_answered"] >= 1
    assert f"一本账已重置，本条已重新记为 #{new_rid}" in [warning.value for warning in ui.warning]
    assert [event["action"] for event in ledger.events_of(new_rid)].count("业务答复重算") == 1


def test_dashboard_wording_follows_comparable_and_jira_fields(ui, monkeypatch):
    _jira_row(Ledger())
    real_dashboard = Ledger.dashboard
    extra = {"closed": 2, "comparable_count": 0, "legacy_count": 0, "jira_mape": 34, "jira_closed": 2,
             "rule_mape": 41, "rule_count": 2}

    def dashboard(self):
        # 只模拟看板新增的口径字段，不依赖台账包的实现
        out = real_dashboard(self)
        out.update(extra)
        return out

    monkeypatch.setattr(Ledger, "dashboard", dashboard)
    ui.toggle(key="home_ledger").set_value(True)
    _run(ui)

    captions = _captions(ui)
    assert any("；Jira 规则评估 vs 实际耗时 偏差 34%（2 条，不计入上方估算偏差）" in c for c in captions)
    assert not any("旧版规则记录" in c for c in captions), "没有旧版规则记录时不提"
    assert any(c.startswith("规则模板参考 vs 实际：平均偏差 41%（2 条已对账；模板权重未校准") for c in captions)
    metrics = {metric.label: metric for metric in ui.metric}
    assert metrics["估算偏差"].help.startswith("已对账 2 条，可比 0 条（尚无有效模型估算）")
    assert metrics["平均追问"].value == "—" and metrics["平均澄清轮次"].value == "—", "只有 Jira 工单时不显示 0"

    extra.update(legacy_count=3, comparable_count=1, jira_mape=None, rule_mape=None)
    _run(ui)
    captions = _captions(ui)
    assert any("，旧版规则记录 3 条（不计入汇总）" in c for c in captions)
    assert not any("Jira 规则评估" in c or "规则模板参考 vs 实际" in c for c in captions)
    assert {m.label: m for m in ui.metric}["估算偏差"].help.startswith("已对账 2 条，其中可比（有效模型估算）1 条；")


def test_memory_panel_shows_dept_rows_and_clears_one_key(ui):
    memory = Memory()
    memory.conn.execute(
        "INSERT INTO answer_memory(scope,scope_key,qid,question,answer,req_type,ts,hits,req_id) VALUES(?,?,?,?,?,?,?,?,?)",
        ("dept", "资管部", "T01", "比较基准是什么？", "上一交易日结算价", "报表", "2026-10-01T10:00:00", 0, 0))
    memory.conn.commit()
    ui.toggle(key="home_ledger").set_value(True)
    _run(ui)

    panel = next(expander for expander in ui.expander if expander.label.startswith("🧠 追问记忆"))
    frame = panel.dataframe[0].value
    assert list(frame["范围"]) == ["部门"] and list(frame["提出方 / 部门"]) == ["资管部"]
    assert _button(panel, "清空全部记忆") and _button(panel, "清除这一项")
    assert not any("sqlite" in caption.value for caption in panel.caption), "不再让用户去手改数据库"
    assert ui.selectbox(key="mem_forget_key").value == "资管部"
    _button(panel, "清除这一项").click()
    _run(ui)
    assert Memory().rows() == []
    assert "已清除「资管部」的 1 条记忆" in [toast.value for toast in ui.toast]


def test_csv_exports_quote_cells_that_look_like_formulas(ui, monkeypatch):
    exports = {}
    real_download = st.download_button

    def spy(label, data=None, *args, **kwargs):
        exports[label] = data
        return real_download(label, data, *args, **kwargs)

    monkeypatch.setattr(st, "download_button", spy)
    rid = _start(ui, "01_")
    ledger = Ledger()
    with ledger.conn:   # 标题、备注是自由文本
        ledger.conn.execute("UPDATE requirements SET title=?, close_note=? WHERE id=?",
                            ('=HYPERLINK("http://example.invalid","点我")', "@SUM(1,2) 报表加一列", rid))
    Audit().record(purpose="=1+1", backend="openai", api_base="https://example.invalid/v1", model="m", duration_ms=7,
                   prompt_chars=1, completion_chars=1, redacted_tags=0, plain_hits=0, ok=False, error="-2+3 超时")
    _run(ui)

    rows = pd.read_csv(io.BytesIO(exports["导出审计台账 .csv"]), encoding="utf-8-sig")
    row = rows[rows["id"] == rid].iloc[0]
    assert row["title"].startswith("'=HYPERLINK(") and row["close_note"].startswith("'@SUM(")
    calls = pd.read_csv(io.BytesIO(exports["导出模型调用审计 .csv"]), encoding="utf-8-sig")
    assert calls["purpose"].iloc[0] == "'=1+1" and calls["error"].iloc[0] == "'-2+3 超时"
    assert calls["duration_ms"].iloc[0] == 7, "数字列不动"


def test_to_jira_hands_the_requirement_and_rule_hours_to_the_jira_page(ui):
    rid = _start(ui, "01_")
    rule = ui.session_state["analysis"].estimate.references["rule"]
    ui.button(key="to_jira").click()
    _run(ui)

    bridge = ui.session_state["jira_bridge"]
    assert bridge["rid"] == rid
    assert isinstance(bridge["rule_before_h"], (int, float)) and bridge["rule_before_h"] == rule["before"]["mid"]
    assert bridge["rule_after_h"] == rule["after"]["mid"]
    assert bridge["text"] == ui.session_state["jira_text"]


def test_jira_row_ai_toggle_explains_the_calibration_rule(ui):
    jira_rid = _jira_row(Ledger())
    ui.toggle(key="home_ledger").set_value(True)
    _run(ui)

    assert ui.selectbox(key="ledger_pick").value == jira_rid
    assert ui.toggle(key=f"rec_ai_{jira_rid}").help == "勾选后这条不作为 Jira 校准样本（校准只用传统开发的实际耗时）"
    assert any(m.value == f"**对账 #{jira_rid}：交付后回写实际人天**（记作 Jira 校准样本，下次评估即用）" for m in ui.markdown)


def test_shield_tab_shows_a_built_in_example_only_when_nothing_was_redacted(ui):
    _start(ui, "01_")
    assert ui.session_state["analysis"].redacted == 0
    shield = next(tab for tab in ui.tabs if tab.label.endswith("隐盾"))
    demo = next(expander for expander in shield.expander if expander.label == "看一个脱敏示例（内置虚构句子）")
    texts = [text.value for text in demo.text]
    assert any("13912345678" in text for text in texts), "左边是原文"
    assert any("<手机_1>" in text and "13912345678" not in text for text in texts), "右边是脱敏后"

    box = next(element for element in ui.selectbox if element.label == SAMPLE_LABEL)
    box.select("（粘贴自己的）")
    _run(ui)
    next(area for area in ui.text_area if area.label == "原话").input("客户手机 13812345678 想在净值日报里加一列较上日变动。")
    _button(ui, "需知，开工").click()
    _run(ui)
    assert ui.session_state["analysis"].redacted > 0
    shield = next(tab for tab in ui.tabs if tab.label.endswith("隐盾"))
    assert not [e for e in shield.expander if e.label == "看一个脱敏示例（内置虚构句子）"], "本条已有脱敏时不再出示例"


def test_ledger_page_details_are_readable(ui):
    _start(ui, "01_")
    _answer_first_question(ui)
    _button(ui, RECOMPUTE).click()
    _run(ui)
    tab = next(tab for tab in ui.tabs if tab.label.endswith("一本账"))

    flow = [caption.value for caption in tab.caption if "业务答复重算" in caption.value]
    assert flow and all(re.match(r"\d\d-\d\d \d\d:\d\d　", line) for line in flow), "流水时间不带 ISO 的 T"
    times = [m.value for m in tab.markdown if re.fullmatch(r"20\d\d-\d\d-\d\d[ T]\d\d:\d\d:\d\d", m.value)]
    assert times and not any("T" in value for value in times), "台账明细的时间也不带 T"
    # 七张指标卡分 4 + 3 两行：一行七张在 1280×720 下数字被截断
    labels = {"需求数", "平均追问", "平均澄清轮次", "全部台账预计省时", "估算偏差", "出稿用时", "记忆省问"}
    weights = sorted(round(column.weight, 3) for column in tab.columns if any(m.label in labels for m in column.metric))
    assert weights == [0.25] * 4 + [0.333] * 3
    assert not any("左右滑动" in caption.value for caption in tab.caption)
    # 部门 / 类型 / 状态柱图：斜 45° 且不自动藏标签
    axes = [json.loads(chart.proto.spec)["encoding"]["x"]["axis"] for chart in tab.get("vega_lite_chart")[:3]]
    assert axes and all(axis == {"labelAngle": -45, "labelLimit": 120, "labelOverlap": False} for axis in axes), axes


def test_page_listens_on_localhost_only():
    conf = tomllib.loads((APP_PATH.parent / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert conf["server"]["address"] == "127.0.0.1", "页面没有登录，不能对局域网开放"
