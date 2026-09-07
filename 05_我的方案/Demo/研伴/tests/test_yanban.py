from datetime import date
from pathlib import Path

from yanban.docs import load_docs
from yanban.memory import Memory
from yanban.privacy import Redactor
from yanban.profile import Profile
from yanban.skills.morning import ask, build_brief, rule_points
from yanban.skills.rulebook import RuleBook
from yanban.skills.writer import compliance_check, weekly_report


def _docs():
    return load_docs()


def test_docs_loaded():
    docs = _docs()
    assert len(docs) == 7
    by_id = {d.id: d for d in docs}
    notice = next(d for d in docs if d.doc_type == "公告")
    assert notice.published_on == date(2026, 9, 5) and "RB" in notice.symbols


def test_rule_points_stances():
    docs = {d.publisher: d for d in _docs()}
    p = {x.symbol: x for x in rule_points(docs["甲期货研究所"], ["RB", "I"])}
    assert p["RB"].stance == "偏空" and p["I"].stance == "中性"
    p = {x.symbol: x for x in rule_points(docs["乙证券金属组"], ["RB", "I"])}
    assert p["RB"].stance == "偏多" and p["I"].stance == "中性"        # 「限制反弹高度」不算偏多
    p = {x.symbol: x for x in rule_points(docs["丁期货能化组"], ["SC"])}
    assert p["SC"].stance == "偏空"                                      # PTA 段不覆盖原油段
    p = {x.symbol: x for x in rule_points(docs["丙期货有色组"], ["CU"])}
    assert p["CU"].stance == "偏多" and any("12.8" in x for x in p["CU"].data_points)


def test_brief_divergence_and_events():
    prof = Profile(watch=["RB", "I", "CU", "SC", "M"])
    b = build_brief(_docs(), prof)
    sec = {s.symbol: s for s in b.sections}
    assert sec["RB"].divergence and "乙证券金属组" in sec["RB"].divergence and "甲期货研究所" in sec["RB"].divergence
    assert sec["I"].divergence is None                                   # 两家都中性
    assert any("最后交易日" in e.event for e in sec["CU"].events)
    assert "分歧雷达" in b.markdown and b.minutes_saved >= 5
    ans, hits = ask("看多螺纹钢的那家理由是什么", _docs())
    assert hits and any("乙证券" in src for src, _ in hits)


def test_rulebook_calcs():
    rb = RuleBook()
    a = rb.answer("沪铜 2609 结算价 78500 今天涨跌停多少")
    assert a.intent == "涨跌停计算" and a.calc["涨停价"] == 84000 and a.calc["跌停价"] == 73000
    a = rb.answer("螺纹钢 按 3200 价格 开 100 手 需要多少保证金")
    assert a.intent == "保证金计算" and a.calc["所需保证金"] == 288000
    a = rb.answer("沪铜 交割月前一月 持有 800 手 超没超限仓")
    assert a.calc["阶段"] == "交割月前一月" and a.calc["结论"] == "未超限"
    a = rb.answer("沪铜 交割月 持有 800 手 超没超限仓")
    assert a.calc["结论"] == "超限"
    a = rb.answer("沪铜 2609 最后交易日是哪天")
    assert a.calc["最后交易日"] == "2026-09-15"
    a = rb.answer("铁矿石 2609 最后交易日是哪天")
    assert a.calc["最后交易日"] == "2026-09-25"                          # 9 月倒数第 4 个工作日（周末粗算）
    a = rb.answer("什么情况下会被强行平仓")
    assert a.intent == "规则问答" and "强行平仓" in a.text and a.citations


def test_writer_and_compliance(tmp_path: Path):
    prof = Profile()
    b = build_brief(_docs(), prof)
    draft, eng = weekly_report(prof, b, notes="测试")
    assert "投研周报" in draft and "免责声明" in draft and "螺纹钢" in draft
    f = compliance_check(draft + " 本周螺纹钢必涨，保证收益。")
    kinds = {(x.kind, x.term) for x in f}
    assert ("禁用表述", "必涨") in kinds and ("禁用表述", "保证收益") in kinds
    assert not any(x.kind == "缺失要素" for x in f)


def test_privacy_and_memory(tmp_path: Path):
    text = "客户：王建国先生（手机 13912345678，资金账号 8812345678901）浮亏 1,250,000 元"
    r = Redactor().redact(text)
    assert "王建国" not in r.text and "<手机_1>" in r.text and Redactor.restore(r.text, r.mapping) == text
    m = Memory(tmp_path / "m.sqlite3")
    m.log("晨读", "生成", inp="x", out="y", redacted=3, minutes=12)
    m.feedback("晨读", "2026-09-05", 1)
    s = m.stats()
    assert s["calls"] == 1 and s["minutes_total"] == 12 and s["feedback_up"] == 1


# ---------- 真实公开研报（9 月初） ----------
def _real():
    return load_docs(Path(__file__).resolve().parent.parent / "data" / "real_docs")


def test_real_docs_stances():
    docs = {d.publisher + d.published_on.isoformat(): d for d in _real()}
    p = {x.symbol: x for x in rule_points(docs["光大期货2026-09-03"], ["RB", "I"])}
    assert p["RB"].stance == "中性" and p["I"].stance == "中性"          # 「需求端支撑有限」不能盖过结论句「震荡整理」
    p = {x.symbol: x for x in rule_points(docs["华泰期货2026-09-03"], ["CU"])}
    assert p["CU"].stance == "偏多" and "逢低买入" in p["CU"].summary    # 多段同品种：策略段胜过行情段
    p = {x.symbol: x for x in rule_points(docs["光大期货2026-09-02"], ["CU"])}
    assert p["CU"].stance == "中性"                                       # 「方向判断的难度」
    p = {x.symbol: x for x in rule_points(docs["中信建投期货2026-09-02"], ["SC", "TA"])}
    assert p["SC"].stance == "偏多" and p["TA"].stance == "偏多"          # 「产销：……回落」不属于策略块
    p = {x.symbol: x for x in rule_points(docs["南华期货2026-09-03"], ["M", "SC", "C"])}
    assert p["M"].stance == "中性" and "SC" not in p                      # 「油脂：」段里顺带提到的原油不算原油观点
    assert p["C"].stance == "中性"                                        # 「上涨乏力」不算偏多


def test_real_docs_brief_and_ask():
    docs = _real()
    b = build_brief(docs, Profile(watch=["RB", "I", "CU", "SC", "M"]), as_of=date(2026, 9, 4))
    sec = {s.symbol: s for s in b.sections}
    assert sec["CU"].divergence and "华泰期货" in sec["CU"].divergence and "光大期货" in sec["CU"].divergence
    ans, hits = ask("看多铜的那家理由是什么", docs)
    assert hits and "华泰期货" in hits[0][0] and all("华泰期货" in src for src, _ in hits)
    ans, hits = ask("光大对铜的观点是什么", docs)
    assert hits and all("光大期货" in src for src, _ in hits)
