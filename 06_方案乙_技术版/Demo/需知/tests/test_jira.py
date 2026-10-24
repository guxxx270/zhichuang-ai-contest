"""Jira 需求评估：解析（粘贴文本 / 导出 CSV）+ 评估（复杂度、工时、AI 编码工时、质量检查、与原估对比）。"""
from __future__ import annotations

import copy
import csv
import io

import pytest

from xuzhi import config
from xuzhi.jira import JiraIssue, parse_duration_h, parse_export, parse_issue_text
from xuzhi.pipeline.assess import assess, assess_many, backtest, calibrate, kind_of
from xuzhi.pipeline.tasks import AI_REVIEW

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
    assert trading.tasks[1].who == AI_REVIEW                               # 和质量检查的「AI 做人审」一致，不是「人必须做」
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


# ---------------------------------------------------------------- 评估口径：分工、回测、留一法、误判、相似度、史诗、小单、取整
def _sample():
    return parse_export(SAMPLE.read_bytes(), SAMPLE.name)


def _sample_by_key():
    results, calib = assess_many(_sample())
    return {a.issue.key: a for a in results}, calib


def _trusted_pool():
    """Jira 原估一贯只有实际的三成：回测里需知比原估准，这时才轮得到说「原估偏乐观」。"""
    done = copy.deepcopy([i for i in _sample() if i.done])
    for it in done:
        it.original_h = round(it.spent_h * 0.3, 1)
    return done


