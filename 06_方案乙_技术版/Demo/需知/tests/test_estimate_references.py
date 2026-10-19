"""模型外的规则/历史参考：标明来源和固定假设，不混合成当前模型估时。"""
from __future__ import annotations

import json

import pytest

from test_intake_requirements import EQUITY_REQUIREMENT
from xuzhi import config, knowledge
from xuzhi.pipeline import estimate_references as references
from xuzhi.pipeline.intake import Question, extract_card


def _refs(text=EQUITY_REQUIREMENT, questions=None):
    return references.build_references(extract_card(text), text, questions or [])


def _history_item(**kwargs):
    values = dict(
        id="record-1", year=2025, title="集团权益敞口计算调整", type="数据", dept="运营",
        keywords=("权益敞口", "集团"), estimate_days=3, actual_days=2, note="历史原记录",
        ai_assisted=True, provenance="ledger", source="台账对账记录", source_ref="requirement:1",
    )
    values.update(kwargs)
    return knowledge.HistoryItem(**values)


def test_equity_change_retains_transparent_fixed_reference():
    rule = _refs()["rule"]

    assert rule["status"] == "ready" and rule["method"] == "incremental"
    assert "未校准" in rule["label"]
    assert rule["before"] == {"low": 8, "mid": 15, "high": 22}
    assert rule["after"] == {"low": 5, "mid": 8.5, "high": 15}
    assert any("母层" in task["name"] for task in rule["tasks"])
    assert not any("数据接入" in task["name"] or "权限接入" in task["name"] for task in rule["tasks"])
    assert any("未读取或验证现有代码" in assumption for assumption in rule["assumptions"])
    assert any("不是模型分析或历史实测" in assumption for assumption in rule["assumptions"])
    assert "固定" in rule["formula"] and "预设比值" in rule["formula"]
    for side in ("before", "after"):
        for key in ("low", "mid", "high"):
            assert sum(task[side][key] for task in rule["tasks"]) == pytest.approx(rule[side][key])
    for task in rule["tasks"]:
        assert task["coefficient"] == pytest.approx(task["after"]["mid"] / task["before"]["mid"])
        assert task["note"]


def test_repeated_explanations_do_not_inflate_incremental_template():
    original = _refs()["rule"]
    repeated = _refs("\n".join([EQUITY_REQUIREMENT] * 5))["rule"]

    assert repeated["before"] == original["before"] and repeated["after"] == original["after"]
    assert repeated["tasks"] == original["tasks"]


@pytest.mark.parametrize("scope", [
    "需要新增一个集团报送系统。", "还需要开发一套新平台。", "需要迁移历史数据。",
    "因为旧服务停用，不得不接入外部接口。",
])
def test_expanded_scope_does_not_use_single_calculation_reference(scope):
    assert _refs(EQUITY_REQUIREMENT + "\n" + scope)["rule"]["method"] == "complexity"


def test_only_answered_scope_expands_rule_reference():
    question = Question("q", "范围", "通用", "高", "需要什么新增工作？", "核对范围", "需要新增接口")
    assert _refs(questions=[question])["rule"]["method"] == "incremental"
    question.answer = "需要新增接口"
    assert _refs(questions=[question])["rule"]["method"] == "complexity"


def test_complexity_reference_is_independent_of_history_and_explains_coefficients(monkeypatch):
    text = "需要一个查询产品净值和风险指标的报表，支持Excel导出。"
    monkeypatch.setattr(knowledge, "history", lambda: [])
    without_history = _refs(text)["rule"]
    monkeypatch.setattr(knowledge, "history", lambda: [
        _history_item(type="报表", keywords=("报表", "净值"), estimate_days=10000, actual_days=20000),
    ])
    with_history = _refs(text)

    assert with_history["rule"] == without_history
    assert with_history["history"]["status"] == "ready"
    rule = with_history["rule"]
    assert rule["status"] == "ready" and rule["method"] == "complexity"
    assert "无历史加权" in rule["formula"] and "固定系数" in rule["formula"]
    assert any("不表示本次必须新建" in assumption for assumption in rule["assumptions"])
    assert rule["before"]["mid"] == pytest.approx(8 * (1.5 + sum(rule["weights"][key] * (score - 1) for key, score in rule["dims"].items())))
    for side in ("before", "after"):
        for key in ("low", "mid", "high"):
            assert sum(task[side][key] for task in rule["tasks"]) == pytest.approx(rule[side][key])
    for task in rule["tasks"]:
        assert task["after"]["mid"] == pytest.approx(task["before"]["mid"] * task["coefficient"], abs=0.01)
        assert task["share"] > 0


