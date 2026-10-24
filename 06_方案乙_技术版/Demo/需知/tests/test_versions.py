"""需求版本对比：快照 / 差异 / 需重新确认 / 变更说明；一本账 versions 表（老库兼容、去重）；服务层、REST、MCP、演示数据。"""
from __future__ import annotations

import asyncio
import copy
import json
import sqlite3

import pytest

from xuzhi import config, knowledge, service, versions
from xuzhi.ledger import Ledger
from xuzhi.llm import LLM
from xuzhi.memory import Memory
from xuzhi.pipeline import analyze

SAMPLE1 = next(config.SAMPLES_DIR.glob("01_*.md")).read_text(encoding="utf-8")
ADDED = "另外再加一列累计净值，盘中也要能看。"
ANSWERS = {"T01": "结算后为准，夜盘算下一交易日", "T05": "上一交易日结算价，不复权"}


def _analyze(text: str = SAMPLE1, answers: dict | None = None):
    return analyze(text, llm=LLM(mode="mock"), answers=answers, llm_polish=False)


def _snap(text: str = SAMPLE1, answers: dict | None = None) -> dict:
    return versions.snapshot(_analyze(text, answers))


def _model(before: tuple, after: tuple) -> dict:
    keys = ("low", "mid", "high")
    return {"before": dict(zip(keys, before)), "after": dict(zip(keys, after))}


@pytest.fixture
def deps(tmp_path):
    return Ledger(tmp_path / "v.sqlite3"), Memory(tmp_path / "v.sqlite3")


# ---------------- 快照与差异 ----------------
def test_snapshot_is_compact_json_without_raw_text():
    snap = _snap(SAMPLE1 + "客户张伟 13812345678。")
    body = json.dumps(snap, ensure_ascii=False)
    assert set(snap) >= {"title", "req_type", "requester", "goal", "features", "indicators", "symbols", "data_sources",
                         "channels", "deadline", "nonfunctional", "questions", "estimate", "data_map", "decisions"}
    assert "13812345678" not in body and len(snap["text_sig"]) == 16
    assert set(snap["questions"][0]) == {"id", "question", "impact", "answer", "default"}
    assert set(snap["data_map"][0]) == {"need", "system", "interface", "freshness", "status"}
    assert set(snap["decisions"][0]) == {"id", "topic", "recommended"}
    # 没调模型：模型区间不记；规则参考只记以前 / 现在参考值（小时）；历史只记条数
    assert _snap()["estimate"] == {"status": "unavailable", "model": None, "rule": {"before": 80.8, "after": 50.08}, "history": 3}
    assert versions.text_sig(" a\nb ") == versions.text_sig("ab") != versions.text_sig("abc")


def test_answering_two_questions_is_the_only_change():
    d = versions.diff(_snap(), _snap(answers=ANSWERS), 1, 2)
    assert d.changed and not d.text_changed
    assert {q["id"] for q in d.questions["answered"]} == {"T01", "T05"}
    assert next(q for q in d.questions["answered"] if q["id"] == "T01")["answer"] == ANSWERS["T01"]
    assert d.summary == "相对 v1：已答 +2"
    assert d.reconfirm == [] and not d.features["added"] and not d.estimate["changed"]


