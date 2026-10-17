"""Jira 需求评估：解析（粘贴文本 / 导出 CSV）+ 评估（复杂度、工时、AI 编码工时、质量检查、与原估对比）。"""
from __future__ import annotations

import csv
import io

import pytest

from xuzhi import config
from xuzhi.jira import parse_duration_h, parse_export, parse_issue_text
from xuzhi.pipeline.assess import assess, assess_many, calibrate, kind_of

SAMPLE = config.DATA_DIR / "jira" / "示例_迭代导出（虚构）.csv"

STORY = """【JIRA】XZ-1024 持仓查询接口增加交易日归属字段
类型：故事　优先级：高　模块：交易系统　Story Points：5　原始预估：2d
描述：作为风控人员，我希望持仓查询接口返回每笔持仓的交易日归属（夜盘计入下一交易日）。
验收标准：
1. /api/position/query 返回新增字段 trade_date_belong，夜盘成交归属下一交易日；
2. 节假日前最后一个夜盘按交易所日历归属；
3. 接口响应时间 P99 不超过 200ms；
4. 补充单元测试，覆盖夜盘、节假日、跨月三种场景。
技术说明：交易日历从结算系统读取；不改表结构，新增计算字段。"""

BUG = """XZ-1031 估值日报导出 Excel 时金额列显示为科学计数法
类型：缺陷　模块：报表平台　原始预估：4h
复现步骤：报表平台 → 估值日报 → 导出 Excel，打开后查看「资产净值」列。
期望结果：金额列按千分位、两位小数显示，与页面一致。"""


def test_parse_pasted_issue():
    it = parse_issue_text(STORY)
    assert it.key == "XZ-1024" and it.summary == "持仓查询接口增加交易日归属字段"
    assert it.issue_type == "故事" and it.components == ["交易系统"] and it.story_points == 5 and it.original_h == 16
    assert len(it.acceptance) == 4 and it.acceptance[2].startswith("接口响应时间")
    assert "结算系统" in it.tech_notes and "Active" not in it.description
    b = parse_issue_text(BUG)
    assert b.key == "XZ-1031" and b.repro and b.expected and not b.acceptance and kind_of(b) == "缺陷"


@pytest.mark.parametrize("raw, hours", [("3d 4h", 28), ("16h", 16), ("2天", 16), ("1w", 40), ("1.5 人天", 12), ("30m", 0.5), ("abc", None)])
def test_duration(raw, hours):
    assert parse_duration_h(raw) == hours


def test_parse_export_csv_seconds_and_repeated_columns():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["摘要", "问题关键字", "问题类型", "状态", "模块", "模块", "描述", "自定义字段 (Story Points)", "原始预估", "耗费时间", "Σ 原始预估"])
    w.writerow(["加一列", "XZ-1", "故事", "完成", "报表平台", "估值核算系统", "h3. 验收标准\n# 新增列\n# 导出一致", "3", "57600", "72000", "999999"])
    issues = parse_export(buf.getvalue().encode("utf-8-sig"), "x.csv")
    assert len(issues) == 1
    it = issues[0]
    assert it.components == ["报表平台", "估值核算系统"] and it.story_points == 3
    assert it.original_h == 16 and it.spent_h == 20 and it.done and it.acceptance == ["新增列", "导出一致"]


def test_sample_export_batch():
    issues = parse_export(SAMPLE.read_bytes(), SAMPLE.name)
    assert len(issues) == 14 and sum(i.done for i in issues) == 8
    results, calib = assess_many(issues)
    assert len(calib.samples) == 8 and 0.5 <= calib.factor <= 2.0 and calib.days_per_sp
    by = {a.issue.key: a for a in results}
    story, bug, trading, epic = by["XZ-1024"], by["XZ-1031"], by["XZ-1036"], by["XZ-1047"]
    assert bug.mid < 1.5 < story.mid                                    # 改个显示格式的缺陷远小于新接口字段
    for a in results:
        assert 0 < a.coding_ai < a.coding_trad <= a.mid and a.coding_ai <= a.full_ai < a.mid   # AI 编码 < 人工；主数只算编码阶段
        assert a.low <= a.mid <= a.high and set(a.dims) == {"改动规模", "涉及系统", "数据与口径", "接口与性能", "测试与验收", "风险与依赖"}
    assert all(kind_of(s) == "缺陷" for s, _ in bug.similar)              # 缺陷只和缺陷类比
    assert any("交易链路" in m for _, m in trading.risks) and trading.tasks[1].coef >= 0.8   # 交易链路编码只按人审折算
    assert any("史诗" in m for _, m in epic.risks) and not epic.similar
    assert any("没有验收标准" in m for _, m in by["XZ-1043"].risks)
    assert by["XZ-1040"].deviation is not None and by["XZ-1040"].deviation >= 0.4   # 原估 1 天，同类实际 2.5 天 → 偏乐观


