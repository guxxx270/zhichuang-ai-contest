"""建议交付方式：按分工占比与原话风险给出（模型结果、规则参考各一份），不再写死成一句口径说明。"""
from __future__ import annotations

import pytest

from test_llm_estimate import FakeLLM
from xuzhi.pipeline.estimate import estimate
from xuzhi.pipeline.estimate_references import build_references
from xuzhi.pipeline.intake import extract_card
from xuzhi.pipeline.tasks import AI_DO, AI_REVIEW, HUMAN, delivery_mode

REPORT = "需要一个查询产品净值和风险指标的报表，支持Excel导出。"
LIMIT_PAGE = "做一个限仓监控页面，展示各产品持仓占交易所限仓额度的比例。"


def _answer(quote: str, whos: list[str]) -> dict:
    return {
        "summary": "按需求新增展示。", "confidence": "低", "confidence_reason": "未提供代码。", "assumptions": ["字段待核对。"],
        "tasks": [{
            "name": f"工作{i}", "phase": "开发", "who": who, "change": "按需求实现并自测。",
            "before": {"low": 1, "mid": 2, "high": 3, "reason": "人工实现与自测。"},
            "after": {"low": 0.5, "mid": 1, "high": 2, "reason": "代理生成，人审核关键数字。"},
            "evidence": [{"source": "requirement", "file": "", "quote": quote}],
        } for i, who in enumerate(whos, 1)],
    }


def test_low_risk_report_mostly_done_by_ai_can_be_self_built():
    card = extract_card(REPORT)
    assert card.req_type == "报表" and "客户" not in card.scope_objects

    result = estimate(card, REPORT, [], llm=FakeLLM(_answer("查询产品净值和风险指标的报表", [AI_DO, AI_DO, AI_REVIEW])))

    assert result.status == "ready" and result.who[AI_DO] >= 0.35
    assert "业务可在公司 AI 平台自建" in result.delivery
    assert "以前与现在比较相同交付范围" not in result.delivery     # 口径说明不再冒充交付建议


def test_trading_limit_wording_keeps_the_tech_team_in_charge():
    card = extract_card(LIMIT_PAGE)
    assert card.req_type == "页面"

    result = estimate(card, LIMIT_PAGE, [], llm=FakeLLM(_answer("限仓监控页面", [AI_DO, AI_DO, AI_DO])))

    assert result.status == "ready" and result.delivery.startswith("技术部主做")


def test_mostly_human_work_says_ai_only_saves_boilerplate():
    card = extract_card(REPORT)
    result = estimate(card, REPORT, [], llm=FakeLLM(_answer("支持Excel导出", [HUMAN, HUMAN, AI_DO])))
    assert result.delivery.startswith("技术部主做；AI 协同只省样板代码")


@pytest.mark.parametrize("text", [REPORT, LIMIT_PAGE])
def test_rule_reference_carries_its_own_split_and_delivery(text):
    card = extract_card(text)
    rule = build_references(card, text, [])["rule"]

    assert rule["status"] == "ready"
    split = rule["who_split"]
    assert set(split) == {AI_DO, AI_REVIEW, HUMAN} and sum(split.values()) == pytest.approx(1, abs=0.01)
    total = sum(task["before"]["mid"] for task in rule["tasks"])
    ai_do = sum(task["before"]["mid"] for task in rule["tasks"] if task["who"] == AI_DO)
    assert split[AI_DO] == pytest.approx(ai_do / total, abs=0.001)   # 按以前参考小时加权
    assert rule["delivery"] == delivery_mode(card, split, text)
    assert rule["delivery"].startswith("技术部主做") == (text == LIMIT_PAGE)


def test_rule_template_does_not_promise_an_adjustment_entry_the_page_lacks():
    assumptions = build_references(extract_card(REPORT), REPORT, [])["rule"]["assumptions"]
    assert any("评审时人工扣除（本页不提供调整入口）" in line for line in assumptions)
    assert not any("重新评估" in line for line in assumptions)


def test_no_model_result_has_no_delivery_and_an_empty_split_is_safe():
    card = extract_card(REPORT)
    assert estimate(card, REPORT, [], llm=None).delivery == ""
    assert delivery_mode(card, {}, REPORT).startswith("技术部主做")
