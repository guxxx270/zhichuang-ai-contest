"""催办摘要（附加功能）：只算数量、标题、天数；默认不推送。"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from xuzhi import reminders, sandbox
from xuzhi.ledger import Ledger
from xuzhi.pipeline import analyze

T1 = "资管运营小周：净值日报能不能加一列'较上一交易日变动'，风险指标也放进去，周五给客户那版也要，最好下周就能用。客户张伟 13812345678。"
T2 = "研究所：做一个基差监控页面，盘中要能看，超过阈值提醒。"
T3 = "风控部：每天结算后把各产品保证金占用汇总成一张表发给我。"


@pytest.fixture
def ledger(tmp_path):
    return Ledger(tmp_path / "l.sqlite3")


def _backdate(ledger: Ledger, req_id: int, days: int) -> None:
    ts = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    ledger.conn.execute("UPDATE requirements SET ts=? WHERE id=?", (ts, req_id))
    ledger.conn.commit()


def test_digest_contents_and_privacy(ledger):
    r1 = ledger.log_analysis(analyze(T1))     # 受理、高影响未答 → 回溯 5 天 → 超期
    r2 = ledger.log_analysis(analyze(T2))     # 已交付 8 天未对账
    r3 = ledger.log_analysis(analyze(T3))     # 今天受理，不超期
    _backdate(ledger, r1, 5)
    ledger.set_status(r2, "已交付")
    _backdate(ledger, r2, 8)
    d = reminders.build_digest(ledger)
    assert [it.req_id for it in d.stale_confirm] == [r1]
    assert d.stale_confirm[0].days == 5 and "高影响" in d.stale_confirm[0].detail or "答复" in d.stale_confirm[0].detail
    assert [it.req_id for it in d.stale_reconcile] == [r2] and d.stale_reconcile[0].days == 8
    assert d.week_total >= 1 and d.by_status.get("已交付") == 1
    md = reminders.render_digest(d)
    assert "催办摘要" in md and f"#{r1}" in md and f"#{r2}" in md and "本周受理" in md
    assert "13812345678" not in md and "张伟" not in md and "净值日报能不能" not in md   # 不含原话与客户信息
    dd = reminders.digest_to_dict(d)
    assert dd["markdown"] == md and dd["stale_confirm"][0]["req_id"] == r1
    # 对账后不再催
    ledger.reconcile(r2, 6, write_history=False)
    assert not reminders.build_digest(ledger).stale_reconcile
    assert r3 not in [it.req_id for it in reminders.build_digest(ledger).stale_confirm]


def test_empty_ledger(ledger):
    d = reminders.build_digest(ledger)
    assert d.empty and "暂无" in reminders.render_digest(d)


def test_push_is_off_by_default(ledger, monkeypatch):
    monkeypatch.delenv("XUZHI_DIGEST_WEBHOOK", raising=False)
    ok, why = reminders.push_enabled()
    assert not ok and "notify.enabled" in why
    assert reminders.send_digest(ledger) == (False, why)
    # 只开策略不配 webhook 也不发
    monkeypatch.setitem(sandbox.policy()["notify"], "enabled", True)
    ok, why = reminders.push_enabled()
    assert not ok and "WEBHOOK" in why
    # 两把锁都开：走 webhook（这里拦截 urlopen）
    monkeypatch.setenv("XUZHI_DIGEST_WEBHOOK", "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x")
    assert reminders.push_enabled()[0]
    sent = {}

    def fake_push(markdown, url="", timeout=10):
        sent["md"] = markdown
        return {"errcode": 0}

    monkeypatch.setattr(reminders, "push_webhook", fake_push)
    assert reminders.send_digest(ledger) == (False, "暂无需要跟进的需求，不推送")
    rid = ledger.log_analysis(analyze(T1))
    _backdate(ledger, rid, 4)
    ok, msg = reminders.send_digest(ledger)
    assert ok and "待确认 1 条" in msg and "13812345678" not in sent["md"]


def test_webhook_requires_https():
    with pytest.raises(ValueError):
        reminders.push_webhook("x", url="http://example.com/hook")


def test_seconds_until():
    now = datetime(2026, 9, 25, 10, 0, 0)
    assert reminders.seconds_until("10:30", now) == 1800
    assert reminders.seconds_until("09:00", now) == 23 * 3600


def test_cli_print(ledger, monkeypatch, capsys):
    monkeypatch.setattr("xuzhi.ledger.Ledger", lambda *a, **k: ledger)
    assert reminders.main(["--print"]) == 0
    assert "催办摘要" in capsys.readouterr().out
    assert reminders.main(["--json"]) == 0
    assert '"as_of"' in capsys.readouterr().out
