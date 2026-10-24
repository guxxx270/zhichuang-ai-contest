"""接口与企微按需调模型：只做要返回的那几步；模型调用审计带上渠道与需求编号。

用真实 LLM 类接一个假网关（不联网）：每次回空 JSON，问清不改、估时不过校验，只用来数调用、看审计。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from xuzhi import service
from xuzhi.audit import Audit
from xuzhi.ledger import Ledger
from xuzhi.llm import LLM
from xuzhi.memory import Memory

SAMPLE = "资管运营小周：净值日报能不能加一列'较上一交易日变动'，风险指标也放进去，周五给客户那版也要，最好下周就能用。客户张伟 13812345678。"


class _EmptyGateway:
    """假 OpenAI 客户端：任何请求都回 "{}"。"""

    def __init__(self) -> None:
        outer = self
        self.calls = 0

        class _Completions:
            def create(_self, **kw):
                outer.calls += 1

                class _Msg:
                    content = "{}"

                class _Choice:
                    message = _Msg()

                class _Resp:
                    model = "fake-model"
                    choices = [_Choice()]

                return _Resp()

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


@pytest.fixture
def rig(tmp_path, monkeypatch):
    db = tmp_path / "l.sqlite3"
    audit = Audit(db)

    def make_llm(*args, channel: str = "", **kwargs) -> LLM:
        llm = LLM(mode="api", model="fake-model", api_base="https://gw.example/v1", api_key="sk-test",
                  audit=audit, channel=channel)
        llm._client = _EmptyGateway()
        return llm

    monkeypatch.setattr(service, "LLM", make_llm)
    return SimpleNamespace(audit=audit, make_llm=make_llm, ledger=Ledger(db), memory=Memory(db))


def _rows(audit: Audit, since: int = 0) -> list[dict]:
    return [r for r in reversed(audit.recent(200)) if r["id"] > since]


def _purposes(audit: Audit, since: int = 0) -> list[str]:
    return [r["purpose"] for r in _rows(audit, since)]


def _last_id(audit: Audit) -> int:
    rows = audit.recent(1)
    return rows[0]["id"] if rows else 0


@pytest.fixture
def client(rig, monkeypatch):
    from fastapi.testclient import TestClient

    import api

    monkeypatch.setattr(api, "_ledger", rig.ledger)
    monkeypatch.setattr(api, "_memory", rig.memory)
    monkeypatch.setattr(api, "API_TOKEN", "")
    return TestClient(api.app)


def test_rest_clarify_only_polishes_intake(rig, client):
    r = client.post("/api/v1/clarify", json={"text": SAMPLE, "polish": True})
    assert r.status_code == 200
    rows = _rows(rig.audit)
    assert [x["purpose"] for x in rows] == ["问清润色"]
    # 正常路径都已脱敏：默认的明文拒发策略不会误拦
    assert all(x["ok"] == 1 and x["plain_hits"] == 0 and x["channel"] == "API" for x in rows)


def test_rest_analyze_runs_only_requested_stages(rig, client):
    mark = _last_id(rig.audit)
    d = client.post("/api/v1/analyze", json={"text": SAMPLE, "polish": True}).json()
    assert set(_purposes(rig.audit, mark)) == {"问清润色", "写单润色", "估时分析"}       # 默认不要原型：不调出样改稿
    assert d["estimate"]["status"] == "failed"                                      # 假网关的空 JSON 过不了校验
    mark = _last_id(rig.audit)
    client.post("/api/v1/analyze", json={"text": SAMPLE, "polish": True, "include_spec": False})
    assert set(_purposes(rig.audit, mark)) == {"问清润色", "估时分析"}
    mark = _last_id(rig.audit)
    client.post("/api/v1/analyze", json={"text": SAMPLE, "polish": True, "include_prototype": True})
    assert "出样改稿" in _purposes(rig.audit, mark)
    assert all(x["ok"] == 1 and x["plain_hits"] == 0 for x in _rows(rig.audit))


def test_stage_switched_off_says_so(rig):
    a = service.run(SAMPLE, polish=True, record=False, memory=rig.memory, stages={"intake"}).analysis
    assert a.estimate.status == "unavailable" and a.estimate.error == "本次调用未请求模型估时"
    assert _purposes(rig.audit) == ["问清润色"]


def test_mcp_tools_run_only_their_stages(rig, monkeypatch):
    import mcp_server as ms

    monkeypatch.setattr(ms, "_ledger", rig.ledger)
    monkeypatch.setattr(ms, "_memory", rig.memory)

    async def go(tool: str, **args) -> list[str]:
        mark = _last_id(rig.audit)
        await ms.mcp.call_tool(tool, {"text": SAMPLE, "polish": True, **args})
        return _purposes(rig.audit, mark)

    assert asyncio.run(go("xuzhi_clarify")) == ["问清润色"]
    assert asyncio.run(go("xuzhi_spec")) == ["问清润色", "写单润色"]
    assert set(asyncio.run(go("xuzhi_analyze"))) == {"问清润色", "写单润色", "估时分析"}
    assert {x["channel"] for x in _rows(rig.audit)} == {"MCP"}


def test_audit_rows_carry_req_id(rig, client):
    """首轮调模型时还没有编号：记账后回填；带 req_id 的重算直接记上编号。"""
    d = client.post("/api/v1/analyze", json={"text": SAMPLE, "polish": True}).json()
    rid = d["req_id"]
    assert rid >= 1 and {x["req_id"] for x in _rows(rig.audit)} == {rid}
    mark = _last_id(rig.audit)
    qid = d["questions"][0]["id"]
    client.post("/api/v1/clarify", json={"text": SAMPLE, "polish": True, "req_id": rid, "answers": {qid: "按结算价"}})
    assert {x["req_id"] for x in _rows(rig.audit, mark)} == {rid}
    # 不记账的调用没有编号可填
    mark = _last_id(rig.audit)
    client.post("/api/v1/clarify", json={"text": SAMPLE, "polish": True, "record": False})
    assert {x["req_id"] for x in _rows(rig.audit, mark)} == {0}


def test_wecom_default_path_calls_intake_and_estimate_only(rig, monkeypatch):
    pytest.importorskip("websockets", reason="企微入口依赖：pip install -r requirements-wecom.txt")
    from xuzhi.channels.wecom.config import AuthConfig, BotConfig, Limits, WecomConfig
    from xuzhi.channels.wecom.handler import WecomHandler

    monkeypatch.setattr("xuzhi.llm.LLM", rig.make_llm)   # 企微入口在线程里现建 LLM
    cfg = WecomConfig(bot=BotConfig(bot_id="b", secret="s"), auth=AuthConfig(users={"u1"}), limits=Limits())
    handler = WecomHandler(cfg, conn=None, ledger=rig.ledger, memory=rig.memory)
    session = handler.sessions.get("wrCHAT:u1")
    session.start(SAMPLE)
    analysis = handler._analyze_blocking(session)
    handler._bookkeep(session, analysis, answered=0)
    rows = _rows(rig.audit)
    assert [x["purpose"] for x in rows] == ["问清润色", "估时分析"]                  # 不润色需求单、不改原型
    assert {x["channel"] for x in rows} == {"企微"} and {x["req_id"] for x in rows} == {session.req_id}
    assert session.req_id >= 1 and session.audit_sink is None
    # 答复重算：编号直接记上
    mark = _last_id(rig.audit)
    session.answers[analysis.questions[0].id] = "按结算价"
    again = handler._analyze_blocking(session)
    handler._bookkeep(session, again, answered=1)
    assert {x["req_id"] for x in _rows(rig.audit, mark)} == {session.req_id}
    assert len(rig.ledger.recent()) == 1
