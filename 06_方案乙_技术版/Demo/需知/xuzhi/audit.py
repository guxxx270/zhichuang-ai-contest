"""模型调用审计：每一次大模型调用都落一行（借鉴 OryxOS "强制审计"）。

记什么：时间、用途（问清润色 / 写单润色 / 出样改稿…）、渠道（Web / 企微 / API / MCP）、
后端与网关主机（不记 Key、不记完整地址参数）、模型、耗时、提示词与回复长度、
出站提示词里的脱敏标签数（证明进模型前过了隐盾）、出站提示词里检出的明文敏感项数（应为 0）、成败与错误摘要。
不记提示词与回复正文——正文可能含业务信息，审计只需要"发了什么级别的东西、去了哪、花了多久"。
与一本账同一个 SQLite。"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime
from urllib.parse import urlparse

from . import config

_TAG_RE = re.compile(r"<[^_<>\s]{1,8}_\d+>")


def count_redaction_tags(*texts: str) -> int:
    """出站文本里 <手机_1> 这类隐盾标签的个数（去重按出现次数计）。"""
    return sum(len(_TAG_RE.findall(t or "")) for t in texts)


def count_plain_sensitive(*texts: str) -> int:
    """出站文本里还能被隐盾规则命中的明文敏感项数（理想为 0；金额不计，属业务数字）。"""
    from .privacy import Redactor

    n = 0
    for t in texts:
        r = Redactor().redact(t or "")
        n += sum(v for k, v in r.counts.items() if k != "金额")
    return n


def host_of(api_base: str) -> str:
    try:
        return urlparse((api_base or "").strip()).hostname or (api_base or "")[:40]
    except ValueError:
        return (api_base or "")[:40]


class Audit:
    def __init__(self, path=None) -> None:
        self.path = str(path or config.LEDGER_DB)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS llm_calls(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, purpose TEXT, channel TEXT, backend TEXT, host TEXT, model TEXT,
            duration_ms INTEGER, prompt_chars INTEGER, completion_chars INTEGER, redacted_tags INTEGER, plain_hits INTEGER,
            ok INTEGER, error TEXT, req_id INTEGER);
        """)
        self.conn.commit()

    def record(self, *, purpose: str, backend: str, api_base: str, model: str, duration_ms: int, prompt_chars: int,
               completion_chars: int, redacted_tags: int, plain_hits: int, ok: bool, error: str = "", channel: str = "",
               req_id: int = 0) -> int:
        cur = self.conn.execute(
            "INSERT INTO llm_calls(ts,purpose,channel,backend,host,model,duration_ms,prompt_chars,completion_chars,redacted_tags,plain_hits,ok,error,req_id) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), purpose or "", channel or "", backend or "", host_of(api_base), model or "",
             int(duration_ms), int(prompt_chars), int(completion_chars), int(redacted_tags), int(plain_hits), 1 if ok else 0,
             (error or "")[:200], int(req_id or 0)))
        self.conn.commit()
        return int(cur.lastrowid)

    def recent(self, n: int = 30) -> list[dict]:
        cur = self.conn.execute(
            "SELECT id,ts,purpose,channel,backend,host,model,duration_ms,prompt_chars,completion_chars,redacted_tags,plain_hits,ok,error,req_id "
            "FROM llm_calls ORDER BY id DESC LIMIT ?", (n,))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def export_rows(self) -> list[dict]:
        cur = self.conn.execute(
            "SELECT id,ts,purpose,channel,backend,host,model,duration_ms,prompt_chars,completion_chars,redacted_tags,plain_hits,ok,error,req_id "
            "FROM llm_calls ORDER BY id")
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def stats(self) -> dict:
        n, ok, avg_ms, tags, plain = self.conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(ok),0), COALESCE(AVG(duration_ms),0), COALESCE(SUM(redacted_tags),0), COALESCE(SUM(plain_hits),0) FROM llm_calls"
        ).fetchone()
        models = {m: k for m, k in self.conn.execute("SELECT COALESCE(NULLIF(model,''),'—'), COUNT(*) FROM llm_calls GROUP BY 1 ORDER BY 2 DESC")}
        hosts = {h: k for h, k in self.conn.execute("SELECT COALESCE(NULLIF(host,''),'—'), COUNT(*) FROM llm_calls GROUP BY 1 ORDER BY 2 DESC")}
        return {"calls": int(n), "ok": int(ok), "failed": int(n) - int(ok), "avg_ms": int(avg_ms or 0),
                "redacted_tags": int(tags), "plain_hits": int(plain), "models": models, "hosts": hosts}


_default: Audit | None = None


def default_audit() -> Audit | None:
    """懒加载默认审计库；打不开就返回 None（审计失败不影响调用）。"""
    global _default
    if _default is None:
        try:
            _default = Audit()
        except Exception:   # noqa: BLE001
            return None
    return _default