def test_feature_added_flows_into_elements_estimate_architecture_and_reconfirm():
    d = versions.diff(_snap(answers=ANSWERS), _snap(SAMPLE1 + ADDED, ANSWERS), 2, 3)
    assert d.text_changed
    assert d.features["added"] == ["再加一列累计净值，盘中也要能看"] and not d.features["removed"]
    fields = {x["key"]: x for x in d.fields}
    assert fields["indicators"]["added"] == ["累计净值"] and "时效要求（实时 / 限时）" in fields["nonfunctional"]["added"]
    assert set(fields) == {"indicators", "data_sources", "nonfunctional", "goal"}   # 一句话理解随指标变了，也列出来
    assert [q["id"] for q in d.questions["new"]] == ["T23"] and [q["id"] for q in d.questions["dropped"]] == ["T22"]
    assert {q["id"] for q in d.questions["reworded"]} >= {"C_FMT"}           # 列名多了一个，问法跟着变
    rule = {r["track"]: r for r in d.estimate["rule"]["rows"]}
    assert (rule["before"]["old"], rule["before"]["new"], rule["before"]["delta"]) == (80.8, 150.4, 69.6)
    assert {r["need"] for r in d.data_map["added"]} >= {"累计净值"}
    assert {c["need"]: c["changes"]["status"] for c in d.data_map["changed"]}["回撤"] == ["可复用", "时效不符"]
    assert d.decisions["changed"] == [{"id": "D2", "topic": "数据来源与时点", "old": "数据仓库日终（T+1）", "new": "行情服务 / 交易系统实时"}]
    kinds = [(r["who"], r["kind"]) for r in d.reconfirm]
    assert kinds == [("业务", "新问题"), ("IT", "决策"), ("数据", "数据地图")]
    assert "时效不符" in d.reconfirm[-1]["text"] and "累计净值" in d.reconfirm[-1]["text"]
    assert d.summary.startswith("相对 v2：原话已改 · 功能点 +1 · 要素变化 4 · 待确认 +1/−1 · 规则参考（以前）80.8→150.4 小时")
    assert d.summary.endswith("决策变化 1 · 需重新确认 3 项")


def test_estimate_model_and_rule_are_compared_separately_never_mixed():
    base = _snap()
    old, new = copy.deepcopy(base), copy.deepcopy(base)
    new["estimate"] = {"status": "ready", "model": _model((4, 9, 15), (2.5, 6, 10.5)), "rule": {"before": 92, "after": 57}, "history": 3}
    d = versions.diff(old, new, 1, 2)
    model, rule = d.estimate["model"], d.estimate["rule"]
    # 旧版没有模型结果：模型只并列新数，不拿规则 80.8 去算模型的增减
    assert all(r["delta"] is None and r["pct"] is None and r["old"] is None for r in model["rows"])
    assert [r["new"] for r in model["rows"]] == [9, 6] and "不算增减" in model["note"]
    assert [(r["old"], r["new"], r["delta"], r["pct"]) for r in rule["rows"]] == [(80.8, 92, 11.2, 13.9), (50.08, 57, 6.92, 13.8)]
    assert "新出模型估时" in d.summary and "规则参考（以前）80.8→92 小时" in d.summary
    md = versions.diff_markdown(d, "净值日报", 1, 2)
    assert "| 模型估时 | 以前（人工开发） | — | 9（4～15） | 不比较 |" in md
    assert "| 规则参考 | 以前（人工开发） | 80.8 | 92 | +11.2（+13.9%） |" in md

    # 两版都有模型结果：模型和模型比；规则没动就不列
    newer = copy.deepcopy(new)
    newer["estimate"]["model"] = _model((6, 12, 20), (3, 7, 12))
    d2 = versions.diff(new, newer, 2, 3)
    rows = {r["track"]: r for r in d2.estimate["model"]["rows"]}
    assert (rows["before"]["delta"], rows["before"]["pct"], rows["after"]["delta"]) == (3, 33.3, 1)
    assert rows["before"]["old_range"] == "4～15" and rows["before"]["new_range"] == "6～20"
    assert d2.estimate["rule"]["rows"] == [] and "模型估时（以前）9→12 小时" in d2.summary and "规则参考" not in d2.summary

    # 新版模型失败：旧数字只作对照，不算增减
    failed = copy.deepcopy(newer)
    failed["estimate"].update(status="failed", model=None)
    d3 = versions.diff(newer, failed, 3, 4)
    assert all(r["delta"] is None and r["new"] is None for r in d3.estimate["model"]["rows"])
    assert "调用未成功" in d3.estimate["model"]["note"] and "模型估时本版无结果" in d3.summary