def _csv_bytes(header: list[str], *rows: list[str]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8-sig")


def test_trading_coding_is_ai_review_not_human():
    by, _ = _sample_by_key()
    for key in ("XZ-1036", "XZ-1005"):
        task = next(t for t in by[key].tasks if t.name == "编码实现")
        assert task.who == AI_REVIEW and task.coef >= 0.8
    bug = assess(parse_issue_text("【JIRA】XZ-2002 报单前置偶发拒单\n类型：缺陷　模块：交易系统\n复现步骤：盘中连续报单 10 笔\n期望结果：不拒单"),
                 calibrate([]))
    fix = next(t for t in bug.tasks if t.name == "修复编码")
    assert fix.who == AI_REVIEW and fix.coef >= 0.8


def test_backtest_decides_whether_to_call_the_estimate_optimistic():
    issues = _sample()
    results, calib = assess_many(issues)
    assert calib.backtest_n == 8 and calib.backtest_mape >= calib.orig_mape      # 示例里需知的回测误差并不比原估小
    assert (calib.backtest_mape, calib.orig_mape, calib.backtest_n) == backtest([i for i in issues if i.done])
    assert calib.backtest_line.startswith("历史回测（留一法，8 条）")
    notes = [a.deviation_note for a in results if not a.issue.done]
    assert notes and not any("偏乐观" in n or "偏保守" in n for n in notes)
    assert "历史回测：需知误差" in {a.issue.key: a for a in results}["XZ-1024"].deviation_note
    trusted = calibrate(_trusted_pool())
    a = assess(parse_issue_text(STORY), trusted)
    assert trusted.beats_original and a.deviation >= 0.4 and a.deviation_note.startswith("原估偏乐观")
    assert "暂无历史回测" in assess(parse_issue_text(STORY), calibrate([])).deviation_note


def test_done_rows_are_estimated_leave_one_out():
    issues = _sample()
    results, _ = assess_many(issues)
    done = [i for i in issues if i.done]
    for a in results:
        if a.issue.done:   # 自己不进自己的校准样本，也不拿自己类比
            assert a.mid == assess(a.issue, calibrate([x for x in done if x is not a.issue])).mid
            assert a.calib_note.startswith("按 7 条") and all(s.key != a.issue.key for s, _ in a.similar)
    three = [i for i in issues if i.key in ("XZ-981", "XZ-994", "XZ-1002") or not i.done]
    for a in assess_many(three)[0]:
        if a.issue.done:   # 留一法后只剩 2 条：不能自称「按 3 条校准」，也撑不起「置信度 高」
            assert "不足 3 条" in a.calib_note and a.confidence != "高"


NEG_BASE = ("【JIRA】XZ-7 持仓查询接口增加字段\n类型：故事　模块：交易系统\n描述：持仓查询接口返回新增字段。{x}\n"
            "验收标准：\n1. 返回新增字段\n2. 老调用方不受影响\n3. 补充单元测试")


def _profile(a):
    return a.mid, a.dims, [m for _, m in a.risks], [(t.name, t.who, t.coef) for t in a.tasks]


@pytest.mark.parametrize("phrase", ["不修改表结构。", "表结构不变。", "不需要改表结构。", "无表结构变更。", "不依赖其他系统。", "无外部依赖。",
                                    "不重构、不迁移。", "行情客户端展示该字段。", "企微机器人支持 /摘要 指令。",
                                    "客户经理可见。", "不涉及 DDL。", "无需迁移历史数据。"])
def test_negations_and_near_synonyms_change_nothing(phrase):
    ref = assess(parse_issue_text(NEG_BASE.format(x="")), calibrate([]))
    assert "需跨系统对接" not in ref.reasons["涉及系统"]                       # 「老调用方不受影响」不是要对接
    assert _profile(assess(parse_issue_text(NEG_BASE.format(x=phrase)), calibrate([]))) == _profile(ref)


def test_client_version_needs_review_but_is_not_customer_data():
    ref = assess(parse_issue_text(NEG_BASE.format(x="")), calibrate([]))
    a = assess(parse_issue_text(NEG_BASE.format(x="客户版同步增加。")), calibrate([]))
    assert a.dims["风险与依赖"] == ref.dims["风险与依赖"] + 1 and any("客户版" in m for _, m in a.risks)   # 客户版仍算
    by, _ = _sample_by_key()
    for key in ("XZ-997", "XZ-1040"):                                          # 客户端不再收到推送 / 客户版同步增加
        assert not any("涉及客户数据" in m for _, m in by[key].risks + [(None, by[key].reasons["风险与依赖"])])
    assert any("客户版" in m for _, m in by["XZ-1040"].risks)


def test_one_shared_system_is_not_enough_to_be_similar():
    by, _ = _sample_by_key()
    assert all(s.key != "XZ-994" for s, _ in by["XZ-1036"].similar) and by["XZ-1036"].confidence != "高"   # 只共享「风控系统」
    assert [s.key for s, _ in by["XZ-1040"].similar] == ["XZ-981"]           # 只共享「报表 / 报表平台」的不再拿来类比


def test_done_epic_is_neither_a_sample_nor_an_analogue():
    done = [i for i in _sample() if i.done]
    epic = JiraIssue(key="XZ-5000", summary="报表平台估值日报与净值体系重建", issue_type="史诗", status="完成", components=["报表平台"],
                     description="估值日报、净值周报、单位净值变动全部迁到新报表平台",
                     acceptance=["估值日报迁移", "净值周报迁移", "单位净值较上日变动列"], spent_h=320.0)
    new = parse_issue_text("【JIRA】XZ-6001 估值日报增加单位净值较上周变动列\n类型：故事　模块：报表平台　原始预估：2d\n验收标准：\n"
                           "1. 估值日报在单位净值右侧新增「较上周变动」列\n2. 首个交易日显示—\n3. 导出 Excel 与页面一致")
    with_epic = calibrate(done + [epic])
    a = assess(new, with_epic)
    assert a.mid == assess(new, calibrate(done)).mid and all(s.key != "XZ-5000" for s, _ in a.similar)
    assert with_epic.factor == calibrate(done).factor and len(with_epic.samples) == 8


def test_bridge_text_keeps_only_the_host_platform():
    from app_jira import jira_text_from_analysis
    from xuzhi.llm import LLM
    from xuzhi.pipeline import analyze

    def bridged(n: int) -> str:
        text = next(config.SAMPLES_DIR.glob(f"0{n}_*.md")).read_text(encoding="utf-8")
        return jira_text_from_analysis(analyze(text, llm=LLM(mode="mock")))

    t2 = bridged(2)
    assert "命中" not in t2 and "优先级" not in t2
    it5 = parse_issue_text(bridged(5))
    a5 = assess(it5, calibrate([]))
    assert len(it5.components) == 1 and a5.dims["涉及系统"] <= 2 and a5.level != "高"   # 净值周报加列不该是「高」


def test_small_improvement_is_a_task_and_not_flagged():
    trusted = calibrate(_trusted_pool())     # 即使回测撑得起「偏乐观」，2 小时的小单也只报差值
    for typ in ("改进", "Improvement", "优化", "任务"):
        it = parse_issue_text(f"【JIRA】XZ-7001 一本账页面按钮文案改成「保存」\n类型：{typ}　模块：报表平台　原始预估：2h\n"
                              "验收标准：\n1. 按钮显示「保存」")
        a = assess(it, trusted)
        assert a.kind == "任务" and a.mid < 1.5 and a.deviation >= 0.4
        assert a.deviation_note.startswith("与原估差") and "偏乐观" not in a.deviation_note
    assert kind_of(next(i for i in _sample() if i.key == "XZ-1005")) == "改造"   # 限仓配置化（改进，验收 4 条）仍按改造


def test_saving_pct_uses_unrounded_days():
    by, _ = _sample_by_key()
    a = by["XZ-1031"]
    assert (a.coding_trad, a.coding_ai, a.coding_saving_pct) == (0.6, 0.3, 43)   # 取整后 0.6→0.3 不是省 50%


# ---------------------------------------------------------------- 校准样本：示例与真实工单不混
OWN = "【JIRA】HX-1 结算单导出增加手续费明细列\n类型：故事　模块：报表平台　原始预估：1d\n验收标准：\n1. 导出新增手续费明细列\n2. 与页面一致\n3. 首日显示—"


def test_own_tickets_never_calibrate_with_the_fictional_sample(jledger):
    import app_jira

    sample, keys = _sample(), app_jira.sample_keys()
    demo_before = [a.mid for a in assess_many(sample, app_jira.calib_pool(jledger, include_sample=True))[0]]
    app_jira._log_batch(jledger, assess_many(sample)[0])          # 示例里 8 条已完成的对过账，进了一本账
    own = [parse_issue_text(OWN) for _ in range(3)]
    for i, it in enumerate(own, 1):
        it.key, it.status, it.spent_h = f"HX-{i}", "完成", 8.0 * i
    app_jira._log_batch(jledger, assess_many(own)[0])             # 自己的 3 条已完成工单也进了一本账
    mine = app_jira.calib_pool(jledger)                            # 默认：评估自己的工单
    assert sorted(it.key for it in mine) == ["HX-1", "HX-2", "HX-3"]
    demo = app_jira.calib_pool(jledger, include_sample=True)
    assert len(demo) == 8 and {it.key for it in demo} <= keys
    assert [a.mid for a in assess_many(sample, demo)[0]] == demo_before   # 示例的数字不随之前的上传漂移
    assert app_jira.pool_note(app_jira.calib_sources(jledger)) == "校准样本：一本账 3 条"
    assert app_jira.pool_note(app_jira.calib_sources(jledger), own) == "校准样本：本次 3 条"
    assert app_jira.pool_note(app_jira.calib_sources(jledger, include_sample=True), sample) == "校准样本：本次 8 条"


# ---------------------------------------------------------------- 逐条表：选中、拆列、导出
def test_batch_table_pick_columns_and_csv():
    import app_jira

    results, _ = assess_many(_sample())
    ordered, todo = results[:4], results[:2]
    assert app_jira._pick([13], ordered, todo) is todo[0]                   # 上一批选中第 14 行，换成 4 行的新批次不越界
    assert app_jira._pick([1], ordered, todo) is ordered[1]
    assert app_jira._pick([], ordered, todo, [(2, "摘要")]) is ordered[2]    # 点单元格也能选中
    assert app_jira._pick([], ordered, todo, [(9, "摘要")]) is todo[0]
    df = app_jira._batch_frame(results, {}).set_index("单号")
    assert (df.loc["XZ-1024", "Jira 原估(人天)"], df.loc["XZ-1024", "故事点折算(人天)"]) == ("2", "")
    assert (df.loc["XZ-1043", "Jira 原估(人天)"], df.loc["XZ-1043", "故事点折算(人天)"]) == ("", "2.2")   # 3 点 × 0.75，不是原估
    assert df.loc["XZ-1047", "类型"] == "史诗"
    data = _csv_bytes(["摘要", "问题关键字", "问题类型", "状态"], ["=1+1", "HX-9", "故事", "待办"], ["@SUM(A1)", "HX-10", "故事", "待办"])
    out = app_jira.csv_safe(app_jira._batch_frame(assess_many(parse_export(data, "x.csv"))[0], {})).to_csv(index=False)
    assert ",'=1+1," in out and ",'@SUM(A1)," in out and ",=1+1," not in out
    safe = app_jira.csv_safe(app_jira._batch_frame(results, {}))
    assert set(safe["对比原估"]) == set(app_jira._batch_frame(results, {})["对比原估"])   # 「+125%」这类数字不加引号


# ---------------------------------------------------------------- 页面
JIRA_PAGE = """
import streamlit as st
from app_jira import render_jira_mode
from xuzhi.ledger import Ledger
render_jira_mode(Ledger(st.session_state["_test_db"]))
"""


@pytest.fixture
def page(tmp_path):
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string(JIRA_PAGE, default_timeout=30)
    app.session_state["_test_db"] = str(tmp_path / "page.sqlite3")
    return app


def _ok(app):
    app.run()
    assert not app.exception, [e.message for e in app.exception]
    return app


def _shown(app) -> str:
    return "\n".join([m.value for m in app.markdown] + [c.value for c in app.caption])


def _submit(app):
    next(b for b in app.button if b.label == "评估这条工单").click()
    return _ok(app)


def _use_sample(app):
    next(b for b in app.button if b.label.startswith("用示例导出")).click()
    return _ok(app)


def test_bridged_ticket_resubmitted_attaches_to_the_original_row(page, tmp_path):
    from app_jira import jira_text_from_analysis
    from xuzhi.ledger import Ledger
    from xuzhi.llm import LLM
    from xuzhi.pipeline import analyze

    led = Ledger(tmp_path / "page.sqlite3")
    a = analyze(next(config.SAMPLES_DIR.glob("01_*.md")).read_text(encoding="utf-8"), llm=LLM(mode="mock"))
    rid, text = led.log_analysis(a), jira_text_from_analysis(a)
    # 写单页「转成 Jira 工单去评估」之后的状态（ui 约定写入 jira_bridge）
    page.session_state["jira_bridge"] = {"rid": rid, "text": text, "rule_before_h": 80.8, "rule_after_h": 50.08}
    page.session_state["jira_sample"] = page.session_state["jira_sample_shown"] = "（粘贴自己的）"
    page.session_state["jira_text"] = text
    _submit(_ok(page))                                              # 文本没改，直接点「评估这条工单」
    assert led.dashboard()["jira_count"] == 0 and page.session_state["jira_single_rid"] == rid
    assert [e["action"] for e in led.events_of(rid)].count("转 Jira 评估") == 1
    shown, mid = _shown(page), page.session_state["jira_single"].mid
    assert "规则模板 以前 80.8 小时（≈10.1 人天）" in shown and "口径不同" in shown
    assert f"{mid} 人天（{mid * 8:g} 小时）" in shown
    page.text_area[0].input(text + "\n补充：周五客户版也要。")       # 改过文本：按一条新的 Jira 工单记账
    _submit(page)
    assert led.dashboard()["jira_count"] == 1 and page.session_state["jira_single_rid"] != rid
    assert "规则模板 以前" not in _shown(page)


def test_switching_back_to_paste_your_own_clears_the_box(page):
    _ok(page)
    pick = page.selectbox(key="jira_sample")
    pick.select(pick.options[1])
    _ok(page)
    assert page.text_area[0].value.startswith("【JIRA】")
    page.selectbox(key="jira_sample").select("（粘贴自己的）")
    _ok(page)
    assert page.text_area[0].value == ""


def test_bad_upload_shows_a_message_instead_of_a_traceback(page):
    page.session_state["jira_src"] = "上传导出（批量）"
    _ok(page)
    page.file_uploader[0].set_value(("jira.xlsx", b"<html><body><table><tr><th>Summary</th></tr></table></body></html>",
                                     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
    _ok(page)
    assert any("读不了这个文件" in e.value for e in page.error)
    _ok(page)                                                       # 再刷新：不重复解析、不重复报错
    assert not page.error
    page.file_uploader[0].set_value(("x.csv", _csv_bytes(["概要", "问题关键字"], ["加一列", "HX-1"]), "text/csv"))
    _ok(page)
    assert any("没读到工时" in w.value for w in page.warning)       # 认不出工时列要说出来，不静默


MINE = _csv_bytes(["摘要", "问题关键字", "问题类型", "状态", "原始预估", "耗费时间"],
                  ["结算单导出加手续费列", "HX-11", "故事", "完成", "28800", "36000"],
                  ["保证金看板加筛选", "HX-12", "故事", "完成", "57600", "57600"],
                  ["消息推送限流", "HX-13", "任务", "完成", "28800", "21600"],
                  ["持仓页加交易日列", "HX-14", "故事", "待办", "28800", ""])


def test_sample_batch_then_own_upload(page):
    page.session_state["jira_src"] = "上传导出（批量）"
    _use_sample(_ok(page))
    shown = _shown(page)
    assert ">5 条<" in shown and "另有史诗 1 条（XZ-1047）" in shown            # 史诗单列，不计入合计
    assert "历史回测（留一法，8 条）" in shown and "校准样本：本次 8 条" in shown
    rev = page.session_state["jira_batch_rev"]
    page.session_state[f"jira_table_{rev}"] = {"selection": {"rows": [13], "columns": [], "cells": []}}
    _ok(page)
    assert any(m.value.startswith("### XZ-1008 ") for m in page.markdown)      # 选中了第 14 行
    page.file_uploader[0].set_value(("mine.csv", MINE, "text/csv"))            # 选着第 14 行时换成 4 行的批次
    _ok(page)
    assert page.session_state["jira_batch_rev"] == rev + 1 and "来源：上传的 mine.csv（4 条）" in _shown(page)
    assert "校准样本：本次 3 条" in _shown(page)                                 # 一条虚构示例都没混进来
    _use_sample(page)
    _ok(page)                                                                   # 文件还在上传框里：刷新不会跳回上传的那份
    assert "示例导出（虚构，14 条）" in _shown(page)
    page.file_uploader[0].set_value(None)
    _ok(page)
    page.file_uploader[0].set_value(("mine.csv", MINE, "text/csv"))            # 移除后再传同一个文件：重新评估
    _ok(page)
    assert "来源：上传的 mine.csv（4 条）" in _shown(page)
