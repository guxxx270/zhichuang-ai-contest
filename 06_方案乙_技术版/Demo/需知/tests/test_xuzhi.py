from pathlib import Path

import pytest

from xuzhi import config, knowledge
from xuzhi.pipeline import analyze
from xuzhi.pipeline.intake import extract_card
from xuzhi.privacy import Redactor

SAMPLES = sorted(config.SAMPLES_DIR.glob("*.md"))


@pytest.fixture(scope="module")
def results():
    return {p.stem[:2]: analyze(p.read_text(encoding="utf-8")) for p in SAMPLES}


def test_knowledge_loads():
    assert len(knowledge.probes()) >= 30 and len(knowledge.systems()) >= 10 and len(knowledge.history()) >= 30


def test_redactor_roundtrip():
    s = "客户张先生（资金账号 8812345678901，手机 13912345678，邮箱 a@b.com）。营业部客户经理。涉及客户信息的通知需符合公司信息安全要求。"
    r = Redactor().redact(s)
    assert "8812345678901" not in r.text and "13912345678" not in r.text and "a@b.com" not in r.text
    assert "客户经理" in r.text and "公司信息安全要求" in r.text          # 不误伤角色词与普通句子
    assert Redactor.restore(r.text, r.mapping) == s


def test_types_and_titles(results):
    assert results["01"].card.req_type == "报表" and "净值日报" in results["01"].card.title
    assert results["02"].card.req_type == "页面" and "基差" in results["02"].card.title
    assert results["03"].card.req_type == "提醒" and "限仓" in results["03"].card.title
    assert "公告" in results["04"].card.title or "保证金" in results["04"].card.title


def test_futures_questions_present(results):
    ids2 = {q.id for q in results["02"].questions}
    assert {"T03", "T09"} <= ids2                       # 主力合约切换、现货来源——期货特有
    ids3 = {q.id for q in results["03"].questions}
    assert {"T17", "T18"} <= ids3                       # 三套限仓口径、阻断还是提示
    for a in results.values():
        assert 6 <= len(a.questions) <= 12
        assert a.questions[0].impact == "高"


def test_estimate_ranges(results):
    for a in results.values():
        e = a.estimate
        assert 0 < e.low < e.mid < e.high
        assert set(e.dims) == {"功能点", "数据接入", "界面", "权限", "性能", "合规"}
        assert all(1 <= v <= 5 for v in e.dims.values())
    assert results["04"].estimate.mid > results["01"].estimate.mid    # 大需求估得更多


def test_answers_change_outputs(results):
    a = results["01"]
    qid = next(q.id for q in a.questions if q.impact == "高")
    b = analyze(a.raw_text, answers={qid: "以结算价为准，夜盘计入次日"})
    assert "✅" in b.spec.business_md and b.estimate.unanswered_high == a.estimate.unanswered_high - 1


def test_reuse_and_decisions(results):
    assert results["01"].architecture.reuse[0].system == "报表平台"
    ids = {d.id for d in results["03"].architecture.decisions}
    assert "D7" in ids                                   # 触及交易链路 → 是否改交易链路
    assert results["04"].architecture.reuse[0].system == "公告采集服务"
    assert "ADR" in results["01"].architecture.adr_md


def test_prototype_html(results):
    for a in results.values():
        assert a.prototype_html.startswith("<!doctype html>") and a.card.title in a.prototype_html
    assert "追加资金" in results["04"].prototype_html
    assert "提醒设置" in results["03"].prototype_html


def test_free_text_works():
    a = analyze("能不能做个页面，每天看各产品的保证金占用和风险度，超过 80% 提醒我，手机也要能看")
    assert a.card.req_type == "页面" and "保证金占用" in a.card.indicators and "手机端可看" in a.card.nonfunctional
    assert a.estimate.mid > 0 and len(a.questions) >= 6


def test_dual_track(results):
    for a in results.values():
        e = a.estimate
        assert e.tasks and abs(sum(t.trad_days for t in e.tasks) - e.mid) < 0.6      # 拆分守恒
        assert 0 < e.ai_mid < e.mid and 10 <= e.saving_pct <= 70
        assert abs(sum(e.who.values()) - 1.0) < 0.05
        assert {t.who for t in e.tasks} == {"AI 能做", "AI 做人审", "人必须做"}
        assert all(t.who != "AI 能做" for t in e.tasks if "验收" in t.name or "口径确认" in t.name)
    r1 = results["03"]
    assert any("交易链路" in t.name and t.who == "AI 做人审" for t in r1.estimate.tasks)   # 交易链路只能 AI 做人审
    plain = analyze("做个页面，每天看各品种主力合约的持仓量和成交量排名，能导出 Excel")
    assert "自建" in plain.estimate.delivery                                           # 无客户 / 交易风险的标准页面 → 业务自建路径


def test_rules_only_is_instant():
    a = analyze("净值日报能不能加一列较上一交易日变动", llm_polish=False)
    assert a.engine == "规则" and a.seconds < 1.0


def test_polish_guard():
    from xuzhi.pipeline.spec import _polish_ok
    orig = "# T\n\n## 1. a\n- ◻︎默认 x → y\n\n## 2. b\ntext\n\n## 3. c\n- ✅ q → r\n"
    assert _polish_ok(orig, orig.replace("text", "润色后的文字"))
    assert not _polish_ok(orig, "# T\n\n## 1. a\n")                     # 丢章节
    assert not _polish_ok(orig, orig.replace("◻︎默认 ", ""))               # 丢标记