def test_data_map_changes_and_which_rows_need_reconfirmation():
    def row(need, status, system="风控系统"):
        return {"need": need, "system": system, "interface": "日终指标表", "freshness": "T+1", "status": status}

    old, new = _snap(), _snap()
    old["data_map"] = [row("回撤", "可复用"), row("客户信息", "需申请"), row("集中度", "可复用")]
    new["data_map"] = [row("回撤", "需申请"), row("客户信息", "可复用"), row("资讯", "需新建", "—"), row("杠杆", "可复用")]
    d = versions.diff(old, new, 1, 2)
    assert [r["need"] for r in d.data_map["added"]] == ["资讯", "杠杆"]
    assert [r["need"] for r in d.data_map["removed"]] == ["集中度"]
    assert {c["need"]: c["changes"] for c in d.data_map["changed"]} == {
        "回撤": {"status": ["可复用", "需申请"]}, "客户信息": {"status": ["需申请", "可复用"]}}
    data = [r["text"] for r in d.reconfirm if r["kind"] == "数据地图"]
    assert len(data) == 2 and "「需申请」1 项（回撤）" in data[0] and "「需新建」1 项（资讯）" in data[1]
    assert not any("客户信息" in t or "杠杆" in t for t in data), "变好的、新增可复用的不用再确认"
    assert "数据地图 +2/−1 · 数据项变化 2" in d.summary


def test_reconfirm_rules_for_questions_features_and_decisions():
    old, new = _snap(), _snap()
    q = lambda qid, impact, text, answer="": {"id": qid, "question": text, "impact": impact, "answer": answer, "default": "按默认"}  # noqa: E731
    old["questions"] = [q("T01", "高", "按什么时点？", "结算后"), q("L1", "中", "模型补问甲？"), q("T09", "低", "要不要导出？")]
    new["questions"] = [q("T01", "高", "按什么时点？夜盘算哪天？", "结算后"), q("L1", "中", "模型补问乙？"),
                        q("T30", "高", "谁验收？"), q("T31", "高", "要不要留痕？", "要"), q("T32", "中", "多久一次？")]
    old["features"] = ["净值日报加一列较上一交易日变动", "客户版去掉内部字段", "新发的两只产品也加上"]
    new["features"] = ["净值日报加一列较上一交易日变动，放在单位净值旁边", "客户版去掉内部字段"]
    old["decisions"] = [{"id": "D1", "topic": "新建 vs 复用", "recommended": "复用报表平台"},
                        {"id": "D5", "topic": "权限模型", "recommended": "角色 + 产品"}]
    new["decisions"] = [{"id": "D1", "topic": "新建 vs 复用", "recommended": "新建独立页面"},
                        {"id": "D3", "topic": "时效", "recommended": "分钟级"}]
    new["text_sig"] = "0" * 16   # 原话改过：删掉的功能点才要找业务确认
    d = versions.diff(old, new, 1, 2)
    assert d.features["reworded"] == [{"old": "净值日报加一列较上一交易日变动", "new": "净值日报加一列较上一交易日变动，放在单位净值旁边"}]
    assert d.features["removed"] == ["新发的两只产品也加上"] and not d.features["added"]
    assert [x["id"] for x in d.questions["new"]] == ["L1", "T30", "T31", "T32"]   # L 号是模型临时补问：同号不同题算新题
    assert [x["id"] for x in d.questions["dropped"]] == ["L1", "T09"]
    assert [x["id"] for x in d.questions["reworded"]] == ["T01"]
    texts = [r["text"] for r in d.reconfirm]
    assert texts[0].startswith("新增高影响问题：谁验收？") and len([t for t in texts if t.startswith("新增高影响问题")]) == 1
    assert any(t.startswith("问题范围变了") and "结算后" in t for t in texts)
    assert any("新发的两只产品也加上" in t for t in texts)
    assert any(t.startswith("决策 D1") and "新建独立页面" in t for t in texts) and any(t.startswith("新增决策 D3") for t in texts)
    assert not any("D5" in t for t in texts), "删掉的决策只列出，不要求拍板"
    assert d.decisions["removed"][0]["id"] == "D5"

    # 原话没改：功能点增删只是抽取 / 模型改写的说法差别，照样列在「功能点」一节，但不拿去问业务
    same_text = copy.deepcopy(new)
    same_text["text_sig"] = old["text_sig"]
    d2 = versions.diff(old, same_text, 1, 2)
    assert d2.features["removed"] == ["新发的两只产品也加上"] and not d2.text_changed
    assert not any(r["kind"] == "功能点" for r in d2.reconfirm)
    # 一版规则初稿、一版含模型结果：即便原话改了，功能点差别也多是说法不同，同样不列；明细和说明里写清缘由
    mixed = copy.deepcopy(new)
    mixed["engine"] = "规则 + 模型"
    d3 = versions.diff(old, mixed, 1, 2)
    assert d3.mixed and d3.text_changed and not any(r["kind"] == "功能点" for r in d3.reconfirm)
    assert d3.to_dict()["mixed"] is True and versions.MIXED_NOTE in versions.diff_markdown(d3, "净值日报", 1, 2)
    assert not d.mixed and versions.MIXED_NOTE not in versions.diff_markdown(d, "净值日报", 1, 2)


