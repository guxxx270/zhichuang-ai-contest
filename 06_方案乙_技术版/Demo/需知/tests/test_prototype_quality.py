"""出样质量：没有底稿时的规则模板要贴着原话（限仓校验、周期、基差列、删列改名、非期货表格）。"""
from __future__ import annotations

import re

import pytest

from xuzhi import config
from xuzhi.llm import LLM
from xuzhi.pipeline import analyze

SAMPLES = {p.stem[:2]: p.read_text(encoding="utf-8") for p in config.SAMPLES_DIR.glob("0[1-5]_*.md")}
HR = ("来源：企业微信 · 人力资源部 小何 → 技术部\n\n每个月月初把当月过生日的员工整理出来，提前一天提醒部门负责人，"
      "现在都是手工翻花名册，发企业微信或者手机短信都行。")


@pytest.fixture(scope="module")
def pages():
    llm = LLM(mode="mock")
    return {k: analyze(t, llm=llm).prototype_html for k, t in SAMPLES.items()}


def _heads(page: str) -> list[str]:
    return [re.sub(r"<[^>]+>|新增", "", h).strip() for h in re.findall(r"<th[^>]*>(.*?)</th>", page, flags=re.S)]


def test_pretrade_limit_page_shows_what_can_still_be_ordered(pages):
    page = pages["03"]
    assert "可下" in page and "限仓" in page and "提醒设置" in page
    heads = _heads(page)
    assert {"当前持仓（手）", "交易所限仓", "公司限额", "下单后占用", "还可下（手）", "状态"} <= set(heads)
    assert "价格异动" not in page


def test_non_futures_reminder_has_no_futures_wording():
    page = analyze(HR, llm_polish=False).prototype_html
    assert "提醒设置" in page and "价格异动" not in page and "夜盘" not in page


def test_report_wording_follows_period_and_basis_columns(pages):
    assert "订阅每周推送" in pages["05"] and "本周异常项" in pages["05"] and "订阅每日推送" not in pages["05"]
    assert "订阅每日推送" in pages["01"]
    heads = _heads(pages["02"])
    assert heads[:5] == ["品种", "现货价", "主力合约价", "基差", "基差率"] and "价差" not in heads   # 期现价差并入基差


def test_non_futures_table_uses_objects_and_record_count():
    page = analyze("营业部想做个客户回访登记页面，加一列回访结果，按客户经理分组。", llm_polish=False).prototype_html
    assert _heads(page)[:2] == ["客户", "回访结果"] and "客户A" in page
    assert "记录数" in page and "结算后" not in page and " 只<" not in page and "今日异常项" not in page


@pytest.mark.parametrize("text, absent, present", [
    ("净值日报改一下发送时间，改成每天晚上 8 点发", "变动", None),          # 模板标题里的「变动」不当新列
    ("资管运营：净值日报把回撤这一列去掉吧，没人看。", "回撤", None),      # 没有底稿也要删列
    ("把集中度改名为持仓集中度", "集中度", "持仓集中度"),                   # 没有底稿也要改名
])
def test_template_table_applies_column_edits(text, absent, present):
    heads = _heads(analyze(text, llm_polish=False).prototype_html)
    assert heads and not [h for h in heads if absent in h and h != present], heads
    assert present is None or present in heads
