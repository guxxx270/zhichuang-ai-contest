"""模型调用审计 + 沙箱策略。"""
from __future__ import annotations

import pytest

from xuzhi import sandbox
from xuzhi.audit import Audit, count_plain_sensitive, count_redaction_tags, host_of
from xuzhi.drafts import check_clone_url, is_secret_path
from xuzhi.llm import LLM


class _FakeClient:
    """假 OpenAI 客户端：记录调用并返回固定内容或抛错。"""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = []

        class _Completions:
            def create(_self, **kw):
                self.calls.append(kw)
                if self.fail:
                    raise RuntimeError("gateway 502")

                class _Msg:
                    content = '{"title": "润色标题"}'

                class _Choice:
                    message = _Msg()

                class _Resp:
                    model = "deepseek-v4-flash"
                    choices = [_Choice()]

                return _Resp()

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


def _llm(audit: Audit, client) -> LLM:
    llm = LLM(mode="api", model="deepseek-ai/DeepSeek-V4-Flash", api_base="https://api.siliconflow.cn/v1", api_key="sk-test",
              audit=audit, channel="测试")
    llm._client = client
    return llm


def test_counts():
    assert count_redaction_tags("客户 <客户_1> 手机 <手机_1> 再来一次 <手机_1>") == 3
    assert count_plain_sensitive("联系 13812345678，账号 6222021234567890123") == 2
    assert count_plain_sensitive("金额 1,234,567 元") == 0            # 金额不算敏感
    assert count_plain_sensitive("<手机_1> 已脱敏") == 0
    assert host_of("https://api.siliconflow.cn/v1") == "api.siliconflow.cn"


def test_every_call_is_audited(tmp_path):
    audit = Audit(tmp_path / "a.sqlite3")
    llm = _llm(audit, _FakeClient())
    llm.current_purpose = "问清润色"
    out = llm.chat_json("system", "原话：<手机_1> 想加一列 <客户_1>")
    assert out == {"title": "润色标题"}
    rows = audit.recent()
    assert len(rows) == 1
    r = rows[0]
    assert r["purpose"] == "问清润色" and r["channel"] == "测试" and r["host"] == "api.siliconflow.cn"
    assert r["model"] == "deepseek-v4-flash" and r["ok"] == 1 and r["redacted_tags"] == 2 and r["plain_hits"] == 0
    assert r["prompt_chars"] > 0 and r["completion_chars"] > 0
    # 失败也记
    bad = _llm(audit, _FakeClient(fail=True))
    with pytest.raises(RuntimeError):
        bad.chat("s", "u", purpose="写单润色")
    st = audit.stats()
    assert st["calls"] == 2 and st["ok"] == 1 and st["failed"] == 1
    assert audit.recent()[0]["error"].startswith("RuntimeError")
    assert len(audit.export_rows()) == 2
    # 明文敏感项会被计数（不阻断，Demo 策略 block_if_plain_sensitive=false）
    _llm(audit, _FakeClient()).chat("s", "手机 13812345678", purpose="出样改稿")
    assert audit.recent()[0]["plain_hits"] == 1


def test_block_if_plain_sensitive(tmp_path, monkeypatch):
    audit = Audit(tmp_path / "a.sqlite3")
    monkeypatch.setitem(sandbox.policy()["llm"], "block_if_plain_sensitive", True)
    llm = _llm(audit, _FakeClient())
    with pytest.raises(RuntimeError, match="沙箱策略拒发"):
        llm.chat("s", "手机 13812345678")
    assert audit.recent()[0]["ok"] == 0
    assert llm.chat("s", "已脱敏 <手机_1>") == '{"title": "润色标题"}'


def test_mock_mode_not_audited(tmp_path):
    audit = Audit(tmp_path / "a.sqlite3")
    llm = LLM(mode="mock", audit=audit)
    assert llm.chat("s", "u") == ""
    assert audit.stats()["calls"] == 0


def test_scheme_policy_falls_back_to_rules():
    llm = LLM(mode="api", model="m", api_base="ftp://evil.example/v1", api_key="k", audit=False)
    assert llm.mode == "mock" and "沙箱策略" in llm.note


def test_policy_loaded_and_enforced():
    p = sandbox.policy()
    assert p["version"] == 1 and p["askcode"]["allowed_tools"] == ["Read", "Grep", "Glob"]
    assert sandbox.get("notify.enabled") is False
    assert sandbox.get("no.such.key", "d") == "d"
    rows = sandbox.rows()
    assert any(g == "llm" and k == "audit_every_call" and loc for g, k, _, loc in rows)
    assert "sandbox.yaml" in sandbox.raw_text() or "内置默认值" in sandbox.raw_text()
    # 仓库协议与凭据文件 glob 由策略驱动
    assert check_clone_url("https://github.com/acme/demo.git") == ""
    assert "只支持" in check_clone_url("ssh://git@github.com/acme/demo.git")
    assert is_secret_path("config/app.pem") and is_secret_path("deploy/id_rsa") and is_secret_path("k8s/db-credentials.yaml")
    assert not is_secret_path("xuzhi/llm.py")
    assert set(sandbox.summary()) >= {"模型", "仓库", "文件", "企微", "接口", "数据", "提醒"}


def test_policy_override_changes_behaviour(monkeypatch):
    monkeypatch.setitem(sandbox.policy()["files"], "deny_read_globs", ["*.xyz"])
    assert is_secret_path("a/b.xyz")
    monkeypatch.setitem(sandbox.policy()["repo_fetch"], "allowed_schemes", ["https"])
    assert "只支持" in check_clone_url("http://github.com/acme/demo.git")