def test_answered_question_whose_wording_stays_is_not_reconfirmed():
    """已答的题只是影响档或默认假设变了：原答复照用，不进需重新确认；问法变了才问。"""
    old, new = _snap(answers=ANSWERS), _snap(answers=ANSWERS)
    target = next(q for q in new["questions"] if q["id"] == "T01")
    target.update(impact="中", default="收盘价")
    d = versions.diff(old, new, 1, 2)
    assert [x["id"] for x in d.questions["reworded"]] == ["T01"] and d.reconfirm == []
    target["question"] += "夜盘算哪天？"
    d = versions.diff(old, new, 1, 2)
    assert [r["kind"] for r in d.reconfirm] == ["口径"] and ANSWERS["T01"] in d.reconfirm[0]["text"]


def test_base_index_compares_like_with_like():
    """配了模型时每次答复重算 / 需求变更先存规则初稿、模型补全后再存一版：「本次变更」和上一份同类结果比。"""
    def v(n, label, engine):
        return {"version": n, "label": label, "ts": "", "snapshot": {"engine": engine}}

    found = [v(1, "受理", "规则"), v(2, "模型增强", "规则 + 模型"), v(3, "业务答复重算", "规则"), v(4, "模型增强", "规则 + 模型")]
    assert versions.base_index(found) == 1 and versions.base_index(found, 2) == 0
    assert versions.base_note(found, 1) == "v3（业务答复重算）是模型补全前的规则初稿，这里直接和上一份同样含模型结果的 v2 比。"
    assert versions.base_note(found[:3], 0) == "v2（模型增强）含模型结果，和本版规则初稿不同类，这里和上一份规则初稿 v1 比。"
    assert versions.base_index(found[:2]) == 0 and versions.base_note(found[:2], 0) == ""   # 前面没有同类：比上一版
    rules_only = [v(1, "受理", "规则"), v(2, "业务答复重算", "规则"), v(3, "需求变更", "规则")]
    assert versions.base_index(rules_only) == 1                                            # 不配模型：就是最近两版
    assert versions.base_index([v(1, "受理", ""), v(2, "业务答复重算", "")]) == 0          # 早于引擎记录的快照
    assert versions.kind(_snap()) == "规则" and versions.kind({}) == ""


