"""收需求应保留业务规则和原文证据，并优先识别明确要修改的指标。"""
from __future__ import annotations

import pytest

from xuzhi import config
from xuzhi.pipeline.intake import Card, extract_card


PARENT_SCOPE = (
    "2. 修改二：报送给集团的数据中，需要增加对母产品（穿透产品）的权益敞口的计算"
    "（目前有三个产品：母产品甲，母产品乙，母产品丙，之后如果有更多的这类母产品，"
    "也需要在集团报送的时候，自动计算其权益敞口的值）"
)
CHILD_FORMULA = (
    "子产品的52W_beta市值计算方法为：该子产品在该母产品下的费前市值"
    "*对标指数最新的收盘价/对标指数在产品最新净值日期对应的收盘价*beta系数"
)
PARENT_LAYER = (
    "计算时取的是该子产品在该母层下的市值，例如：当我需要计算母产品甲这个母产品的权益敞口值时，"
    "我需要加总的是子产品丁（母产品甲）的费前市值*beta系数（而不是子产品丁的beta市值）"
)
PRICE_RATIO_CLARIFICATION = (
    "补充确认：指数价格比已包含在现有字段里，计算时不能重复相乘，具体字段映射待确认"
)
EQUITY_REQUIREMENT = "\n".join(
    [
        "需求是：给集团报送的数据中，需要修改一个指标的计算方法：权益敞口",
        "1. 修改一：所有产品的权益敞口指标需要改为取该产品52W_beta市值",
        PARENT_SCOPE,
        "母产品权益敞口计算逻辑：将该母产品下所有的“是否含beta为是”的子产品的52W_beta市值加总。",
        CHILD_FORMULA + "。",
        PARENT_LAYER,
        PRICE_RATIO_CLARIFICATION,
    ]
)


def _assert_linked_requirement(card: Card, excerpt: str) -> None:
    """业务内容必须出现在功能点中，且该功能点能回到包含同一内容的原文。"""
    assert len(card.features) == len(card.raw_features)
    assert any(
        excerpt in feature and excerpt in source
        for feature, source in zip(card.features, card.raw_features)
    ), f"功能点或对应原文遗漏了：{excerpt}"


def test_equity_change_names_the_target_indicator_before_formula_inputs():
    card = extract_card(EQUITY_REQUIREMENT)

    assert card.indicators[0] == "权益敞口"
    assert card.title.startswith("权益敞口")
    # 净值和指数仍可用于理解公式，但不应取代原文明确要修改的指标。
    assert {"净值", "对标指数"} <= set(card.indicators)


@pytest.mark.parametrize("source", [PARENT_SCOPE, PARENT_LAYER], ids=["future-parents", "parent-specific-value"])
def test_long_equity_rules_keep_the_complete_source_and_linked_feature(source):
    assert len(source) > 80, "回归场景必须覆盖长业务原句"
    card = extract_card(EQUITY_REQUIREMENT)

    assert source in card.raw_features, "不得因展示长度限制丢弃或截断业务原文"
    _assert_linked_requirement(card, source.removeprefix("2. "))


def test_equity_intake_retains_filters_formula_and_the_confirmed_caveat():
    card = extract_card(EQUITY_REQUIREMENT)

    for excerpt in (
        "所有产品的权益敞口指标需要改为取该产品52W_beta市值",
        "之后如果有更多的这类母产品",
        "自动计算其权益敞口的值",
        "“是否含beta为是”的子产品的52W_beta市值加总",
        CHILD_FORMULA,
        "该子产品在该母层下的市值",
        "而不是子产品丁的beta市值",
        PRICE_RATIO_CLARIFICATION,
    ):
        _assert_linked_requirement(card, excerpt)

    assert all(source in EQUITY_REQUIREMENT for source in card.raw_features)


def test_nonfinancial_fee_change_preserves_long_rules_and_explicit_target():
    fee_rule = (
        "需要将各门店订单的应付费用改为订单金额减去优惠金额后加上配送费用与手续费，"
        "同一订单存在多次退款时必须累计本订单的已退金额后再扣减，不能直接使用该客户所有订单的退款总额，"
        "之后新增门店也要自动采用这套计算规则，无需逐个维护门店名称"
    )
    text = "结算报表需要修改一个指标的计算方法：应付费用。\n" + fee_rule
    assert len(fee_rule) > 80

    card = extract_card(text)

    assert card.indicators[0] == "应付费用"
    assert card.title.startswith("应付费用")
    assert "手续费" in card.indicators
    assert fee_rule in card.raw_features
    for excerpt in (
        "订单金额减去优惠金额后加上配送费用与手续费",
        "不能直接使用该客户所有订单的退款总额",
        "之后新增门店也要自动采用这套计算规则",
    ):
        _assert_linked_requirement(card, excerpt)
    assert all(source in text for source in card.raw_features)


def test_existing_short_demo_keeps_its_title_and_added_column():
    sample = next(config.SAMPLES_DIR.glob("01_*.md")).read_text(encoding="utf-8")
    card = extract_card(sample)

    assert "净值日报" in card.title
    assert "较上一交易日变动" in card.indicators
    assert card.features
    _assert_linked_requirement(card, "较上一交易日变动")
