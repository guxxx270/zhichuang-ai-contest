"""REST API + MCP + 服务层。全部走规则引擎（不调模型），一本账写临时库。"""
from __future__ import annotations

import asyncio
import json

import pytest

from xuzhi import service
from xuzhi.ledger import Ledger
from xuzhi.memory import Memory
from xuzhi.pipeline.estimate import ESTIMATION_COMPARISON, Estimate
from xuzhi.pipeline.tasks import AI_REVIEW, Task

SAMPLE = "资管运营小周：净值日报能不能加一列'较上一交易日变动'，风险指标也放进去，周五给客户那版也要，最好下周就能用。客户张伟 13812345678。"
SAMPLE2 = "资管运营：净值日报再加一列累计净值，也要给客户版，下周要。"
EMAIL = ("发件人：资管部 投资经理 赵经理\n主题：下单前限仓提示\n"
         "技术部同事：发这封邮件是想请你们在下单前自动提示这笔下去会不会超限仓，临近交割月尤其需要。")


def _ready_estimate(references: dict | None = None) -> Estimate:
    """一份已通过校验的模型估时（模型任务沿用 Task 字段：share 存以前参考小时、coef 存两轨比值）。"""
    task = Task("E1", "净值日报加变动列", "开发", AI_REVIEW, 8.0, 0.5, "在日报中增加变动列",
                trad_days=1.0, ai_days=0.5, trad_low_hours=6.0, trad_high_hours=10.0, ai_low_hours=3.0, ai_high_hours=5.0,
                before_reason="手写 SQL 与页面", after_reason="代理改多文件、跑测试",
                evidence=[{"source": "requirement", "file": "", "quote": "加一列"}])
    return Estimate(method="llm", status="ready", mid=1.0, low=0.75, high=1.25, ai_mid=0.5, ai_low=0.375, ai_high=0.625,
                    saving_pct=50.0, tasks=[task], comparison=dict(ESTIMATION_COMPARISON), references=references or {})


@pytest.fixture
def deps(tmp_path):
    return Ledger(tmp_path / "l.sqlite3"), Memory(tmp_path / "l.sqlite3")


# ---------------- 服务层 ----------------
def test_service_run_records_and_serializes(deps):
    ledger, memory = deps
    res = service.run(SAMPLE, channel="API", ledger=ledger, memory=memory)
    assert res.req_id >= 1 and ledger.get(res.req_id)["status"] == "受理"
    d = service.to_dict(res.analysis, req_id=res.req_id, include_prototype=True)
    json.dumps(d, ensure_ascii=False)                      # 必须可序列化
    assert d["redacted"] >= 2 and d["card"]["title"] and d["questions"]
    assert set(d["estimate"]) >= {"trad_days", "ai_days", "saving_pct", "confidence", "tasks", "similar"}
    assert d["estimate"]["status"] == "unavailable" and d["estimate"]["trad_days"]["mid"] is None
    assert d["estimate"]["before"] == {"label": "以前", "unit": "小时", "low": None, "mid": None, "high": None}
    assert d["estimate"]["after"]["label"] == "现在" and d["estimate"]["after"]["mid"] is None
    assert set(d["architecture"]) >= {"decisions", "data_map", "reuse", "adr_md"}
    assert d["spec"]["business_md"].startswith("#") and d["prototype_html"]
    assert "13812345678" not in json.dumps(d["card"], ensure_ascii=False)   # 卡片来自脱敏文本
    # 答复重算：同一条账更新，并记进追问记忆
    qid = d["questions"][0]["id"]
    # 人工设置一次旧结果，确认无模型重算会清掉旧值，而非继续报上次工时。
    ledger.update_estimate(res.req_id, Estimate(
        method="llm", status="ready", mid=4, low=3, high=5, ai_mid=2, ai_low=1.5, ai_high=3,
    ))
    res2 = service.run(SAMPLE, answers={qid: "按结算价"}, req_id=res.req_id, channel="API", ledger=ledger, memory=memory)
    assert res2.req_id == res.req_id and res2.learned >= 1
    row = ledger.get(res.req_id)
    assert row["n_answered"] >= 1 and row["status"] == "已确认"
    assert row["mid_days"] is None and row["ai_days"] is None
    assert json.loads(row["payload"])["estimate"]["status"] == "unavailable"
    assert any(e["action"] == "追问记忆" for e in ledger.events_of(res.req_id))
    assert any(e["action"] == "估量重算" for e in ledger.events_of(res.req_id))
    # 不记账
    res3 = service.run(SAMPLE, record=False, ledger=ledger, memory=memory)
    assert res3.req_id == 0 and len(ledger.recent()) == 1
    with pytest.raises(ValueError):
        service.run("   ", ledger=ledger, memory=memory)
    b = service.brief(res.analysis, res.req_id)
    assert "spec" not in b and "stack" not in b["architecture"]


