"""模型润色后的文字不该把规则带偏：跨词的「报单 / 下单」、模型页面里的内部列、换了顺序的功能点。"""
from __future__ import annotations

import copy
import re

import pytest

from xuzhi import config, textutil
from xuzhi.llm import LLM
from xuzhi.pipeline import analyze
from xuzhi.pipeline.architect import build_architecture
from xuzhi.pipeline.assess import _TRADING
from xuzhi.pipeline.intake import Card
from xuzhi.pipeline.prototype import apply_prototype_view
from xuzhi.pipeline.spec import ORIGIN_NONE, build_stories

SAMPLE_01 = next(config.SAMPLES_DIR.glob("01_*.md")).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def nav():
    return analyze(SAMPLE_01, llm=LLM(mode="mock"))


@pytest.mark.parametrize("text", ["净值日报单位净值旁加一列", "以下单位需要填报", "申报单位：资管部", "周报单独发一份"])
def test_order_keyword_skips_cross_word_matches(text):
    assert not re.search(textutil.ORDER, text)
    assert not _TRADING.search(text)


@pytest.mark.parametrize("text", ["下单前提示限仓", "最近两次报单被交易所拒了", "当日报单笔数", "拟下单手数"])
def test_order_keyword_still_catches_trading(text):
    assert re.search(textutil.ORDER, text)


def test_polished_nav_features_do_not_pull_in_trading_system(nav):
    # 模型把功能点润色成「在净值日报单位净值旁…」：不能因此判成交易链路、改推新建独立服务
    card = copy.deepcopy(nav.card)
    card.features = ["在净值日报单位净值旁新增「较上一交易日变动」列", "日报加入回撤、集中度等风险指标"]
    arch = build_architecture(card, SAMPLE_01)
    assert arch.reuse[0].system == nav.architecture.reuse[0].system == "报表平台"
    d1 = next(d for d in arch.decisions if d.id == "D1")
    assert "报表平台" in d1.recommended
    assert not any(d.id == "D7" for d in arch.decisions)


def test_client_view_drops_internal_columns_in_model_pages():
    page = ("<html><body><table><tr><th>产品</th><th>单位净值</th><th>集中度预警</th><th>持仓集中度(%)</th></tr>"
            "<tr><td>甲</td><td>1.0213</td><td>高</td><td>31.2%</td></tr></table>"
            "<table><tr><th>产品</th><th>杠杆率</th><th>保证金占用</th></tr><tr><td>甲</td><td>1.3</td><td>12%</td></tr></table>"
            "</body></html>")
    out = apply_prototype_view(page, client_view=True)
    assert not re.search(r"集中度|杠杆|保证金占用", out)
    assert "单位净值" in out and "1.0213" in out and out.count("<td>") == 3


def test_story_origins_follow_content_after_reordering():
    raws = ["净值日报能不能加一列较上一交易日变动，就放在单位净值旁边", "风险指标也放进去，回撤、集中度那几个", "客户版不要的去掉"]
    card = Card(users=["运营"], raw_features=raws + [""],
                features=["在净值日报中加入回撤、集中度等风险指标", "单位净值旁新增「较上一交易日变动」列", "客户版去掉内部字段", "支持导出 Excel 并按产品筛选"])
    origins = [s.origin for s in build_stories(card)]
    assert origins == [raws[1], raws[0], raws[2], ORIGIN_NONE]


def test_rule_stories_keep_their_own_sentences(nav):
    stories = build_stories(nav.card)
    assert [s.origin for s in stories] == nav.card.raw_features[: len(stories)]