def test_same_means_nothing_visible_changed():
    """存版本去重与「屏上是不是最新一版」都用 same()：存下来的每一处差别都要在对比里看得见。"""
    base = _snap(answers=ANSWERS)
    shuffled = copy.deepcopy(base)
    for key in ("features", "questions", "data_map", "decisions"):
        shuffled[key].reverse()
    assert versions.same(base, shuffled), "只是顺序变了不算新版本"
    no_model = copy.deepcopy(base)
    no_model["estimate"]["status"] = "failed"
    assert versions.same(base, no_model), "两边都没有模型数字时，未调用 / 失败的状态差别不另存"
    goal = copy.deepcopy(base)
    goal["goal"] += "（模型润色）"
    d = versions.diff(base, goal, 1, 2)
    assert not versions.same(base, goal) and [x["key"] for x in d.fields] == ["goal"] and d.summary == "相对 v1：要素变化 1"
    reworded = copy.deepcopy(base)
    target = next(q for q in reworded["questions"] if q["id"] == "T02")
    target.update(impact="高", default="跳过非交易日，节后补发")
    d = versions.diff(base, reworded, 1, 2)
    assert [x["id"] for x in d.questions["reworded"]] == ["T02"]
    note = versions.reworded_note(d.questions["reworded"][0])
    assert note == "（影响 中→高；默认改为：跳过非交易日，节后补发）"
    assert note in versions.diff_markdown(d, "净值日报", 1, 2)


def test_identical_versions_report_no_change():
    s = _snap(answers=ANSWERS)
    d = versions.diff(s, copy.deepcopy(s), 1, 2)
    assert not d.changed and d.reconfirm == [] and d.summary == "相对 v1：内容无变化"
    assert d.to_dict()["changed"] is False
    assert "两版分析结果一致" in versions.diff_markdown(d, "净值日报", 1, 2)


def test_change_note_markdown_has_sections_and_escapes_table_cells():
    old, new = _snap(answers=ANSWERS), _snap(SAMPLE1 + ADDED, ANSWERS)
    new["title"] = "净值日报改版 | 加累计净值"
    d = versions.diff(old, new, 2, 3)
    md = versions.diff_markdown(d, new["title"], {"version": 2, "label": "业务答复重算", "ts": "2026-10-24T10:00:00"},
                                {"version": 3, "label": "需求变更", "ts": "2026-10-24T10:05:00"})
    assert md.startswith("# 需求变更说明：净值日报改版 | 加累计净值")
    assert "- 版本：v2（业务答复重算 · 2026-10-24 10:00） → v3（需求变更 · 2026-10-24 10:05）" in md
    for head in ("## 需重新确认（3 项）", "## 功能点", "## 要素", "## 待确认清单", "## 工时", "## 数据地图", "## IT 决策"):
        assert head in md
    assert "- [ ] 【IT】决策 D2 数据来源与时点" in md
    assert "| 标题 | 净值日报改版：新增变动列与风险指标 | 净值日报改版 \\| 加累计净值 | 改了 |" in md