def test_rerun_without_answers_keeps_existing_estimate(deps, monkeypatch):
    """带 req_id、不带答复的定架 / 规则估量：不算一轮答复重算，不清掉已有模型估时，也不改答复数。"""
    import mcp_server as ms

    ledger, memory = deps
    monkeypatch.setattr(ms, "_ledger", ledger)
    monkeypatch.setattr(ms, "_memory", memory)
    rid = service.run(SAMPLE, channel="API", ledger=ledger, memory=memory).req_id
    ledger.update_estimate(rid, _ready_estimate())        # 先有一份模型估时（如 Web 后台补算）
    before, n_events, n_versions = ledger.get(rid), len(ledger.events_of(rid)), len(ledger.versions_of(rid))
    rounds = ledger.dashboard()["avg_rounds"]

    async def go():
        await ms.mcp.call_tool("xuzhi_architect", {"text": SAMPLE, "req_id": rid})
        await ms.mcp.call_tool("xuzhi_estimate", {"text": SAMPLE, "req_id": rid})

    asyncio.run(go())
    assert ledger.get(rid) == before and before["mid_days"] == 1.0 and before["ai_days"] == 0.5
    assert not any(e["action"] == "业务答复重算" for e in ledger.events_of(rid))
    assert len(ledger.events_of(rid)) == n_events and len(ledger.versions_of(rid)) == n_versions
    assert ledger.dashboard()["avg_rounds"] == rounds and len(ledger.recent()) == 1


def test_rerun_with_model_estimate_is_written_back(deps, monkeypatch):
    ledger, memory = deps
    rid = service.run(SAMPLE, channel="API", ledger=ledger, memory=memory).req_id
    real = service.analyze

    def with_model(*args, **kwargs):
        a = real(*args, **kwargs)
        a.estimate = _ready_estimate(a.estimate.references)
        return a

    monkeypatch.setattr(service, "analyze", with_model)
    service.run(SAMPLE, req_id=rid, channel="MCP", ledger=ledger, memory=memory)
    row = ledger.get(rid)
    assert row["mid_days"] == 1.0 and row["status"] == "受理" and row["n_answered"] == 0
    assert [v["label"] for v in ledger.versions_of(rid)][-1] == "模型估时"
    assert not any(e["action"] == "业务答复重算" for e in ledger.events_of(rid))


def test_reconciled_requirement_is_read_only(deps):
    ledger, memory = deps
    res = service.run(SAMPLE, channel="API", ledger=ledger, memory=memory)
    rid = res.req_id
    ledger.update_estimate(rid, _ready_estimate())
    ledger.reconcile(rid, 1.5, write_history=False)
    before, n_events, n_versions = ledger.get(rid), len(ledger.events_of(rid)), len(ledger.versions_of(rid))
    qid = res.analysis.questions[0].id
    res2 = service.run(SAMPLE + "另外再加一列累计净值。", answers={qid: "按结算价"}, req_id=rid, ledger=ledger, memory=memory)
    assert "该需求已对账，本次结果未写回一本账" in res2.analysis.notes
    assert ledger.get(rid) == before
    assert len(ledger.events_of(rid)) == n_events and len(ledger.versions_of(rid)) == n_versions
    assert res2.learned == 0 and memory.stats()["entries"] == 0


