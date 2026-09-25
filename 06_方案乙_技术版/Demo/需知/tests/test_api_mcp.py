"""REST API + MCP + 服务层。全部走规则引擎（不调模型），一本账写临时库。"""
from __future__ import annotations

import asyncio
import json

import pytest

from xuzhi import service
from xuzhi.ledger import Ledger
from xuzhi.memory import Memory

SAMPLE = "资管运营小周：净值日报能不能加一列'较上一交易日变动'，风险指标也放进去，周五给客户那版也要，最好下周就能用。客户张伟 13812345678。"


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
    assert set(d["architecture"]) >= {"decisions", "data_map", "reuse", "adr_md"}
    assert d["spec"]["business_md"].startswith("#") and d["prototype_html"]
    assert "13812345678" not in json.dumps(d["card"], ensure_ascii=False)   # 卡片来自脱敏文本
    # 答复重算：同一条账更新，并记进追问记忆
    qid = d["questions"][0]["id"]
    res2 = service.run(SAMPLE, answers={qid: "按结算价"}, req_id=res.req_id, channel="API", ledger=ledger, memory=memory)
    assert res2.req_id == res.req_id and res2.learned >= 1
    row = ledger.get(res.req_id)
    assert row["n_answered"] >= 1 and row["status"] == "已确认"
    assert any(e["action"] == "追问记忆" for e in ledger.events_of(res.req_id))
    # 不记账
    res3 = service.run(SAMPLE, record=False, ledger=ledger, memory=memory)
    assert res3.req_id == 0 and len(ledger.recent()) == 1
    with pytest.raises(ValueError):
        service.run("   ", ledger=ledger, memory=memory)
    b = service.brief(res.analysis, res.req_id)
    assert "spec" not in b and "stack" not in b["architecture"]


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
        assert est["trad_days"]["mid"] > 0 and est["ai_days"]["mid"] > 0
        out = await ms.mcp.call_tool("xuzhi_architect", {"text": SAMPLE, "req_id": rid})
        arch = json.loads((out[0] if isinstance(out, list) else out[0][0]).text)["architecture"]
        assert arch["decisions"] and "adr_md" in arch
        out = await ms.mcp.call_tool("xuzhi_spec", {"text": SAMPLE, "req_id": rid, "version": "business"})
        sp = json.loads((out[0] if isinstance(out, list) else out[0][0]).text)
        assert "business_md" in sp and "tech_md" not in sp
        out = await ms.mcp.call_tool("xuzhi_ledger", {"n": 5})
        led = json.loads((out[0] if isinstance(out, list) else out[0][0]).text)
        assert led["dashboard"]["count"] == 1 and led["recent"][0]["id"] == rid   # 三个工具带 req_id 都落在同一条账上
        res = await ms.mcp.read_resource("xuzhi://sandbox")
        text = res[0].content if hasattr(res[0], "content") else str(res[0])
        assert "askcode" in text

    asyncio.run(go())