def test_history_displays_original_records_without_inventing_before_after(monkeypatch):
    original = _history_item()
    unrelated = _history_item(id="unrelated", keywords=("完全不同的业务",))
    monkeypatch.setattr(knowledge, "history", lambda: [original, unrelated])

    history = _refs()["history"]

    assert history["status"] == "ready" and len(history["items"]) == 1
    item = history["items"][0]
    assert item["id"] == original.id and item["title"] == original.title
    assert item["estimated_hours"] == 24 and item["actual_hours"] == 16
    assert item["ai_assisted"] is True and item["provenance"] == "ledger"
    assert item["source"] == "台账对账记录" and item["source_ref"] == "requirement:1"
    assert not {"before", "after", "mid", "saving_pct", "current_hours"} & set(history)
    assert not {"before", "after", "saving_pct"} & set(item)
    assert any("不等于同规模" in limitation for limitation in history["limitations"])


def test_no_matching_history_does_not_invent_a_value(monkeypatch):
    monkeypatch.setattr(knowledge, "history", lambda: [])

    history = _refs()["history"]

    assert history["status"] == "unavailable" and history["items"] == []
    assert "不生成" in history["summary"]


def test_history_failure_does_not_prevent_rule_reference(monkeypatch):
    def broken_history():
        raise ValueError("invalid json")
    monkeypatch.setattr(knowledge, "history", broken_history)

    result = _refs()

    assert result["rule"]["before"]["mid"] == 15
    assert result["history"]["status"] == "unavailable" and result["history"]["items"] == []
    assert "读取失败" in result["history"]["summary"]


def test_rule_failure_does_not_prevent_history_reference(monkeypatch):
    def broken_rule(*args):
        raise ValueError("malformed rule")
    monkeypatch.setattr(references, "calculation_change_scope", broken_rule)
    monkeypatch.setattr(knowledge, "history", lambda: [_history_item()])

    result = _refs()

    assert result["rule"]["status"] == "unavailable" and result["rule"]["before"]["mid"] is None
    assert result["history"]["status"] == "ready"


@pytest.fixture
def history_paths(tmp_path, monkeypatch):
    base, learned = tmp_path / "base.json", tmp_path / "learned.json"
    monkeypatch.setattr(config, "HISTORY_PATH", base)
    monkeypatch.setattr(config, "HISTORY_LEARNED_PATH", learned)
    knowledge.history.cache_clear()
    yield base, learned
    knowledge.history.cache_clear()


def _raw_item(**overrides):
    item = dict(id="H001", year=2025, title="集团权益敞口计算调整", type="数据", dept="运营",
                keywords=["权益敞口"], estimate_days=3, actual_days=2, note="")
    item.update(overrides)
    return item


@pytest.mark.parametrize(("description", "item", "provenance", "label"), [
    ("历史需求库，全部虚构", {}, "demo", "虚构演示"),
    ("来自公司工单系统导出（本机导入）", {}, "imported", "公司导入"),
    ("来自公司真实需求记录（本机导入）", {}, "imported", "公司导入"),
    ("历史记录", {"source": "台账对账"}, "ledger", "台账对账"),
    ("历史记录", {"source": "台账对账", "note": "演示数据（虚构）"}, "demo", "虚构演示"),
    ("未说明来源", {}, "unknown", "来源未确认"),
])
def test_history_provenance_uses_explicit_evidence_not_identifier(history_paths, description, item, provenance, label):
    base, _ = history_paths
    base.write_text(json.dumps({"_说明": description, "items": [_raw_item(**item)]}, ensure_ascii=False), encoding="utf-8")

    history = knowledge.history()[0]
    reference = _refs()["history"]["items"][0]

    assert history.provenance == reference["provenance"] == provenance
    assert label in reference["source"] and reference["source_ref"] == "base.json"
    assert reference["ai_assisted"] is None, "未记录AI标志时不能假定为传统开发"


def test_learned_record_keeps_its_source_when_overriding_demo_id(history_paths):
    base, learned = history_paths
    base.write_text(json.dumps({"_说明": "全部虚构", "items": [_raw_item()]}), encoding="utf-8")
    learned.write_text(json.dumps({"items": [_raw_item(source="台账对账", actual_days=4, ai_assisted=False)]}), encoding="utf-8")

    history = knowledge.history()

    assert len(history) == 1 and history[0].provenance == "ledger"
    assert history[0].actual_days == 4 and history[0].source_ref == "learned.json"
    assert history[0].ai_assisted is False


@pytest.mark.parametrize(("marker", "expected"), [
    ("unknown", None), ("未填写", None), ("", None), ("false", False), ("否", False), (" true ", True),
])
def test_unknown_ai_marker_remains_unknown(history_paths, marker, expected):
    base, _ = history_paths
    base.write_text(json.dumps({"items": [_raw_item(ai_assisted=marker)]}), encoding="utf-8")

    assert knowledge.history()[0].ai_assisted is expected