def test_rerun_does_not_recount_memory_hits(deps):
    """同一条需求重算照常沿用记忆，但「记忆省问」只在第一次沿用时计数。"""
    ledger, memory = deps
    first = service.run(SAMPLE, ledger=ledger, memory=memory)
    qid = first.analysis.questions[0].id
    service.run(SAMPLE, answers={qid: "按结算价"}, req_id=first.req_id, ledger=ledger, memory=memory)
    assert memory.stats()["entries"] >= 1 and memory.stats()["hits"] == 0
    second = service.run(SAMPLE2, ledger=ledger, memory=memory)            # 同一提出方的新需求
    assert second.analysis.recalled >= 1
    hits = memory.stats()["hits"]
    assert hits == second.analysis.recalled
    for _ in range(2):
        again = service.run(SAMPLE2, req_id=second.req_id, ledger=ledger, memory=memory)
        assert again.analysis.recalled == second.analysis.recalled
    assert memory.stats()["hits"] == hits


def test_estimate_json_uses_current_fields(deps):
    _, memory = deps
    a = service.run(SAMPLE, record=False, memory=memory).analysis
    refs = a.estimate.references
    est = service.to_dict(a)["estimate"]
    assert est["dims"] == refs["rule"]["dims"] and est["dims"]                 # 六维评分来自规则参考
    assert [s["id"] for s in est["similar"]] == [i["id"] for i in refs["history"]["items"]] and est["similar"]
    first = est["similar"][0]
    assert first["estimated_hours"] == refs["history"]["items"][0]["estimated_hours"]
    assert first["estimate_days"] == first["estimated_hours"] / 8 and "source" in first
    assert est["comparison"] == {}                                              # 没有模型估时就没有比较口径
    a.estimate = _ready_estimate(refs)
    est = service.to_dict(a)["estimate"]
    assert est["comparison"] == ESTIMATION_COMPARISON
    (task,) = est["tasks"]
    assert not {"share", "coef", "note"} & set(task)
    assert task["before_mid_hours"] == 8.0 and task["ratio"] == 0.5 and task["change"] == "在日报中增加变动列"
    assert 0 < est["references"]["rule"]["tasks"][0]["share"] < 1               # 规则参考的任务仍是占比
    json.dumps(est, ensure_ascii=False)


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


