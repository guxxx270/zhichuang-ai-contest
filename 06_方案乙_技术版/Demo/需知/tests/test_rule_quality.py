"""规则引擎产出质量：问清抽取、追问、写单、定架在演示样例和几类典型原话上的回归。"""
from __future__ import annotations

import json
import re

import pytest

from xuzhi import config, textutil
from xuzhi.llm import LLM
from xuzhi.pipeline import analyze
from xuzhi.pipeline.intake import extract_card
from xuzhi.pipeline.rule_reference import score_dims

SAMPLES = {p.stem[:2]: p.read_text(encoding="utf-8") for p in config.SAMPLES_DIR.glob("0[1-5]_*.md")}
CASES = {c["id"]: c for c in json.loads((config.DATA_DIR / "eval" / "eval_set.json").read_text(encoding="utf-8"))["cases"]}
HR = ("来源：企业微信 · 人力资源部 小何 → 技术部\n\n每个月月初把当月过生日的员工整理出来，提前一天提醒部门负责人，"
      "现在都是手工翻花名册，发企业微信或者手机短信都行。")
VAGUE = "那个报表能不能优化一下，越快越好。"
OPENING = ("来源：邮件 · 营业部 客户经理 小赵 → 技术部\n主题：关于新开户资料核对的需求\n\n技术部同事：\n想做一个开户资料核对工具：\n"
           "1. 每天把新开户客户的资料和影像件做比对，缺项的标红；\n2. 核对结果发到我邮箱。\n最近经常出错，人工核对太慢。\n谢谢！")
FUTURES_WORDS = ("品种", "合约", "下单", "投资经理", "交易日")


@pytest.fixture(scope="module")
def runs():
    llm = LLM(mode="mock")
    return {k: analyze(t, llm=llm) for k, t in SAMPLES.items()}


def _ids(a) -> set[str]:
    return {q.id for q in a.questions}


def _decision(a, did):
    return next((d for d in a.architecture.decisions if d.id == did), None)


# ---------------- 问清：功能点 ----------------
def test_salutation_led_sentence_keeps_its_request(runs):
    """「小李，」「小李你好，」开头的整句是需求本身，不能当寒暄丢掉。"""
    assert any("较上周变动" in f for f in runs["05"].card.features)
    for text in ("小李，能不能做个保证金占用日报，每天结算后发我。", "小李你好，能不能做个保证金占用日报，每天结算后发我。",
                 "王经理，能不能做个保证金占用日报，每天结算后发我。", "@小周 能不能做个保证金占用日报，每天结算后发我。"):
        feats = extract_card(text).features
        assert feats and feats[0].startswith("保证金占用日报"), (text, feats)
    assert extract_card("小李在吗，跟你说个事。").features == []
    # 姓氏表之外的「汇总：」「员工，」不是称呼；「小于 80%」「周总结」后面不是标点，也不剥
    for s in ("汇总：各部门的报销金额", "员工，每月过生日的要提醒", "小于 80% 的标红", "周总结要发给领导"):
        assert textutil.FILLER.sub("", s) == s


def test_samples_keep_their_features_except_pure_constraints(runs):
    assert runs["01"].card.features == [
        '净值日报能不能加一列"较上一交易日变动"，就放在单位净值旁边', "风险指标也放进去，回撤、集中度那几个，风控那边每天都有的",
        "周五给客户的那版也要，客户版你看着把不该给的去掉", "产品还是现在那 20 只，新发的两只也加上"]
    assert runs["03"].card.features == [
        '下单前自动提示一下"这笔下去会不会超限仓"，别等拒单了才知道', "我们几个产品都需要，尤其临近交割月的时候",
        "如果能顺便把还能下多少手也算出来就更好了"]
    # 只有约束的句子（手机能看、夜间、信息安全）只进非功能，不进「要做什么」
    assert runs["02"].card.features == ["基差监控页面，主要品种都要，螺纹、热卷、铁矿、焦炭这些",
                                        "期现价差每天更新，异常了要提醒一下，不用太复杂，主要是别漏掉大的变化"]
    assert "手机端可看" in runs["02"].card.nonfunctional
    feats4 = runs["04"].card.features
    assert len(feats4) == 6 and feats4[-1] == "公告发布后 30 分钟内完成测算与推送"
    assert not any(f.startswith(("需支持夜间", "涉及客户信息的通知")) for f in feats4)
    assert {"夜间 / 夜盘时段可用", "客户信息脱敏"} <= set(runs["04"].card.nonfunctional)


