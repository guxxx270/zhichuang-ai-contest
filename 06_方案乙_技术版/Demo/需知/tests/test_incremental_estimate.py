"""已有指标口径的小改按增量工作估时，保留人工核对与真实范围边界。"""
from __future__ import annotations

import json

import pytest

from test_intake_requirements import EQUITY_REQUIREMENT
from xuzhi import knowledge
from xuzhi.ledger import Ledger
from xuzhi.pipeline import analyze
from xuzhi.pipeline.estimate import estimate
from xuzhi.pipeline.intake import Question, extract_card
from xuzhi.pipeline.tasks import HUMAN
from xuzhi.service import to_dict


SINGLE_CALCULATION = (
    "现有集团报送需要修改一个指标的计算方法：权益敞口。\n"
    "所有产品的权益敞口指标改为取该产品52W_beta市值。"
)


def _estimate(text=EQUITY_REQUIREMENT, questions=None):
    return estimate(extract_card(text), text, questions or [])


def _numbers(result):
    return (result.low, result.mid, result.high, result.ai_low, result.ai_mid, result.ai_high)


def _scope_question(*, answer="", default="沿用现有系统"):
    return Question(
        id="scope-test", category="范围", tag="通用", impact="高",
        question="是否新增接入、系统或迁移工作？", why="明确本次交付范围",
        default=default, answer=answer,
    )


def test_existing_equity_change_contains_only_incremental_work():
    result = _estimate()
    task_names = [task.name for task in result.tasks]

    assert result.method == "incremental"
    assert result.history_days is None
    assert result.assumptions and result.confidence == "低"
    assert any("母层" in name and "汇总" in name for name in task_names)
    assert any("回归" in name for name in task_names)
    assert not any(
        unwanted in name
        for name in task_names
        for unwanted in ("数据接入", "权限接入", "报表模板", "定时任务", "前端页面")
    )
    assert any("不重复" in task.note for task in result.tasks), "已包含的价格比不能再计一次计算工作"


def test_repeating_explanations_does_not_inflate_work_or_estimated_time():
    original = _estimate()
    repeated = _estimate("\n".join([EQUITY_REQUIREMENT] * 8))

    assert repeated.method == "incremental"
    assert _numbers(repeated) == pytest.approx(_numbers(original))
    assert [task.name for task in repeated.tasks] == [task.name for task in original.tasks]
    assert repeated.phases == pytest.approx(original.phases)
    assert repeated.ai_phases == pytest.approx(original.ai_phases)


def test_large_similar_history_is_reference_only_for_incremental_estimates(monkeypatch):
    monkeypatch.setattr(knowledge, "history", lambda: [])
    without_history = _estimate()
    big_project = knowledge.HistoryItem(
        id="large-project", year=2025, title="集团权益敞口平台完整建设",
        type="报表", dept="技术部", keywords=("权益敞口", "集团", "报送"),
        estimate_days=600, actual_days=900, note="整个项目，不能替代局部修改的工作量",
    )
    monkeypatch.setattr(knowledge, "history", lambda: [big_project])

    with_history = _estimate()

    assert with_history.method == "incremental"
    assert any(item.id == big_project.id for item in with_history.similar), "大历史应仍可展示供参考"
    assert with_history.history_days is None
    assert _numbers(with_history) == pytest.approx(_numbers(without_history))


@pytest.mark.parametrize(
    "additional_scope",
    [
        "本次还需新增外部接口。",
        "同时重建报送系统。",
        "需要迁移历史数据。",
        "同时回补历史报送数据。",
        "新增权限模型。",
        "增加审核页面。",
        "还需要新增一个集团报送系统。",
        "还需要开发一套新的报送平台。",
        "因为旧链路停用，不得不接入外部接口。",
    ],
    ids=[
        "external-interface", "rebuild-system", "migration", "backfill", "permissions", "new-page",
        "new-reporting-system", "develop-platform", "double-negative-interface",
    ],
)
def test_explicit_new_scope_uses_full_complexity_estimate(additional_scope):
    assert _estimate(EQUITY_REQUIREMENT + "\n" + additional_scope).method == "complexity"


@pytest.mark.parametrize(
    "excluded_scope",
    [
        "无需新增外部接口。",
        "不需要重建系统。",
        "不涉及迁移。",
        "不用回补历史报送数据。",
        "无需新增权限模型。",
        "不要增加审核页面。",
        "本次不涉及历史数据迁移。",
        "无需进行系统重构。",
    ],
    ids=["no-interface", "no-rebuild", "no-migration", "no-backfill", "no-permissions", "no-page", "no-historical-migration", "no-system-refactor"],
)
def test_explicitly_excluded_new_scope_stays_incremental(excluded_scope):
    result = _estimate(EQUITY_REQUIREMENT + "\n" + excluded_scope)

    assert result.method == "incremental"
    assert _numbers(result) == pytest.approx(_numbers(_estimate()))