# ---------------- 一本账 ----------------
def test_old_ledger_file_without_versions_table_keeps_working(tmp_path):
    path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript("""
    CREATE TABLE requirements(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, title TEXT, source TEXT, req_type TEXT, requester TEXT,
        engine TEXT, n_questions INTEGER, n_answered INTEGER, mid_days REAL, confidence TEXT, redacted INTEGER, payload TEXT);
    CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, req_id INTEGER, action TEXT, detail TEXT);
    CREATE TABLE feedback(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, req_id INTEGER, skill TEXT, score INTEGER, note TEXT);
    INSERT INTO requirements(ts,title,source,req_type,requester,engine,n_questions,n_answered,mid_days,confidence,redacted,payload)
        VALUES('2026-08-01T09:00:00','旧需求','企业微信','报表','资管运营','规则',3,1,NULL,'未估时',0,'{}');
    """)
    conn.commit()
    conn.close()

    ledger = Ledger(path)
    assert ledger.get(1)["title"] == "旧需求" and ledger.dashboard()["count"] == 1
    assert ledger.versions_of(1) == [] and ledger.latest_version(1) is None
    assert ledger.add_version(1, _snap(), "业务答复重算") == 1
    rid = ledger.log_analysis(_analyze())
    assert [v["label"] for v in ledger.versions_of(rid)] == ["受理"]
    assert {t for (t,) in ledger.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")} >= {"versions"}
    Ledger(path)   # 再开一次不报错（建表与加列都是幂等的）


def test_add_version_numbers_and_skips_unchanged_snapshots(deps):
    ledger, _ = deps
    a = _analyze()
    rid = ledger.log_analysis(a)
    first = ledger.versions_of(rid)
    assert [(v["version"], v["label"]) for v in first] == [(1, "受理")] and first[0]["ts"]
    assert first[0]["snapshot"] == json.loads(json.dumps(versions.snapshot(a), ensure_ascii=False))
    assert ledger.add_version(rid, a, "重复") == 1 and len(ledger.versions_of(rid)) == 1
    answered = _analyze(answers=ANSWERS)
    assert ledger.add_version(rid, answered, "业务答复重算") == 2
    assert ledger.add_version(rid, versions.snapshot(answered), "又点了一次") == 2
    assert ledger.add_version(rid, a, "撤回答复") == 3                  # 回到旧内容也是新的一版
    assert ledger.add_version(9999, a, "受理") == 0 and ledger.add_version(0, a) == 0
    assert ledger.conn.execute("SELECT COUNT(*) FROM versions").fetchone()[0] == 3
    assert ledger.stats()["count"] == 1 and ledger.dashboard()["count"] == 1   # 版本不算需求条数
    # Jira 工单走「Jira 重新评估」：即便接口拿它的编号来重算，也不记版本
    from test_jira import STORY
    from xuzhi.jira import parse_issue_text
    from xuzhi.pipeline.assess import assess, calibrate

    jira_rid, _ = ledger.log_jira(assess(parse_issue_text(STORY), calibrate([])), "粘贴")
    assert ledger.add_version(jira_rid, a, "业务答复重算") == 0 and ledger.versions_of(jira_rid) == []


def test_update_analysis_and_record_change_keep_the_same_row(deps):
    ledger, _ = deps
    a = _analyze(answers=ANSWERS)
    rid = ledger.log_analysis(a)
    ledger.set_status(rid, "已确认")
    assert ledger.update_analysis(rid, a) == 1                          # 内容没变：不另存
    changed = _analyze(SAMPLE1 + ADDED, ANSWERS)
    assert ledger.record_change(rid, changed) == 2
    row = ledger.get(rid)
    assert ledger.stats()["count"] == 1 and row["status"] == "已确认" and row["n_questions"] == len(changed.questions)
    assert json.loads(row["payload"])["card"]["features"][-1] == "再加一列累计净值，盘中也要能看"
    assert [v["label"] for v in ledger.versions_of(rid)] == ["受理", "需求变更"]
    event = [e for e in ledger.events_of(rid) if e["action"] == "需求变更"][-1]
    assert event["detail"].startswith("记为 v2，相对 v1：原话已改") and "需重新确认 3 项" in event["detail"]
    assert ledger.record_change(rid, changed) == 2                      # 再交一次同样的：记流水，不另存
    assert ledger.events_of(rid)[-1]["detail"] == "原话与分析结果同 v2，未另存版本"
    with pytest.raises(ValueError):
        ledger.record_change(9999, changed)


def test_record_change_event_compares_with_the_last_version_of_the_same_kind(deps):
    """模型增强之后再需求变更：变更版是规则初稿，流水里的概要和上一份规则初稿比，不把模型改写的功能点算成删除。"""
    ledger, _ = deps
    rid = ledger.log_analysis(_analyze(answers=ANSWERS))               # v1 规则初稿
    enhanced = _analyze(answers=ANSWERS)
    enhanced.engine = "规则 + 模型"
    enhanced.card.features = ["净值日报新增「较上一交易日变动」列", "补充回撤、集中度等风险指标"]
    assert ledger.update_analysis(rid, enhanced) == 2                  # v2 模型增强
    assert ledger.record_change(rid, _analyze(SAMPLE1 + ADDED, ANSWERS)) == 3
    detail = [e for e in ledger.events_of(rid) if e["action"] == "需求变更"][-1]["detail"]
    assert detail.startswith("记为 v3，相对 v1：原话已改 · 功能点 +1 · ") and "需重新确认 3 项" in detail


# ---------------- 服务层 / REST / MCP ----------------
def test_service_run_versions_answers_and_requirement_change(deps):
    ledger, memory = deps
    rid = service.run(SAMPLE1, ledger=ledger, memory=memory).req_id
    with pytest.raises(ValueError, match="只有 1 版"):
        service.version_diff(ledger, rid)
    res = service.run(SAMPLE1, answers=ANSWERS, req_id=rid, ledger=ledger, memory=memory)
    assert res.req_id == rid and [v["label"] for v in ledger.versions_of(rid)] == ["受理", "业务答复重算"]
    out = service.version_diff(ledger, rid)
    assert out["from"]["version"] == 1 and out["to"]["version"] == 2 and out["summary"] == "相对 v1：已答 +2"
    assert out["markdown"].startswith("# 需求变更说明：") and len(out["diff"]["questions"]["answered"]) == 2
    json.dumps(out, ensure_ascii=False)
    # 同一 req_id 发来改过的原话 = 需求变更：整条更新、记流水、存一版
    service.run(SAMPLE1 + ADDED, answers=ANSWERS, req_id=rid, ledger=ledger, memory=memory)
    assert [v["label"] for v in ledger.versions_of(rid)][-1] == "需求变更"
    assert any(e["action"] == "需求变更" for e in ledger.events_of(rid))
    assert ledger.stats()["count"] == 1 and json.loads(ledger.get(rid)["payload"])["card"]["indicators"].count("累计净值") == 1
    assert service.version_diff(ledger, rid)["summary"].startswith("相对 v2：原话已改")
    assert service.version_diff(ledger, rid, 1, 3)["diff"]["questions"]["answered"]
    assert service.version_diff(ledger, rid, v_to=2)["from"]["version"] == 1
    with pytest.raises(LookupError):
        service.version_diff(ledger, 9999)
    with pytest.raises(ValueError, match="没有 v9"):
        service.version_diff(ledger, rid, 1, 9)
    with pytest.raises(ValueError, match="同一版"):
        service.version_diff(ledger, rid, 2, 2)


@pytest.fixture
def client(deps, monkeypatch):
    from fastapi.testclient import TestClient

    import api

    ledger, memory = deps
    monkeypatch.setattr(api, "_ledger", ledger)
    monkeypatch.setattr(api, "_memory", memory)
    monkeypatch.setattr(api, "API_TOKEN", "")
    return TestClient(api.app)


def test_rest_versions_and_diff(client):
    rid = client.post("/api/v1/analyze", json={"text": SAMPLE1}).json()["req_id"]
    r = client.get(f"/api/v1/requirements/{rid}/versions")
    assert r.status_code == 200 and r.json()["count"] == 1 and r.json()["versions"][0]["label"] == "受理"
    assert client.get(f"/api/v1/requirements/{rid}/diff").status_code == 422          # 只有一版
    client.post("/api/v1/analyze", json={"text": SAMPLE1, "answers": ANSWERS, "req_id": rid})
    r = client.get(f"/api/v1/requirements/{rid}/versions")
    assert [v["label"] for v in r.json()["versions"]] == ["受理", "业务答复重算"]
    assert set(r.json()["versions"][1]["snapshot"]) >= {"questions", "estimate", "data_map", "decisions"}
    r = client.get(f"/api/v1/requirements/{rid}/diff")
    body = r.json()
    assert r.status_code == 200 and body["summary"] == "相对 v1：已答 +2" and body["markdown"].startswith("# 需求变更说明")
    assert body["from"]["label"] == "受理" and body["to"]["label"] == "业务答复重算"
    assert client.get(f"/api/v1/requirements/{rid}/diff", params={"from": 2, "to": 1}).json()["summary"].startswith("相对 v2")
    assert client.get(f"/api/v1/requirements/{rid}/diff", params={"from": 1, "to": 5}).status_code == 422
    assert client.get(f"/api/v1/requirements/{rid}/diff", params={"from": -1}).status_code == 422
    assert client.get("/api/v1/requirements/9999/versions").status_code == 404
    assert client.get("/api/v1/requirements/9999/diff").status_code == 404


def test_rest_versions_and_diff_need_the_token(client, monkeypatch):
    """配了 XUZHI_API_TOKEN 时，版本快照与差异和其它台账接口一样要带 Token。"""
    import api

    rid = client.post("/api/v1/analyze", json={"text": SAMPLE1}).json()["req_id"]
    client.post("/api/v1/analyze", json={"text": SAMPLE1, "answers": ANSWERS, "req_id": rid})
    monkeypatch.setattr(api, "API_TOKEN", "s3cret")
    for path in (f"/api/v1/requirements/{rid}/versions", f"/api/v1/requirements/{rid}/diff"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.get(path, headers={"X-API-Token": "s3cret"}).status_code == 200


def test_mcp_diff_tool(deps, monkeypatch):
    import mcp_server as ms

    ledger, memory = deps
    monkeypatch.setattr(ms, "_ledger", ledger)
    monkeypatch.setattr(ms, "_memory", memory)

    def payload(out):
        return json.loads((out[0] if isinstance(out, list) else out[0][0]).text)

    async def go():
        tool = next(t for t in await ms.mcp.list_tools() if t.name == "xuzhi_diff")
        assert "需重新确认" in tool.description and set(tool.inputSchema["properties"]) == {"req_id", "from_version", "to_version"}
        rid = payload(await ms.mcp.call_tool("xuzhi_clarify", {"text": SAMPLE1}))["req_id"]
        await ms.mcp.call_tool("xuzhi_clarify", {"text": SAMPLE1, "answers": ANSWERS, "req_id": rid})
        d = payload(await ms.mcp.call_tool("xuzhi_diff", {"req_id": rid}))
        assert d["summary"] == "相对 v1：已答 +2" and d["markdown"].startswith("# 需求变更说明")
        assert [v["label"] for v in d["versions"]] == ["受理", "业务答复重算"]
        d = payload(await ms.mcp.call_tool("xuzhi_diff", {"req_id": rid, "from_version": 2, "to_version": 1}))
        assert d["from"]["version"] == 2 and d["to"]["version"] == 1
        with pytest.raises(Exception, match="9999"):
            await ms.mcp.call_tool("xuzhi_diff", {"req_id": 9999})

    asyncio.run(go())


# ---------------- 演示数据 ----------------
def test_demo_seed_versions_follow_backdated_timeline_and_reset_cleanly(tmp_path, monkeypatch):
    from tools import demo_seed

    monkeypatch.setattr(config, "LEDGER_DB", tmp_path / "ledger.sqlite3")
    monkeypatch.setattr(config, "HISTORY_LEARNED_PATH", tmp_path / "history_learned.json")
    knowledge.history.cache_clear()
    try:
        assert demo_seed.main([]) == 0
        ledger = Ledger()
        answered = sum(1 for plan in demo_seed.PLAN if plan[3])
        total = ledger.conn.execute("SELECT COUNT(*) FROM versions").fetchone()[0]
        assert total == len(demo_seed.PLAN) + answered
        for row in ledger.recent(50):
            found = ledger.versions_of(row["id"])
            assert found[0]["label"] == "受理" and found[0]["ts"] == row["ts"]
            assert all(a["ts"] <= b["ts"] for a, b in zip(found, found[1:]))
        assert demo_seed.main([]) == 0                                    # 重置：版本跟着清，不串到新编号上
        assert Ledger().conn.execute("SELECT COUNT(*) FROM versions").fetchone()[0] == total
    finally:
        knowledge.history.cache_clear()