def test_feature_cues_cover_delivery_and_column_edits():
    card = extract_card(OPENING)
    assert "核对结果发到我邮箱" in card.features
    assert not any(f.endswith(("：", ":")) or f == "开户资料核对工具" for f in card.features)
    assert not any("经常出错" in f for f in card.features)
    for text in ("资管运营：净值日报把回撤这一列去掉吧，没人看。", "净值日报把集中度改名为持仓集中度"):
        assert extract_card(text).features, text
    assert textutil.find_removed_columns("净值日报把回撤这一列去掉吧") == ["回撤"]
    assert textutil.find_renamed_columns("净值日报把集中度改名为持仓集中度") == [("集中度", "持仓集中度")]


def test_mobile_and_lots_are_not_bare_keywords(runs):
    """单字「手」、隐盾换掉的手机号不能触发持仓 / 手机端。"""
    assert not any(d.startswith("持仓") for d in runs["02"].card.data_sources)
    assert "手机端" not in runs["03"].card.channels and "T24" not in _ids(runs["03"])
    assert "手机端可看" not in runs["03"].card.nonfunctional
    assert not any(d.startswith("持仓") for d in analyze(HR, llm_polish=False).card.data_sources)
    assert any(d.startswith("持仓") for d in extract_card("拟下单 300 手前先算一下").data_sources)
    assert "T06" not in _ids(runs["02"])


def test_data_source_role_is_not_a_user(runs):
    card = runs["01"].card
    assert "风控" not in card.users and "风控定时拿到" not in card.goal and card.users[0] == "资管运营"


# ---------------- 问清：时效、标题 ----------------
def test_frequency_deadline_and_trigger(runs):
    assert runs["01"].card.frequency == "每日"
    assert runs["04"].card.frequency == "事件后限时" and runs["04"].card.deadline == ""
    assert "期望上线" not in runs["04"].spec.business_md
    assert runs["05"].card.frequency == "每周"
    hr = analyze(HR, llm_polish=False).card
    assert hr.frequency == "每月" and hr.trigger == "定时"
    assert analyze(CASES["E06"]["text"], llm_polish=False).card.frequency == "每日"
    assert analyze(CASES["E20"]["text"], llm_polish=False).card.frequency == "每日"
    dims, reasons = score_dims(runs["04"].card, runs["04"].redacted_text)   # 事件后限时仍按实时计规则参考（实时 4 + 五家交易所 1）
    assert dims["性能"] == 5 and reasons["性能"].startswith("实时 / 盘中")


def test_nav_daily_template_only_for_change_and_risk_columns(runs):
    assert runs["01"].card.title == "净值日报改版：新增变动列与风险指标"
    a = analyze("净值日报改一下发送时间，改成每天晚上 8 点发", llm_polish=False)
    assert "变动" not in a.card.title and not a.card.title_from_template
    assert not [q.id for q in a.questions if q.id.startswith("C_")]   # 没点名加列，就没有展示格式题，更不会问「变动」列
    heads = re.findall(r"<th[^>]*>(.*?)</th>", a.prototype_html)
    assert heads and not any("变动" in h for h in heads)
    assert analyze(CASES["E17"]["text"], llm_polish=False).card.title == "净值日报改版：新增同类产品对比列"


def test_titles_from_subject_and_first_clause(runs):
    assert extract_card(OPENING).title == "新开户资料核对"
    assert runs["05"].card.title != "单位净值、较上周变动报表" and "较上周变动" in runs["05"].card.title
    e19 = analyze(CASES["E19"]["text"], llm_polish=False).card.title
    assert e19 == "各部门每个月提了多少需求、完成多少、平均多久"
    # 苹果、玻璃在没有期货语境时是日常用词，不算品种
    assert extract_card("年会要采购一批苹果手机和玻璃奖杯，能不能做个页面登记大家的领取情况。").symbols == []
    assert extract_card("苹果主力合约和玻璃期货的价差做个监控").symbols == ["苹果", "玻璃"]