def test_answered_scope_can_expand_work_but_an_unanswered_default_cannot():
    scope = "需要新增外部接口并迁移历史数据"

    assert _estimate(questions=[_scope_question(answer=scope)]).method == "complexity"
    assert _estimate(questions=[_scope_question(default=scope)]).method == "incremental"
    assert _estimate(questions=[_scope_question(answer="无需新增外部接口")]).method == "incremental"


def test_parent_rollup_adds_work_beyond_a_single_calculation_change():
    single = _estimate(SINGLE_CALCULATION)
    rollup = _estimate()

    assert single.method == rollup.method == "incremental"
    assert not any("母层" in task.name for task in single.tasks)
    assert any("母层" in task.name for task in rollup.tasks)
    assert len(single.tasks) < len(rollup.tasks)
    assert single.mid < rollup.mid and single.ai_mid < rollup.ai_mid


@pytest.mark.parametrize(
    "price_ratio_statement",
    [
        "价格比尚未包含在现有字段里，需要在计算时补上。",
        "价格比是否已包含在现有字段里，待核对。",
    ],
    ids=["price-ratio-not-in-field", "price-ratio-in-field-unconfirmed"],
)
def test_negative_or_unconfirmed_price_ratio_is_not_claimed_as_confirmed(price_ratio_statement):
    result = _estimate(SINGLE_CALCULATION + "\n" + price_ratio_statement)

    assert result.method == "incremental"
    assert not any("已确认价格比" in assumption for assumption in result.assumptions)
    assert not any("价格比已在字段中" in task.note for task in result.tasks)
    assert any("口径仍待核对" in task.note for task in result.tasks)


def test_hour_precision_and_phase_totals_are_conserved():
    result = _estimate()

    # 当前局部修改的工作小时基准；不能先四舍五入为一位人天再累加。
    assert (result.low * 8, result.mid * 8, result.high * 8) == pytest.approx((8, 15, 22))
    assert (result.ai_low * 8, result.ai_mid * 8, result.ai_high * 8) == pytest.approx((5, 8.5, 15))
    assert sum(task.trad_days for task in result.tasks) == pytest.approx(result.mid)
    assert sum(task.ai_days for task in result.tasks) == pytest.approx(result.ai_mid)
    assert sum(result.phases.values()) == pytest.approx(result.mid)
    assert sum(result.ai_phases.values()) == pytest.approx(result.ai_mid)
    for phase in result.phases:
        tasks = [task for task in result.tasks if task.phase == phase]
        assert result.phases[phase] == pytest.approx(sum(task.trad_days for task in tasks))
        assert result.ai_phases[phase] == pytest.approx(sum(task.ai_days for task in tasks))
    for task in result.tasks:
        assert task.ai_days == pytest.approx(task.trad_days * task.coef)
        assert task.trad_days * 16 == pytest.approx(round(task.trad_days * 16))
        assert task.ai_days * 16 == pytest.approx(round(task.ai_days * 16))


def test_human_validation_and_acceptance_do_not_receive_an_ai_discount():
    human_tasks = [task for task in _estimate().tasks if task.who == HUMAN]

    assert any("关键数字" in task.name for task in human_tasks)
    assert any("验收" in task.name for task in human_tasks)
    assert all(task.ai_days >= task.trad_days for task in human_tasks)


def test_service_and_ledger_snapshot_preserve_estimation_method_and_assumptions(tmp_path):
    analysis = analyze(EQUITY_REQUIREMENT, llm_polish=False)
    result = analysis.estimate
    public = to_dict(analysis)["estimate"]

    assert public["method"] == result.method == "incremental"
    assert public["assumptions"] == result.assumptions and public["assumptions"]
    assert public["hours_per_day"] == 8
    assert public["trad_days"]["mid"] == result.mid
    assert public["ai_days"]["mid"] == result.ai_mid
    assert sum(task["trad_days"] for task in public["tasks"]) == pytest.approx(result.mid)
    assert sum(task["ai_days"] for task in public["tasks"]) == pytest.approx(result.ai_mid)
    ledger = Ledger(tmp_path / "incremental.sqlite3")
    try:
        row = ledger.get(ledger.log_analysis(analysis))
        snapshot = json.loads(row["payload"])["estimate"]
        assert snapshot["method"] == "incremental"
        assert snapshot["assumptions"] == result.assumptions
        assert row["mid_days"] == snapshot["mid"] == result.mid
        assert row["ai_days"] == snapshot["ai_mid"] == result.ai_mid
        assert snapshot["low"] == result.low and snapshot["high"] == result.high
        assert snapshot["ai_low"] == result.ai_low and snapshot["ai_high"] == result.ai_high
    finally:
        ledger.conn.close()
