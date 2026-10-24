"""Jira 粘贴 / 导出解析的边界写法：单号、人名字段、数字、字段分行与 Tab、段落标题、GWT 兜底、表头别名与单位、状态白名单。"""
from __future__ import annotations

import csv
import io

import pytest

from xuzhi.jira import JiraIssue, parse_duration_h, parse_export, parse_issue_text
from xuzhi.ledger import _jira_status
from xuzhi.pipeline.assess import assess, calibrate, kind_of


def _csv(header: list[str], *rows: list[str]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")


# ---------------------------------------------------------------- 单号
def test_tech_words_are_not_issue_keys():
    it = parse_issue_text("导出 CSV 改成 UTF-8 编码\n类型：任务　模块：报表平台\n验收标准：\n1. 用 Excel 打开不乱码")
    assert it.key == "" and it.summary == "导出 CSV 改成 UTF-8 编码"
    it = parse_issue_text("【JIRA】XZ-1050 接口签名升级到 SHA-256\n类型：任务　模块：交易系统")
    assert it.key == "XZ-1050" and it.summary == "接口签名升级到 SHA-256"      # 摘要里只删选中的单号


def test_key_quoted_in_the_body_is_not_the_issue_key():
    body = ("下单前限仓超限提示\n类型：故事　优先级：高　模块：交易系统 / 风控系统\n描述：投资经理报单前希望知道这笔下去会不会超限仓。\n"
            "验收标准：报单前按交易所、公司、产品三套限仓口径校验\n技术说明：接入交易系统报单前置链路，复用 XZ-1005 的限仓配置表。")
    it = parse_issue_text(body)
    assert it.key == "" and it.summary == "下单前限仓超限提示" and "XZ-1005" in it.tech_notes
    crumb = parse_issue_text("需知项目 / XZ-1050\n接口签名升级到 SHA-256\n类型：任务")   # 详情页面包屑里的单号照样认
    assert crumb.key == "XZ-1050" and crumb.summary == "接口签名升级到 SHA-256" and not crumb.description


# ---------------------------------------------------------------- 人名字段一律不读
def test_people_fields_are_dropped_when_pasted():
    it = parse_issue_text("【JIRA】XZ-8001 风险度超限提醒\n类型：故事　模块：风控系统\n经办人：张三　报告人：李四\n"
                          "描述：风险度超 100% 时提醒\nAssignee:\nWang Wu\n验收标准：\n1. 超限提醒\n2. 留痕\n3. 夜盘也提醒")
    assert not any(name in it.text for name in ("张三", "李四", "Wang Wu"))
    assert it.issue_type == "故事" and it.description == "风险度超 100% 时提醒" and len(it.acceptance) == 3
    cloud = parse_issue_text("XZ-8003\n风险度超限提醒\n描述\n风险度超 100% 时提醒\n验收标准\n1. 超限提醒\n2. 留痕\n"
                             "经办人\n张三\n报告人\n李四\nAssignee\nWang Wu")   # Jira Cloud 详情栏：标签不带冒号
    assert not any(name in cloud.text for name in ("张三", "李四", "Wang Wu")) and cloud.acceptance == ["超限提醒", "留痕"]


def test_export_acceptance_column_needs_an_exact_header():
    data = _csv(["摘要", "问题关键字", "问题类型", "状态", "描述", "验收人", "验收时间", "Assignee"],
                ["客户适当性导出", "XZ-8002", "故事", "待办", "h3. 验收标准\n# 导出对比表\n# 不匹配单独一页\n# 姓名脱敏",
                 "王五", "2026-10-01", "赵六"])
    it = parse_export(data, "x.csv")[0]
    assert it.acceptance == ["导出对比表", "不匹配单独一页", "姓名脱敏"]   # 「验收人」不覆盖描述里的验收标准
    assert "王五" not in it.text and "赵六" not in it.text


# ---------------------------------------------------------------- 数字
@pytest.mark.parametrize("line, field, value", [
    ("Story Points：5（约 2 天）", "story_points", 5), ("故事点：3 / 估 2d", "story_points", 3),
    ("原始预估：2d（16h）", "original_h", 16), ("原始预估：2人日", "original_h", 16), ("原始预估：2 工作日", "original_h", 16),
])
def test_numbers_in_pasted_fields(line, field, value):
    assert getattr(parse_issue_text(f"【JIRA】XZ-1 加字段\n{line}"), field) == value


def test_duration_does_not_read_dates_as_days():
    assert parse_duration_h("10月5日") is None and parse_duration_h("5 人日") == 40


# ---------------------------------------------------------------- 结构：字段分行、Tab、段落标题、GWT、没写类型
def test_label_and_value_on_separate_lines():
    it = parse_issue_text("【JIRA】XZ-2 持仓查询加字段\n类型:\n故事\n优先级:\n高\n模块:\n交易系统\nStory Points:\n5\n原始预估:\n2d\n"
                          "描述:\n持仓接口加字段")
    assert (it.issue_type, it.priority, it.components, it.story_points, it.original_h) == ("故事", "高", ["交易系统"], 5, 16)
    assert it.description == "持仓接口加字段"


def test_tab_separated_fields():
    it = parse_issue_text("【JIRA】XZ-3 持仓查询加字段\n类型:\t故事\t优先级:\t高\t模块:\t交易系统\nStory Points:\t5\t原始预估:\t2d")
    assert (it.issue_type, it.priority, it.components, it.story_points, it.original_h) == ("故事", "高", ["交易系统"], 5, 16)


@pytest.mark.parametrize("heading", ["验收标准（AC）：", "【验收标准】", "## 验收标准", "验收标准:", "*验收标准*", "Acceptance Criteria:"])
def test_acceptance_heading_variants(heading):
    it = parse_issue_text(f"【JIRA】XZ-4 加字段\n类型：故事　模块：报表平台\n{heading}\n1. 新增列\n2. 导出一致\n3. 首日显示—")
    assert it.acceptance == ["新增列", "导出一致", "首日显示—"] and not it.description


def test_wiki_numbered_item_is_not_a_heading():
    """Wiki 的「# 」是编号列表项：「# 期望：…」是一条验收，不能把整段验收挪进「期望结果」。"""
    items = "# 期望：夜盘成交计入下一交易日\n# 节假日按日历归属\n# P99 ≤ 200ms"
    it = parse_issue_text(f"【JIRA】XZ-4 持仓加交易日\n类型：故事\n验收标准：\n{items}")
    exported = parse_export(_csv(["摘要", "问题关键字", "问题类型", "状态", "描述"],
                                 ["持仓加交易日", "XZ-4", "故事", "待办", f"持仓接口加字段\nh3. 验收标准\n{items}"]), "x.csv")[0]
    for x in (it, exported):
        assert x.acceptance == ["期望：夜盘成交计入下一交易日", "节假日按日历归属", "P99 ≤ 200ms"] and not x.expected


def test_gwt_fallback_only_takes_given_when_then():
    it = parse_issue_text("【JIRA】XZ-5 持仓页加字段\n类型：故事　模块：交易系统\n描述：\n当前持仓页没有交易日\n当日夜盘成交显示错误\n假设用户在夜盘下单")
    assert it.acceptance == [] and any("没有验收标准" in m for _, m in assess(it, calibrate([])).risks)
    gwt = parse_issue_text("【JIRA】XZ-5 持仓页加字段\n类型：故事\n描述：\nGiven 夜盘成交\nWhen 打开持仓页\nThen 交易日为下一交易日\n"
                           "当用户切换产品时，刷新持仓\n假设已登录，打开持仓页")
    assert len(gwt.acceptance) == 5
    for sep in (" ", "："):   # 中文 Gherkin：关键词后跟空格或冒号
        zh = parse_issue_text(f"【JIRA】XZ-5 持仓页加字段\n类型：故事\n描述：\n假设{sep}用户已登录\n当{sep}打开持仓页\n那么{sep}显示交易日列")
        assert len(zh.acceptance) == 3


def test_typeless_ticket_with_an_expectation_is_not_a_bug():
    it = parse_issue_text("【JIRA】XZ-6 日报定时推送\n模块：报表平台\n描述：每天推送日报\n期望：每天 8 点前推送")
    assert it.expected and kind_of(it) == "需求"
    bug = parse_issue_text("【JIRA】XZ-7 日报没推送\n模块：报表平台\n复现步骤：周一 8 点查看企业微信群\n期望结果：8 点前收到日报")
    assert kind_of(bug) == "缺陷"


def test_summary_field_keeps_the_first_body_line():
    it = parse_issue_text("摘要：持仓查询接口增加交易日归属字段\n类型：故事　模块：交易系统\n"
                          "风控需要在持仓查询里返回交易日归属，夜盘成交计入下一交易日。\n验收标准：\n1. a\n2. b\n3. c")
    assert it.summary == "持仓查询接口增加交易日归属字段" and it.description.startswith("风控需要在持仓查询里")
    en = parse_issue_text("Summary: Add trade date to position query\nType: Story  Component/s: 交易系统\n"
                          "Risk team needs trade date on each row.\nAcceptance Criteria:\n1. a")
    assert en.summary == "Add trade date to position query" and en.description == "Risk team needs trade date on each row."
    assert en.issue_type == "Story" and en.components == ["交易系统"]
    both = parse_issue_text("【JIRA】XZ-11 持仓查询加字段\n摘要：持仓查询加字段\n类型：故事\n风控要交易日")   # 标题行就是摘要：不进描述
    assert both.key == "XZ-11" and both.description == "风控要交易日"


# ---------------------------------------------------------------- 导出：描述原样、表头别名与单位
def test_export_description_keeps_field_like_lines():
    data = _csv(["摘要", "问题关键字", "问题类型", "状态", "描述"],
                ["行情断线", "XZ-77", "缺陷", "待办", "现象：盘中断线后不重连\n状态：线上已复现\n优先级：请尽快处理\n"
                 "h3. 复现步骤\n断网 10 秒\nh3. 期望结果\n5 秒内重连"])
    it = parse_export(data, "x.csv")[0]
    assert "状态：线上已复现" in it.description and "优先级：请尽快处理" in it.description
    assert (it.status, it.priority, it.repro, it.expected) == ("待办", "", "断网 10 秒", "5 秒内重连")


def test_export_header_aliases_and_units():
    data = _csv(["概要", "问题关键字", "问题类型", "状态", "预估工时", "实际工时"], ["加一列", "XZ-9", "故事", "完成", "57600", "72000"])
    it = parse_export(data, "x.csv")[0]
    assert (it.summary, it.original_h, it.spent_h, it.done) == ("加一列", 16, 20, True)   # 表头不带单位：Jira 导出按秒
    data = _csv(["Summary", "Issue key", "Status", "Original Estimate (h)", "Time Spent (hours)", "剩余预估（人天）", "Parent summary"],
                ["Add column", "XZ-10", "Done", "16", "20", "0.5", "父任务的摘要"])
    it = parse_export(data, "x.csv")[0]
    assert (it.summary, it.original_h, it.spent_h, it.remaining_h) == ("Add column", 16, 20, 4)


# ---------------------------------------------------------------- 状态：显式白名单
@pytest.mark.parametrize("status, done", [("已发布", True), ("已交付", True), ("上线完成", True), ("验收通过", True), ("Done", True),
                                          ("完成", True), ("待发布", False), ("未解决", False), ("待上线", False), ("进行中", False)])
def test_done_statuses_are_an_exact_whitelist(status, done):
    it = JiraIssue(status=status, spent_h=8.0)
    assert it.status_done is done and it.done is done and (_jira_status(it) == "已交付") is done
    no_hours = JiraIssue(status=status)
    assert no_hours.status_done is done and not no_hours.done   # 完成但没登记工时：不算待评估，也不进校准