def test_rest_end_to_end(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200 and r.json()["ok"] and r.json()["product"] == "需知"
    r = client.post("/api/v1/analyze", json={"text": SAMPLE})
    assert r.status_code == 200
    d = r.json()
    rid, qid = d["req_id"], d["questions"][0]["id"]
    assert rid >= 1 and d["engine"] == "规则" and "prototype_html" not in d and d["spec"]["tech_md"]
    r = client.post("/api/v1/clarify", json={"text": SAMPLE, "answers": {qid: "按结算价"}, "req_id": rid})
    assert r.status_code == 200 and r.json()["req_id"] == rid
    assert next(q for q in r.json()["questions"] if q["id"] == qid)["answer"] == "按结算价"
    assert set(r.json()) == {"req_id", "engine", "seconds", "redacted", "recalled", "card", "questions", "confirm_message"}
    r = client.get("/api/v1/requirements")
    assert r.status_code == 200 and r.json()[0]["id"] == rid
    r = client.get(f"/api/v1/requirements/{rid}")
    assert r.status_code == 200 and r.json()["events"]
    assert client.get("/api/v1/requirements/999").status_code == 404
    assert client.post(f"/api/v1/requirements/{rid}/status", json={"status": "已交付"}).status_code == 200
    assert client.post(f"/api/v1/requirements/{rid}/status", json={"status": "已对账"}).status_code == 422
    r = client.post(f"/api/v1/requirements/{rid}/reconcile", json={"actual_days": 6, "ai_assisted": True, "ai_share": 0.5})
    assert r.status_code == 200 and "deviation" in r.json()
    r = client.get("/api/v1/dashboard")
    assert r.status_code == 200 and r.json()["count"] == 1 and "memory" in r.json() and "llm_audit" in r.json()
    r = client.get("/api/v1/digest")
    assert r.status_code == 200 and "markdown" in r.json()
    r = client.get("/api/v1/digest", params={"fmt": "markdown"})
    assert r.status_code == 200 and "催办摘要" in r.json()["markdown"]
    r = client.get("/api/v1/sandbox")
    assert r.status_code == 200 and r.json()["policy"]["api"]["accept_keys_in_request"] is False
    assert client.get("/api/v1/audit/llm").status_code == 200
    assert client.post("/api/v1/analyze", json={"text": ""}).status_code == 422


def test_rest_req_id_must_exist_and_not_be_jira(client, deps):
    ledger, _ = deps
    r = client.post("/api/v1/analyze", json={"text": SAMPLE, "req_id": 9999})
    assert r.status_code == 404 and "9999" in r.json()["detail"]
    assert client.post("/api/v1/clarify", json={"text": SAMPLE, "req_id": 9999}).status_code == 404
    assert ledger.recent() == []                                                # 不再静默新建一行
    rid = client.post("/api/v1/analyze", json={"text": SAMPLE}).json()["req_id"]
    ledger.conn.execute("UPDATE requirements SET source='Jira', ext_key='XZ-1' WHERE id=?", (rid,))
    ledger.conn.commit()
    before = ledger.get(rid)
    for path in ("/api/v1/analyze", "/api/v1/clarify"):
        r = client.post(path, json={"text": SAMPLE, "req_id": rid, "answers": {"T01": "按结算价"}})
        assert r.status_code == 422 and "Jira" in r.json()["detail"]
    assert ledger.get(rid) == before


def test_rest_error_codes(client):
    assert client.post("/api/v1/clarify", json={"text": "   "}).status_code == 422
    rid = client.post("/api/v1/analyze", json={"text": SAMPLE}).json()["req_id"]
    assert client.post(f"/api/v1/requirements/{rid}/reconcile", json={"actual_days": 0}).status_code == 422
    assert client.post(f"/api/v1/requirements/{rid}/reconcile", json={"actual_days": -2}).status_code == 422
    assert client.post("/api/v1/requirements/9999/reconcile", json={"actual_days": 3}).status_code == 404


def test_rest_source_is_detected_when_not_given(client, deps):
    ledger, _ = deps
    d = client.post("/api/v1/analyze", json={"text": EMAIL}).json()
    assert d["card"]["source"] == "邮件"                                         # 不再写死成渠道名「API」
    assert any(e["action"] == "API受理" for e in ledger.events_of(d["req_id"]))   # 渠道记在受理事件里
    d2 = client.post("/api/v1/analyze", json={"text": EMAIL, "source": "会议纪要"}).json()
    assert d2["card"]["source"] == "会议纪要"


def test_rest_token(deps, monkeypatch):
    from fastapi.testclient import TestClient

    import api

    ledger, memory = deps
    monkeypatch.setattr(api, "_ledger", ledger)
    monkeypatch.setattr(api, "_memory", memory)
    monkeypatch.setattr(api, "API_TOKEN", "s3cret")
    c = TestClient(api.app)
    assert c.get("/api/v1/health").status_code == 200                           # 健康检查不要 Token
    assert c.post("/api/v1/analyze", json={"text": SAMPLE}).status_code == 401
    assert c.post("/api/v1/analyze", json={"text": SAMPLE}, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.post("/api/v1/analyze", json={"text": SAMPLE}, headers={"X-API-Token": "s3cret"}).status_code == 200
    assert c.get("/api/v1/requirements", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert c.get("/api/v1/sandbox").status_code == 401                           # 沙箱策略也要 Token
    assert c.get("/api/v1/sandbox", headers={"X-API-Token": "s3cret"}).status_code == 200
    assert c.get("/api/v1/sandbox", headers={"X-API-Token": "s3cré".encode("latin-1")}).status_code == 401   # 非 ASCII 也只是 401


# ---------------- MCP ----------------
def test_mcp_tools(deps, monkeypatch):
    import mcp_server as ms

    ledger, memory = deps
    monkeypatch.setattr(ms, "_ledger", ledger)
    monkeypatch.setattr(ms, "_memory", memory)

    async def go():
        tools = await ms.mcp.list_tools()
        names = {t.name for t in tools}
        assert {"xuzhi_clarify", "xuzhi_estimate", "xuzhi_architect", "xuzhi_spec", "xuzhi_analyze", "xuzhi_ledger", "xuzhi_reconcile"} <= names
        assert all(t.description for t in tools)
        out = await ms.mcp.call_tool("xuzhi_clarify", {"text": SAMPLE})
        payload = json.loads(out[0].text if isinstance(out, list) else out[0][0].text)
        assert payload["req_id"] >= 1 and payload["questions"] and "确认" in payload["confirm_message"]
        rid, qid = payload["req_id"], payload["questions"][0]["id"]
        out = await ms.mcp.call_tool("xuzhi_estimate", {"text": SAMPLE, "answers": {qid: "按结算价"}, "req_id": rid})
        est = json.loads((out[0] if isinstance(out, list) else out[0][0]).text)["estimate"]
        assert est["trad_days"]["mid"] is None and est["ai_days"]["mid"] is None
        assert est["status"] == "unavailable" and est["tasks"] == []
        out = await ms.mcp.call_tool("xuzhi_architect", {"text": SAMPLE, "req_id": rid})
        arch = json.loads((out[0] if isinstance(out, list) else out[0][0]).text)["architecture"]
        assert arch["decisions"] and "adr_md" in arch
        out = await ms.mcp.call_tool("xuzhi_spec", {"text": SAMPLE, "req_id": rid, "version": "business"})
        sp = json.loads((out[0] if isinstance(out, list) else out[0][0]).text)
        assert "business_md" in sp and "tech_md" not in sp
        with pytest.raises(Exception, match="version"):
            await ms.mcp.call_tool("xuzhi_spec", {"text": SAMPLE, "req_id": rid, "version": "full"})
        with pytest.raises(Exception, match="9999"):
            await ms.mcp.call_tool("xuzhi_clarify", {"text": SAMPLE, "req_id": 9999})
        out = await ms.mcp.call_tool("xuzhi_ledger", {"n": 5})
        led = json.loads((out[0] if isinstance(out, list) else out[0][0]).text)
        assert led["dashboard"]["count"] == 1 and led["recent"][0]["id"] == rid   # 三个工具带 req_id 都落在同一条账上
        res = await ms.mcp.read_resource("xuzhi://sandbox")
        text = res[0].content if hasattr(res[0], "content") else str(res[0])
        assert "askcode" in text

    asyncio.run(go())


def test_mcp_instructions_match_behaviour():
    import mcp_server as ms

    text = ms.mcp.instructions
    assert "双轨工时" not in text and "两路参考" in text and "xuzhi_analyze(polish=true)" in text


def test_mcp_http_behind_gateway(monkeypatch):
    """对外监听（放在网关后）时按 XUZHI_MCP_ALLOWED_HOSTS 放行 Host，不再一律 421；其它 Host 仍拒绝。"""
    import sys

    from starlette.testclient import TestClient

    import mcp_server as ms

    for key in ("host", "port", "transport_security"):
        monkeypatch.setattr(ms.mcp.settings, key, getattr(ms.mcp.settings, key))
    monkeypatch.setattr(ms.mcp, "_session_manager", None)
    monkeypatch.setattr(ms.mcp, "run", lambda transport: None)
    monkeypatch.setenv("XUZHI_MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("XUZHI_MCP_ALLOW_EXPOSED", "1")
    monkeypatch.setenv("XUZHI_MCP_ALLOWED_HOSTS", "mcp.corp.example, 10.0.0.5:8771")
    monkeypatch.setattr(sys, "argv", ["mcp_server.py", "--transport", "streamable-http"])
    ms.main()
    assert ms.mcp.settings.transport_security.enable_dns_rebinding_protection
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    with TestClient(ms.mcp.streamable_http_app()) as c:
        for host in ("mcp.corp.example", "mcp.corp.example:8443", "10.0.0.5:8771", "127.0.0.1:8771"):
            r = c.post("/mcp", json=init, headers={**headers, "Host": host})
            assert r.status_code == 200, (host, r.status_code, r.text[:200])
        assert c.post("/mcp", json=init, headers={**headers, "Host": "evil.example"}).status_code == 421
        assert c.post("/mcp", json=init, headers={**headers, "Host": "10.0.0.5:9999"}).status_code == 421