def test_no_false_positive_terms():
    a = assess(parse_issue_text(STORY), calibrate([]))
    assert "不改表结构" in parse_issue_text(STORY).tech_notes and "表结构" not in a.reasons["数据与口径"]
    assert not any("提到结算" in m for _, m in a.risks)                 # 「结算系统」里的结算不算口径
    assert "外部依赖" not in a.reasons["风险与依赖"]                      # 「交易所日历」不算外部依赖
    assert a.jira_basis.startswith("原始预估 16") and a.jira_days == 2.0


def test_spec_to_jira_bridge():
    from app_jira import jira_text_from_analysis
    from xuzhi.llm import LLM
    from xuzhi.pipeline import analyze

    text = next(config.SAMPLES_DIR.glob("01_*.md")).read_text(encoding="utf-8")
    jira = jira_text_from_analysis(analyze(text, llm=LLM(mode="mock")))
    it = parse_issue_text(jira)
    assert it.summary.startswith("净值日报") and kind_of(it) == "需求" and len(it.acceptance) >= 2 and it.components
    assert assess(it, calibrate([])).mid > 0


# ---------------------------------------------------------------- 记进一本账
@pytest.fixture
def jledger(tmp_path, monkeypatch):
    from xuzhi import knowledge
    from xuzhi.ledger import Ledger

    monkeypatch.setattr(config, "HISTORY_LEARNED_PATH", tmp_path / "history_learned.json")
    knowledge.history.cache_clear()
    yield Ledger(tmp_path / "l.sqlite3")
    knowledge.history.cache_clear()


def test_jira_logged_into_ledger(jledger):
    results, _ = assess_many(parse_export(SAMPLE.read_bytes(), SAMPLE.name))
    first = [jledger.log_jira(a, "导出") for a in results]
    assert all(created for _, created in first) and len({rid for rid, _ in first}) == 14
    again = [jledger.log_jira(a, "导出") for a in results]          # 再评一遍：只更新，不重复建账
    assert [rid for rid, _ in again] == [rid for rid, _ in first] and not any(created for _, created in again)
    by_key = {a.issue.key: rid for a, (rid, _) in zip(results, first)}
    done = jledger.get(by_key["XZ-981"])
    assert done["source"] == "Jira" and done["status"] == "已对账" and done["actual_days"] == 2.5
    assert done["coding_ai"] and done["coding_ai"] < done["coding_trad"] <= done["mid_days"]
    assert not config.HISTORY_LEARNED_PATH.exists()                 # 不写零散需求的历史库
    todo = jledger.get(by_key["XZ-1024"])
    assert todo["status"] == "已确认" and todo["actual_days"] is None and todo["ext_key"] == "XZ-1024"
    acts = [e["action"] for e in jledger.events_of(by_key["XZ-1024"])]
    assert acts.count("Jira 评估") == 1 and acts.count("Jira 重新评估") == 1
    d = jledger.dashboard()
    assert d["count"] == 14 and d["jira_count"] == 14 and d["closed"] == 8 and d["avg_questions"] == 0.0 and d["avg_rounds"] == 0.0
    assert 0 < d["jira_coding_ai"] < d["jira_coding_trad"]
    assert d["by_dept"] == {} and d["by_type"] == {} and sum(d["jira_by_kind"].values()) == 14   # 部门 / 类型分布只算零散需求


def test_ledger_reconcile_feeds_jira_calibration(jledger):
    rid, _ = jledger.log_jira(assess(parse_issue_text(STORY), calibrate([])), "粘贴")
    assert jledger.jira_done_issues() == []
    res = jledger.reconcile(rid, 3.5, note="上线后回写")
    assert res["history_id"] == "" and not config.HISTORY_LEARNED_PATH.exists()
    pool = jledger.jira_done_issues()
    assert len(pool) == 1 and pool[0].key == "XZ-1024" and pool[0].done and pool[0].spent_days == 3.5
    assert pool[0].acceptance and pool[0].components == ["交易系统"]          # 工单内容原样存回，可做类比


def test_free_text_metrics_exclude_jira(jledger):
    from xuzhi.llm import LLM
    from xuzhi.pipeline import analyze

    a = analyze(next(config.SAMPLES_DIR.glob("01_*.md")).read_text(encoding="utf-8"), llm=LLM(mode="mock"))
    rid = jledger.log_analysis(a)
    jledger.log_jira(assess(parse_issue_text(BUG), calibrate([])), "粘贴")
    d = jledger.dashboard()
    assert d["count"] == 2 and d["jira_count"] == 1 and d["avg_questions"] == len(a.questions) and d["avg_rounds"] == 1.0
    jledger.attach_jira(rid, assess(parse_issue_text(STORY), calibrate([])))   # 写单转 Jira：挂回原账，不另开一行
    assert jledger.dashboard()["count"] == 2 and jledger.get(rid)["coding_ai"] > 0
    assert any(e["action"] == "转 Jira 评估" for e in jledger.events_of(rid))
