"""纪要直通车：一段纪要识别多条需求 → 拆成几条分别受理。规则切分 + 服务层 + REST / MCP + 页面，全部不调模型。"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from xuzhi import config, reminders, service
from xuzhi.ledger import Ledger
from xuzhi.memory import Memory
from xuzhi.pipeline import analyze
from xuzhi.pipeline.split import MAX_SEGMENTS, source_hint, split_requirements

MINUTES = next(config.SAMPLES_DIR.glob("06_*.md")).read_text(encoding="utf-8")
APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


# ---------------- 规则切分 ----------------
def test_sample_minutes_splits_into_three_requests_with_their_requesters():
    segs = split_requirements(MINUTES)
    assert [s.index for s in segs] == [1, 2, 3]
    assert [s.requester for s in segs] == ["研究所", "风控部", "资管部"]
    assert {s.reason for s in segs} == {"编号条目"}
    assert "仓单库存周报" in segs[0].title_hint and "追保" in segs[1].title_hint and "集中度" in segs[2].title_hint
    for s in segs:
        assert 0 < len(s.title_hint) <= 30
        # 头部元信息每条都带上，来源照常识别；正文去掉了原来的编号
        assert s.text.startswith("来源：投研晨会纪要") and "记录：技术部 Jerry" in s.text and s.text.endswith(s.body)
        assert not s.body[0].isdigit()
        card = analyze(s.text, source_hint(MINUTES), None, llm_polish=False).card
        assert card.requester == s.requester and card.source.startswith("投研晨会纪要")


def test_attendance_background_and_decisions_are_not_requirements():
    blob = "\n".join(s.text for s in split_requirements(MINUTES))
    for line in ("参会", "市场回顾", "震荡偏弱", "需技术部支持的事项", "下周再议", "领导表示同意"):
        assert line in MINUTES and line not in blob


def test_single_requirements_are_not_split():
    for path in sorted(config.SAMPLES_DIR.glob("0[1-5]_*.md")):
        assert split_requirements(path.read_text(encoding="utf-8")) == [], path.name
    cases = json.loads((config.DATA_DIR / "eval" / "eval_set.json").read_text(encoding="utf-8"))["cases"]
    assert [c["id"] for c in cases if split_requirements(c["text"])] == []
    # 需求单里的编号是同一条需求的功能点；纪要里「要求如下：」后的编号是这一条的要点
    word = ("来源：Word 需求单 · 风控部\n标题：追保提醒\n\n1. 客户风险度超过 100% 时自动提醒客户经理。\n"
            "2. 盘中每半小时检查一次，夜盘也要。\n3. 提醒记录全程留痕，支持事后查询与导出。")
    points = ("来源：投研晨会纪要\n三、其他事项\n黑色组张老师提出，希望做一个基差监控页面，要求如下：\n1. 主要品种都要，螺纹、热卷、铁矿、焦炭这些；\n"
              "2. 期现价差每天更新，异常了要提醒一下；\n3. 最好手机也能看，出差时方便。")
    addon = "净值日报能不能加一列\"较上一交易日变动\"，就放在单位净值旁边。另外，客户版也要能导出 Excel，不该给的字段去掉。"
    assert split_requirements(word) == split_requirements(points) == split_requirements(addon) == []


def test_speaker_switch_and_transition_cues_split_without_numbers():
    talk = ("来源：周一联席晨会纪要\n风控部李经理：客户风险度超过 100% 要追保时，能不能自动提醒对应客户经理，夜盘也要。\n"
            "研究所张老师：希望做一个仓单库存周报，螺纹、热卷、铁矿，每周一早上发。\n王总：同意，技术部本周给评估。")
    segs = split_requirements(talk)
    assert [(s.requester, s.reason) for s in segs] == [("风控部", "提出方切换"), ("研究所", "提出方切换")]
    assert "王总" not in segs[0].body
    chat = ("小李，跟你说两件事。净值日报能不能加一列\"较上一交易日变动\"，就放在单位净值旁边，客户版也要。"
            "还有一件事，研究所希望做个仓单库存周报，每周一早上发到研究群。")
    segs = split_requirements(chat)
    assert [s.reason for s in segs] == ["首段", "连接词"]
    assert segs[1].requester == "研究所" and segs[1].body.startswith("研究所希望") and segs[1].title_hint.startswith("仓单库存周报")
    assert segs[0].requester == ""   # 不从另一条里借提出方


def test_short_numbered_lines_are_requests_not_section_titles():
    text = "来源：资管部周会纪要\n一、净值日报加一列较上一交易日变动\n二、产品风险度超过八成时自动提醒投资经理\n三、其他"
    segs = split_requirements(text)
    assert [s.body for s in segs] == ["净值日报加一列较上一交易日变动", "产品风险度超过八成时自动提醒投资经理"]
    assert [s.requester for s in segs] == ["资管部", "资管部"]   # 没点名：沿用头部，不认收件人「投资经理」


def test_header_department_does_not_override_each_requester():
    text = ("来源：投研晨会纪要（2026-10-21）· 研究所主持\n记录：Jerry\n\n"
            "1. 风控部提出：客户风险度超过 100% 要追保的时候，自动提醒客户经理。\n2. 研究所：希望做一个仓单库存周报，每周一早上发到研究群。")
    first, second = split_requirements(text)
    assert first.text.startswith("提出方：风控部\n来源：") and not second.text.startswith("提出方")
    assert [analyze(s.text, llm_polish=False).card.requester for s in (first, second)] == ["风控部", "研究所"]
    # 开场白点名的部门给后面没点名的条目
    intro = "来源：联席晨会纪要\n风控部提出两件事：\n1. 客户风险度超过 100% 要追保时，自动提醒客户经理和风控部。\n2. 能不能做个保证金占用日报，每天结算后发给风控群。"
    assert [s.requester for s in split_requirements(intro)] == ["风控部", "风控部"]


def test_item_naming_an_unlisted_department_keeps_its_own_requester():
    # 「结算部」不在部门词典里：本条不借上一条的资管部，也不在原话前写上错的「提出方」
    email = ("主题：关于两项系统需求\n各位好，\n第一件事，资管部希望净值日报能自动发到投资经理邮箱，每天早上 8 点前。\n"
             "第二件事，结算部需要一个保证金追加通知的导出功能，按客户导出 Excel。\n谢谢")
    first, second = split_requirements(email)
    assert (first.requester, second.requester) == ("资管部", "结算部")
    assert "资管部" not in second.text and second.title_hint.startswith("保证金追加通知")
    assert analyze(second.text, llm_polish=False).card.requester != "资管部"
    # 名字里带词典部门的按词典口径，照样压过头部点名的部门；「我们部门 / 全部」这类不算点名
    branch = ("来源：资管部周会纪要\n1. 上海营业部提出：开户进度要能按营业部查询，每天更新。\n"
              "2. 产品净值异常波动要自动提醒投资经理，每天结算后检查。")
    segs = split_requirements(branch)
    assert [s.requester for s in segs] == ["营业部", "资管部"]
    assert [analyze(s.text, llm_polish=False).card.requester for s in segs] == ["营业部", "资管部"]
    ours = branch.replace("上海营业部提出：", "我们部门需要：")
    assert [s.requester for s in split_requirements(ours)] == ["资管部", "资管部"]


def test_sub_points_and_addons_stay_with_their_item():
    two = ("来源：周会纪要\n1. 风控部提出：希望做一个追保提醒\n{sub}\n"
           "2. 研究所提出：希望做一个仓单库存周报，每周一早上发到研究群。")
    for sub in ("   - 客户风险度超过 100% 时提醒对应的客户经理\n   - 夜盘也要，企业微信推送就行",   # 缩进的子弹点
                "    1) 客户风险度超过 100% 时提醒对应的客户经理\n    2) 提醒记录要能导出查询",       # 缩进更深的同式编号
                "（1）客户风险度超过 100% 时提醒对应的客户经理\n（2）提醒记录要能导出查询"):        # 低一级的编号
        first, second = split_requirements(two.format(sub=sub))
        assert (first.requester, second.requester) == ("风控部", "研究所") and "客户经理" in first.body, sub
    # 小标题本身就是一条需求时，下面低一级的编号是它的要点
    nested = ("来源：周会纪要\n一、风控部提出希望做一个追保提醒\n1. 客户风险度超过 100% 时提醒对应的客户经理；\n"
              "2. 提醒记录要能导出查询。\n二、研究所提出希望做一个仓单库存周报，每周一早上发到研究群。")
    assert [s.requester for s in split_requirements(nested)] == ["风控部", "研究所"]
    # 紧跟在「要做什么」那句后面的列表是要点（「期现」不是提出方）；自己另提新东西的才另起一条
    dash = ("来源：投研晨会纪要\n研究所张老师：希望做一个基差监控页面\n- 主要品种都要，螺纹、热卷、铁矿、焦炭\n"
            "- 期现价差每天更新，异常了要自动提醒\n- 最好手机也能看，出差时方便")
    numbered = dash.replace("\n- 主要", "\n1. 主要").replace("\n- 期现", "\n2. 期现").replace("\n- 最好", "\n3. 最好")
    assert split_requirements(dash) == split_requirements(numbered) == []
    asks = ("来源：投研晨会纪要\n研究所张老师：希望做一个基差监控页面，主要品种都要。\n"
            "- 能不能做个保证金占用日报，每天结算后发给风控群\n- 希望做一个持仓集中度查询页面，按产品看")
    assert len(split_requirements(asks)) == 3
    named = ("来源：周会纪要\n风控部提出：客户风险度超过100%时自动提醒客户经理。\n1. 盘中每半小时检查一次，夜盘也要。\n"
             "2. 提醒记录全程留痕，支持事后查询与导出。")
    assert split_requirements(named) == []
    # 开场白既没点提出方、也没说要做个什么（「需要技术部尽快评估」）：后面的编号照常各算一条
    meta = ("来源：周会纪要\n本次会议讨论了各业务线的系统需求，需要技术部尽快评估。\n1. 仓单库存周报自动发送到研究群，每周一早上。\n"
            "2. 客户风险度超过100%时自动提醒客户经理。\n3. 产品持仓集中度查询页面，按产品看。")
    hints = [s.title_hint for s in split_requirements(meta)]
    assert all(sum(k in h for h in hints) == 1 for k in ("仓单库存周报", "风险度", "集中度")), hints
    # 「营业部补充：」接着上一条说（带不带编号、在不在同一行都一样）；「补充一个需求」才是新的一条
    addon = ("来源：周会纪要\n1. 风控部提出：客户风险度超过100%时，自动提醒客户经理。\n"
             "2. 营业部补充：提醒里要带上客户的可用资金，方便客户经理沟通。")
    assert split_requirements(addon) == []
    inline = ("来源：周会纪要\n风控部提出：客户风险度超过100%时，自动提醒客户经理。营业部补充：提醒里要带上客户的可用资金。\n"
              "研究所提出：希望做一个仓单库存周报，每周一早上发。")
    first, second = split_requirements(inline)
    assert "可用资金" in first.body and second.requester == "研究所"
    new = addon.replace("营业部补充：提醒里要带上客户的可用资金，方便客户经理沟通。", "营业部补充一个需求：能不能做个开户进度查询页面，每天更新。")
    assert [s.requester for s in split_requirements(new)] == ["风控部", "营业部"]
    verb = addon.replace("营业部补充：提醒里要带上客户的可用资金，方便客户经理沟通。", "需要补充一份风险揭示书模板，客户签署后能导出归档。")
    assert len(split_requirements(verb)) == 2   # 「补充」只是动词：照常算一条


def test_points_intro_without_verb_and_decisions_inside_a_request():
    # 「需求如下：」本身没有需求动词，后面的编号仍是这一条的要点（「客户经理」是收件人，不该拆出一条营业部的需求）
    spec = ("标题：追保提醒\n经会上讨论，需求如下：\n1. 客户风险度超过 100% 时自动提醒客户经理。\n"
            "2. 盘中每半小时检查一次，夜盘也要。\n3. 提醒记录全程留痕，支持事后查询与导出。")
    assert split_requirements(spec) == []
    each = ("来源：周会纪要\n各部门需求如下：\n1. 研究所：希望做一个仓单库存周报，每周一早上发。\n"
            "2. 风控部：能不能做个追保提醒，客户风险度超过100%时提醒客户经理。")
    assert [s.requester for s in split_requirements(each)] == ["研究所", "风控部"]
    # 明确提了要做什么、只是细节「下周再议」的仍是一条；只说「××需求下周再议」的不算
    base = ("来源：周会纪要\n1. 研究所提出：希望做一个仓单库存周报，每周一早上发到研究群。\n"
            "2. 风控部提出：客户风险度超过100%时自动提醒客户经理。\n")
    segs = split_requirements(base + "3. 能不能做个持仓集中度查询页面，具体字段下周再议。")
    assert len(segs) == 3 and segs[2].title_hint.startswith("持仓集中度查询页面")
    assert len(split_requirements(base + "3. 风控部的保证金预警需求，下周再议。")) == 2


def test_titles_and_positions_are_not_departments():
    base = "来源：周会纪要\n1. {x}\n2. 风控部提出：客户风险度超过100%时自动提醒客户经理。"
    for x in ("王建国部长提出：希望做一个仓单库存周报，每周一早上发。", "公司内部需要一个审批流程，报销单线上走。"):
        first, second = split_requirements(base.format(x=x))
        assert (first.requester, second.requester) == ("", "风控部"), x


def test_pasted_zero_width_chars_and_crlf_do_not_hide_items():
    pasted = ("\ufeff来源：投研晨会纪要\u200b\r\n\u200b1.\u200b研究所提出：希望做一个仓单库存周报，每周一早上发。\r\n"
              "2.\u00a0风控部提出：客户风险度超过100%时自动提醒客户经理。\r\n")
    segs = split_requirements(pasted)
    assert [(s.requester, s.reason) for s in segs] == [("研究所", "编号条目"), ("风控部", "编号条目")]
    assert all(s.text.startswith("来源：投研晨会纪要\n") and "\u200b" not in s.text for s in segs)


def test_cap_and_min_segments():
    many = "来源：技术部周会纪要\n" + "\n".join(f"{i}. 风控部提出：能不能做个第{i}号监控页面，每天结算后自动更新指标。" for i in range(1, 11))
    segs = split_requirements(many)
    assert len(segs) == MAX_SEGMENTS == 8 and segs[-1].index == 8 and "第8号" in segs[-1].body
    assert len(split_requirements(many, max_segments=3)) == 3
    assert split_requirements(MINUTES, min_segments=4) == [] and len(split_requirements(MINUTES, min_segments=3)) == 3
    assert split_requirements("") == split_requirements("   ") == []
    assert source_hint(MINUTES) == "会议纪要"
    assert source_hint(next(config.SAMPLES_DIR.glob("01_*.md")).read_text(encoding="utf-8")) == ""


# ---------------- 服务层 ----------------
@pytest.fixture
def deps(tmp_path):
    return Ledger(tmp_path / "l.sqlite3"), Memory(tmp_path / "l.sqlite3")


def test_service_split_redacts_and_run_split_records_each_row(deps):
    ledger, memory = deps
    text = MINUTES.replace("企业微信提醒就行。", "企业微信提醒就行，联系人手机 13912345678。")
    preview = service.split(text)
    assert [d["requester"] for d in preview] == ["研究所", "风控部", "资管部"]
    assert set(preview[0]) == {"index", "requester", "reason", "title_hint", "body", "text"}
    assert "13912345678" not in json.dumps(preview, ensure_ascii=False) and "<手机_1>" in preview[1]["body"]

    results = service.run_split(text, channel="API", ledger=ledger, memory=memory)
    ids = [r.req_id for r in results]
    assert len(set(ids)) == 3 and all(ids) and ledger.stats()["count"] == 3
    assert [ledger.get(i)["dept"] for i in ids] == ["研究所", "风控部", "资管部"]
    events = ledger.events_of(ids[1])
    assert events[0]["action"] == "纪要拆分受理" and "同一段纪要拆出 3 条，第 2 条" in events[0]["detail"]
    assert all(r.analysis.engine == "规则" and r.analysis.card.source.startswith("投研晨会纪要") for r in results)
    assert "13912345678" not in results[1].analysis.card.to_json()

    assert [r.req_id for r in service.run_split(text, record=False, ledger=ledger, memory=memory)] == [0, 0, 0]
    assert ledger.stats()["count"] == 3
    single = next(config.SAMPLES_DIR.glob("01_*.md")).read_text(encoding="utf-8")
    assert service.split(single) == [] and service.run_split(single, ledger=ledger, memory=memory) == []
    with pytest.raises(ValueError):
        service.run_split("  ", ledger=ledger, memory=memory)


def test_split_parent_row_is_kept_but_not_counted(deps):
    ledger, memory = deps
    parent = ledger.log_analysis(analyze(MINUTES, llm_polish=False))
    children = [r.req_id for r in service.run_split(MINUTES, ledger=ledger, memory=memory)]
    assert ledger.dashboard()["count"] == 4
    ledger.mark_split(parent, children)
    ids = " ".join(f"#{i}" for i in children)
    # 整段那行留着（原文、流水都在），但看板、首页计数、催办都只算拆出来的 3 条
    assert ledger.get(parent)["split_into"] == ids and ledger.events_of(parent)[-1]["detail"] == f"已拆分为 {ids}"
    dash = ledger.dashboard()
    assert ledger.stats()["count"] == dash["count"] == 3 and dash["by_dept"] == {"研究所": 1, "风控部": 1, "资管部": 1}
    assert sum(dash["by_status"].values()) == 3 and dash["unestimated_count"] == 3
    assert {r["id"]: r["split_into"] for r in ledger.recent()}[parent] == ids
    assert {r["id"]: r["split_into"] for r in ledger.export_rows()}[parent] == ids
    old = (datetime.now() - timedelta(days=10)).isoformat(timespec="seconds")
    ledger.conn.execute("UPDATE requirements SET ts=?", (old,))
    ledger.conn.commit()
    digest = reminders.build_digest(ledger)
    assert sorted(it.req_id for it in digest.stale_confirm) == sorted(children) and sum(digest.by_status.values()) == 3


def test_old_ledger_without_split_column_still_opens(tmp_path):
    path = tmp_path / "old.sqlite3"
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE requirements(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, title TEXT, source TEXT, req_type TEXT, "
        "requester TEXT, engine TEXT, n_questions INTEGER, n_answered INTEGER, mid_days REAL, confidence TEXT, redacted INTEGER, "
        "payload TEXT);"
        "INSERT INTO requirements(ts,title,source,req_type,requester,engine,n_questions,n_answered,mid_days,confidence,redacted,"
        "payload) VALUES('2026-09-01T09:00:00','旧需求','微信','报表','研究所','规则',3,0,NULL,'中',0,'{}');")
    con.close()
    ledger = Ledger(path)   # 老库自动补列
    assert ledger.stats()["count"] == ledger.dashboard()["count"] == 1 and ledger.recent()[0]["split_into"] is None
    assert [it.title for it in reminders.build_digest(ledger).stale_confirm] == ["旧需求"]


# ---------------- REST ----------------
@pytest.fixture
def client(deps, monkeypatch):
    from fastapi.testclient import TestClient

    import api

    ledger, memory = deps
    monkeypatch.setattr(api, "_ledger", ledger)
    monkeypatch.setattr(api, "_memory", memory)
    monkeypatch.setattr(api, "API_TOKEN", "")
    return TestClient(api.app)


def test_rest_split_preview_then_record(client, deps):
    ledger, _ = deps
    r = client.post("/api/v1/split", json={"text": MINUTES})
    assert r.status_code == 200
    d = r.json()
    assert d["count"] == 3 and len(d["segments"]) == 3 and "results" not in d and ledger.stats()["count"] == 0
    r = client.post("/api/v1/split", json={"text": MINUTES, "record": True})
    assert r.status_code == 200
    results = r.json()["results"]
    assert len(results) == 3 and ledger.stats()["count"] == 3
    assert {"req_id", "card", "questions", "estimate", "architecture"} <= set(results[0]) and "spec" not in results[0]
    assert [x["card"]["requester"] for x in results] == ["研究所", "风控部", "资管部"]
    row = client.get(f"/api/v1/requirements/{results[2]['req_id']}").json()
    assert any(e["action"] == "纪要拆分受理" and "第 3 条（API）" in e["detail"] for e in row["events"])
    single = next(config.SAMPLES_DIR.glob("01_*.md")).read_text(encoding="utf-8")
    assert client.post("/api/v1/split", json={"text": single, "record": True}).json() == {"count": 0, "segments": []}
    assert client.post("/api/v1/split", json={"text": " \n "}).status_code == 422
    assert client.post("/api/v1/split", json={"text": ""}).status_code == 422


def test_rest_split_requires_token(deps, monkeypatch):
    from fastapi.testclient import TestClient

    import api

    ledger, memory = deps
    monkeypatch.setattr(api, "_ledger", ledger)
    monkeypatch.setattr(api, "_memory", memory)
    monkeypatch.setattr(api, "API_TOKEN", "s3cret")
    c = TestClient(api.app)
    assert c.post("/api/v1/split", json={"text": MINUTES}).status_code == 401
    assert c.post("/api/v1/split", json={"text": MINUTES}, headers={"Authorization": "Bearer s3cret"}).status_code == 200


# ---------------- MCP ----------------
def test_mcp_split_tool(deps, monkeypatch):
    import mcp_server as ms

    ledger, memory = deps
    monkeypatch.setattr(ms, "_ledger", ledger)
    monkeypatch.setattr(ms, "_memory", memory)

    def payload(out):
        return json.loads((out[0] if isinstance(out, list) else out[0][0]).text)

    async def go():
        assert "xuzhi_split" in {t.name for t in await ms.mcp.list_tools()}
        d = payload(await ms.mcp.call_tool("xuzhi_split", {"text": MINUTES}))
        assert d["count"] == 3 and "results" not in d and ledger.stats()["count"] == 0
        d = payload(await ms.mcp.call_tool("xuzhi_split", {"text": MINUTES, "record": True}))
        assert len(d["results"]) == 3 and ledger.stats()["count"] == 3
        assert all(e["action"] == "纪要拆分受理" for x in d["results"] for e in ledger.events_of(x["req_id"]))

    asyncio.run(go())


# ---------------- 页面 ----------------
def _run(app: AppTest) -> AppTest:
    app.run()
    assert not app.exception, [error.message for error in app.exception]
    return app


def _start(app: AppTest, prefix: str) -> None:
    box = app.selectbox(key="sample")
    box.select(next(o for o in box.options if o.startswith(prefix)))
    _run(app)
    next(b for b in app.button if b.label == "需知，开工").click()
    _run(app)


def _paste(app: AppTest, text: str) -> None:
    box = app.selectbox(key="sample")
    box.select("（粘贴自己的）")
    _run(app)
    next(t for t in app.text_area if t.label == "原话").input(text)
    next(b for b in app.button if b.label == "需知，开工").click()
    _run(app)


def _banner(app: AppTest) -> list[str]:
    return [m.value for m in app.markdown if "纪要直通车" in m.value]


def _pick(app: AppTest, rid: int) -> None:
    app.segmented_control(key="minutes_pick").set_value(rid)
    _run(app)


class _RunningJob:
    """一直在跑的假后台模型任务：只记下有没有被结束。"""
    kind = "analysis"

    def __init__(self):
        self.discarded = False

    def snapshot(self):
        return {"status": "running", "stage": "模型估时", "elapsed": 3.0, "discarded": self.discarded}

    def discard(self):
        self.discarded = True


@pytest.fixture
def home():
    st.cache_resource.clear()
    app = _run(AppTest.from_file(str(APP_PATH), default_timeout=30))
    yield app
    st.cache_resource.clear()


def test_ui_minutes_banner_splits_into_ledger_rows_and_opens_each(home):
    _start(home, "06_")
    parent = home.session_state["req_id"]
    assert any("识别到 3 条需求" in m for m in _banner(home))
    preview = next(d.value for d in home.dataframe if "需求预览" in d.value.columns)
    assert list(preview["提出方"]) == ["研究所", "风控部", "资管部"]
    count = Ledger().stats()["count"]

    next(b for b in home.button if b.label == "拆成 3 条分别受理").click()
    _run(home)
    children = home.session_state["minutes_split"]["children"]
    assert [r["id"] for r in Ledger().recent(4)] == [*reversed(children), parent]   # 新记 3 行
    assert Ledger().stats()["count"] == count + 2   # 整段那行不再算一条：-1 + 3
    assert Ledger().get(parent)["split_into"] == " ".join(f"#{i}" for i in children)
    assert Ledger().events_of(parent)[-1]["detail"] == "已拆分为 " + " ".join(f"#{i}" for i in children)
    assert [Ledger().get(i)["dept"] for i in children] == ["研究所", "风控部", "资管部"]
    assert any("已拆成 3 条" in m for m in _banner(home)) and home.session_state["req_id"] == parent

    home.segmented_control(key="minutes_pick").set_value(children[1])
    _run(home)
    a = home.session_state["analysis"]
    assert home.session_state["req_id"] == children[1] and a.card.requester == "风控部"
    assert f"### {a.card.title}" in [m.value for m in home.markdown]
    assert "企业微信提醒就行" in a.raw_text and "仓单库存周报" not in a.raw_text

    # 在第 2 条上答复重算：记在第 2 条那行；切走再回来，答复仍在
    qid = a.questions[0].id
    home.text_input(key=f"ans_{qid}").input("按结算后的风险度")
    _run(home)
    next(b for b in home.button if b.label.startswith("🔁 按业务答复重算")).click()
    _run(home)
    assert Ledger().get(children[1])["n_answered"] >= 1
    home.segmented_control(key="minutes_pick").set_value(children[2])
    _run(home)
    assert home.session_state["analysis"].card.requester == "资管部"
    home.segmented_control(key="minutes_pick").set_value(children[1])
    _run(home)
    assert home.text_input(key=f"ans_{qid}").value == "按结算后的风险度"
    home.segmented_control(key="minutes_pick").set_value(parent)
    _run(home)
    assert home.session_state["req_id"] == parent and Ledger().stats()["count"] == count + 2


def test_ui_single_requirement_shows_no_banner(home):
    count = Ledger().stats()["count"]
    _start(home, "01_")
    assert not _banner(home) and not [b for b in home.button if b.label.startswith("拆成")]
    assert Ledger().stats()["count"] == count + 1


def test_ui_typed_answers_stay_with_each_item_until_recomputed(home):
    _start(home, "06_")
    next(b for b in home.button if b.label == "拆成 3 条分别受理").click()
    _run(home)
    children = home.session_state["minutes_split"]["children"]
    _pick(home, children[1])
    qid = home.session_state["analysis"].questions[0].id
    answered = Ledger().get(children[1])["n_answered"]
    home.text_input(key=f"ans_{qid}").input("夜盘算下一交易日")
    _run(home)
    _pick(home, children[2])   # 没点重算就切走
    assert all(t.value != "夜盘算下一交易日" for t in home.text_input if str(t.key).startswith("ans_"))
    _pick(home, children[1])
    assert home.text_input(key=f"ans_{qid}").value == "夜盘算下一交易日"
    assert Ledger().get(children[1])["n_answered"] == answered   # 只是草稿：没重算不记账


def test_ui_switching_items_ends_the_running_model_job(home):
    _start(home, "06_")
    parent = home.session_state["req_id"]
    job = _RunningJob()
    home.session_state["_model_job"] = job
    home.session_state["_model_job_meta"] = {"req_id": parent, "revision": home.session_state["_analysis_revision"],
                                             "kind": "analysis"}
    _run(home)
    assert any("模型正在分析" in s.label for s in home.status)
    next(b for b in home.button if b.label == "拆成 3 条分别受理").click()
    _run(home)
    children = home.session_state["minutes_split"]["children"]
    assert not job.discarded and any("切换会结束这次分析" in c.value for c in home.caption)
    _pick(home, children[1])
    # 换条前先结束整段那条的后台任务，结果不会写到第 2 条上
    assert job.discarded and home.session_state["req_id"] == children[1]
    assert "_model_job" not in home.session_state or home.session_state["_model_job"] is None
    assert not any("模型正在分析" in s.label for s in home.status)


def test_ui_offer_mentions_the_cap_only_when_items_were_cut_off(home):
    item = "{i}. 风控部提出：能不能做个第{i}号监控页面，每天结算后自动更新指标。"
    _paste(home, "来源：技术部周会纪要\n" + "\n".join(item.format(i=i) for i in range(1, 11)))
    assert any("识别到 8 条需求（只列前 8 条）" in m for m in _banner(home))
    assert any("后面的请另贴一次" in c.value for c in home.caption)
    assert len(next(d.value for d in home.dataframe if "需求预览" in d.value.columns)) == 8
    _paste(home, "来源：技术部周会纪要\n" + "\n".join(item.format(i=i) for i in range(1, 9)))
    assert any("识别到 8 条需求**" in m for m in _banner(home))
    assert not any("另贴一次" in c.value for c in home.caption)