# ---------------- 问清：追问 ----------------
def test_client_mention_is_not_client_facing(runs):
    a3 = runs["03"]
    assert not a3.card.client_facing and "客户" not in a3.card.users
    assert not {"T15", "T36"} & _ids(a3) and _decision(a3, "D8") is None
    assert not any("client-view" in x for x in a3.spec.api)
    for k in ("01", "04", "05"):
        assert "T15" in _ids(runs[k]) and runs[k].card.client_facing, k
    e20 = analyze(CASES["E20"]["text"], llm_polish=False)
    assert "T37" in _ids(e20) and "T15" not in _ids(e20)


def test_weekly_report_asks_weekly_baseline(runs):
    ids5 = _ids(runs["05"])
    assert "T05W" in ids5 and "T05" not in ids5 and "T05" in _ids(runs["01"])
    assert "T05W" not in _ids(analyze(CASES["E05"]["text"], llm_polish=False))   # 仓单库存周报没有净值基准可问


@pytest.mark.parametrize("key, min_highs", [("E10", 2), ("E11", 3), ("E19", 3), ("HR", 2)])
def test_non_futures_wording_has_no_futures_terms(key, min_highs):
    a = analyze(HR if key == "HR" else CASES[key]["text"], llm_polish=False)
    for blob in (a.message, a.spec.business_md, a.spec.tech_md):
        assert not [w for w in FUTURES_WORDS if w in blob], blob
    # 中性措辞只换说法，不丢高影响题：未答复的高影响题全部出现在确认消息里，条数不少于改措辞之前
    highs = [q for q in a.questions if q.impact == "高" and not q.answer.strip()]
    assert len(highs) >= min_highs and all(q.question in a.message for q in highs)
    assert len(re.findall(r"^\d+\. ", a.message, flags=re.M)) >= min_highs


def test_vague_one_liner_asks_what_and_why_first(runs):
    a = analyze(VAGUE, llm_polish=False)
    assert a.questions[0].id == "Q_VAGUE" and a.questions[0].impact == "高"
    assert "原话信息不足，先确认对象与问题" in a.card.assumptions
    for k, run in runs.items():
        assert "Q_VAGUE" not in _ids(run), k
    assert "Q_VAGUE" not in _ids(analyze("每月初提醒部门负责人下个月过生日的员工，发企业微信。", llm_polish=False))


def test_sign_color_follows_red_up_green_down():
    a = analyze("净值日报能不能加一列对标指数", llm_polish=False)
    sign = next(q for q in a.questions if q.id == "C_SIGN")
    assert "红涨绿跌" in sign.question and "红跌绿涨" not in sign.question


# ---------------- 写单 ----------------
def test_spec_is_built_from_this_demand(runs):
    assert "二期：对账" not in runs["01"].spec.tech_md
    for a in runs.values():
        assert all(not s.want.startswith("能不能") and "能不能" not in s.want for s in a.spec.stories)
    weekly = {cn: tp for cn, _f, tp in runs["05"].spec.fields}
    assert "较上周变动" in weekly and "上周" in weekly["较上周变动"] and "较上一交易日变动" not in weekly
    daily = {cn: tp for cn, _f, tp in runs["01"].spec.fields}
    assert "上一交易日" in daily["较上一交易日变动"]
    acc = [x for s in runs["01"].spec.stories for x in s.acceptance]
    assert any("报表含「较上一交易日变动」列" in x for x in acc)
    assert any("还可下" in x for s in runs["03"].spec.stories for x in s.acceptance)


# ---------------- 定架 ----------------
def test_architecture_app_layer_uses_short_labels(runs):
    for k, a in runs.items():
        boxes = a.architecture.layers["应用"]
        assert boxes and all(len(b) <= 16 for b in boxes), (k, boxes)
        assert not set(boxes) & set(a.card.features), k
