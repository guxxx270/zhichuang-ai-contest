from pathlib import Path

import pytest

from xuzhi import config, knowledge
from xuzhi.pipeline import analyze
from xuzhi.pipeline.intake import extract_card
from xuzhi.privacy import Redactor

SAMPLES = sorted(config.SAMPLES_DIR.glob("*.md"))


@pytest.fixture(scope="module")
def results():
    from xuzhi.llm import LLM
    llm = LLM(mode="mock")
    return {p.stem[:2]: analyze(p.read_text(encoding="utf-8"), llm=llm) for p in SAMPLES}


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
        assert e.status == "unavailable" and e.method == "unavailable"
        assert all(value is None for value in (e.low, e.mid, e.high, e.ai_low, e.ai_mid, e.ai_high))
        assert not e.tasks and not e.dims


def test_answers_change_outputs(results):
    from xuzhi.llm import LLM
    a = results["01"]
    qid = next(q.id for q in a.questions if q.impact == "高")
    b = analyze(a.raw_text, llm=LLM(mode="mock"), answers={qid: "以结算价为准，夜盘计入次日"})
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
    assert "较上一交易日变动" in results["01"].prototype_html


def test_added_column_in_prototype():
    from xuzhi.llm import LLM
    a = analyze("净值日报能不能加一列对标指数，就放在单位净值旁边，盘后给投研看。", llm=LLM(mode="mock"))
    html = a.prototype_html
    assert "对标指数" in a.card.indicators
    import re
    assert len(re.findall(r"<th[^>]*>对标指数", html)) == 1
    assert ">净值</th>" not in html and ">净值 <span" not in html
    # 「放在单位净值旁边」：新列紧跟单位净值，表头标新增
    assert re.search(r">单位净值</th>\s*<th[^>]*>对标指数 <span class=\"tag md\">新增</span>", html)


def test_indicator_case_variants_deduped():
    """原话 sortino + 模型写成 Sortino 时，指标只保留一份（保留原话写法）。"""
    from types import SimpleNamespace

    from xuzhi.pipeline.intake import extract_card, refine_with_llm

    text = "报表加一列sortino"
    card = extract_card(text)
    assert card.indicators == ["sortino"]

    llm = SimpleNamespace(
        mode="api",
        chat_json=lambda system, user: {
            "title": "加 Sortino 列",
            "goal": "展示 Sortino",
            "features": ["加一列Sortino"],
            "extra_questions": [],
        },
    )
    refined, _ = refine_with_llm(text, card, [], llm)
    assert refined.indicators == ["sortino"]
    assert "Sortino" not in refined.indicators


def test_free_text_works():
    from xuzhi.llm import LLM
    a = analyze("能不能做个页面，每天看各产品的保证金占用和风险度，超过 80% 提醒我，手机也要能看", llm=LLM(mode="mock"))
    assert a.card.req_type == "页面" and "保证金占用" in a.card.indicators and "手机端可看" in a.card.nonfunctional
    assert a.estimate.mid is None and a.estimate.status == "unavailable" and len(a.questions) >= 6


def test_dual_track(results):
    for a in results.values():
        e = a.estimate
        assert e.mid is None and e.ai_mid is None and e.saving_pct is None
        assert not e.tasks and not e.who and not e.phases and not e.ai_phases
    plain = analyze("做个页面，每天看各品种主力合约的持仓量和成交量排名，能导出 Excel")
    assert plain.estimate.status == "unavailable"  # 没有模型，不能从需求类型生成两轨工时。


def test_rules_only_is_instant():
    a = analyze("净值日报能不能加一列较上一交易日变动", llm_polish=False)
    assert a.engine == "规则" and a.seconds < 1.0


def test_polish_guard():
    from xuzhi.pipeline.spec import _polish_ok
    orig = "# T\n\n## 1. a\n- ◻︎默认 x → y\n\n## 2. b\ntext\n\n## 3. c\n- ✅ q → r\n"
    assert _polish_ok(orig, orig.replace("text", "润色后的文字"))
    assert not _polish_ok(orig, "# T\n\n## 1. a\n")                     # 丢章节
    assert not _polish_ok(orig, orig.replace("◻︎默认 ", ""))               # 丢标记


def test_demo_samples_prototype_quality():
    """演示样例的出样不能退化：样例 ① 出真表格且新列紧跟单位净值；引号里的列名不当 Tab；人名不当标识；
    用示例底稿时不凭一句话造空卡片；客户版隐去集中度。"""
    import re

    from xuzhi import config
    from xuzhi.drafts import parse_draft
    from xuzhi.llm import LLM
    from xuzhi.pipeline.prototype import apply_prototype_view

    s1 = next(config.SAMPLES_DIR.glob("01_*.md")).read_text(encoding="utf-8")
    page = analyze(s1, llm=LLM(mode="mock")).prototype_html
    assert "稳健一号" in page and "data-xz-tab" not in page and 'class="tab' not in page
    heads = [re.sub(r"<[^>]+>|新增", "", h).strip() for h in re.findall(r"<th[^>]*>(.*?)</th>", page)]
    assert heads[:3] == ["产品", "单位净值", "较上一交易日变动"] and "集中度" in heads
    client = apply_prototype_view(page, client_view=True)
    assert ">集中度" not in client and "客户版 · 已脱敏" in client
    s2 = next(config.SAMPLES_DIR.glob("02_*.md")).read_text(encoding="utf-8")
    assert "Jerry" not in analyze(s2, llm=LLM(mode="mock")).prototype_html
    dp = next((config.ROOT / "data" / "drafts").glob("*.html"))
    drafted = analyze(s1, llm=LLM(mode="mock"), drafts=[parse_draft(dp.name, dp.read_text(encoding="utf-8"))]).prototype_html
    assert "xz-demand-patch-tabs" not in drafted and "确认</button>" not in drafted
    assert re.search(r">单位净值</th>\s*<th[^>]*>较上一交易日变动</th>", drafted) and ">集中度</th>" in drafted


def test_memory_demo_sample_recalls_answers():
    """演示脚本：样例 ① 答了结算时点与客户版脱敏 → 样例 ⑤（同一提出方的净值周报）这两条直接沿用、不再问；
    周报的比较基准是上周，另问 T05W，不能把日报的「上一交易日」口径沿用过去。"""
    from xuzhi import config
    from xuzhi.llm import LLM
    from xuzhi.memory import Memory

    mem = Memory(config.LEDGER_DB.with_name("memory_demo.sqlite3"))
    mem.forget()
    llm = LLM(mode="mock")
    s1 = next(config.SAMPLES_DIR.glob("01_*.md")).read_text(encoding="utf-8")
    ids1 = [q.id for q in analyze(s1, llm=llm, memory=mem).questions]
    assert ids1[:2] == ["T01", "T05"] and "T15" in ids1  # 先问期货口径：结算时点、比较基准；周五给客户的那版要问脱敏
    a = analyze(s1, llm=llm, answers={"T01": "结算后为准，夜盘算下一交易日", "T15": "客户版只留本产品净值，去掉风险指标"}, memory=mem)
    assert mem.learn(a.card, a.questions, 1) == 2
    s5 = next(config.SAMPLES_DIR.glob("05_*.md")).read_text(encoding="utf-8")
    b = analyze(s5, llm=llm, memory=mem)
    ids5 = {q.id for q in b.questions}
    assert {q.id for q in b.questions if q.recalled} == {"T01", "T15"} and "C_POS" not in ids5
    assert "T05W" in ids5 and "T05" not in ids5 and not next(q for q in b.questions if q.id == "T05W").recalled
