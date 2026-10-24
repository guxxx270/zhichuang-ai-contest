"""模型调用审计：每一次大模型调用都落一行（借鉴 OryxOS "强制审计"）。

记什么：时间、用途（问清润色 / 写单润色 / 出样改稿…）、渠道（Web / 企微 / API / MCP / 命令行）、需求编号、
后端与网关主机（不记 Key、不记完整地址参数）、模型、耗时、提示词与回复长度、
出站提示词里的脱敏标签数（证明进模型前过了隐盾）、出站提示词里检出的明文敏感项数（应为 0）、成败与错误摘要。
不记提示词与回复正文——正文可能含业务信息，审计只需要"发了什么级别的东西、去了哪、花了多久"；
错误摘要也先去掉地址参数、Key 与 Token 再存。
与一本账同一个 SQLite。"""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
from datetime import datetime
from urllib.parse import urlparse

from . import config

log = logging.getLogger(__name__)

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


_URL_RE = re.compile(r"(?i)\b(?:https?|wss?)://[^\s'\"<>()\[\]{}，。；]+")
_SK_RE = re.compile(r"\bsk-[A-Za-z0-9_\-]{4,}")
_BEARER_RE = re.compile(r"(?i)(\bbearer\s+)[^\s'\",;]+")
_KV_RE = re.compile(r"(?i)((?:token|key|secret|password|passwd|pat)[\"']?\s*[=:]\s*[\"']?)[^\s&'\",;}]+")


def _sanitize_error(text: str) -> str:
    """错误摘要入库前去敏：保留异常类名与 HTTP 状态码；网址只留主机名；sk- 串、Bearer 后的令牌、
    token= / key= / secret= 之后的值换成 [已隐藏]；最多 120 字。"""
    s = str(text or "")
    s = _URL_RE.sub(lambda m: host_of(m.group(0)), s)
    s = _SK_RE.sub("[已隐藏]", s)
    s = _BEARER_RE.sub(r"\1[已隐藏]", s)
    s = _KV_RE.sub(r"\1[已隐藏]", s)
    return s[:120]


class Audit:
    """一个实例共用一条连接：Web 后台任务、企微线程、接口可能同时写，读写都在锁内，避免并发时丢行。"""

    def __init__(self, path=None) -> None:
        self.path = str(path or config.LEDGER_DB)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        with self._lock:
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
        """写一行，返回行 id；库忙等原因失败时重试一次，仍失败记告警并返回 0（审计失败不影响业务，但不再静默）。"""
        row = (datetime.now().isoformat(timespec="seconds"), purpose or "", channel or "", backend or "", host_of(api_base), model or "",
               int(duration_ms), int(prompt_chars), int(completion_chars), int(redacted_tags), int(plain_hits), 1 if ok else 0,
               _sanitize_error(error), int(req_id or 0))
        error_seen: Exception | None = None
        for _ in range(2):
            with self._lock:
                try:
                    cur = self.conn.execute(
                        "INSERT INTO llm_calls(ts,purpose,channel,backend,host,model,duration_ms,prompt_chars,completion_chars,"
                        "redacted_tags,plain_hits,ok,error,req_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
                    self.conn.commit()
                    return int(cur.lastrowid)
                except sqlite3.Error as e:
                    error_seen = e
                    try:
                        self.conn.rollback()
                    except sqlite3.Error:
                        pass
        log.warning("模型调用审计写入失败（%s · %s）：%s", purpose or "模型调用", channel or "未注明渠道", error_seen)
        return 0

    def assign_req_id(self, ids, req_id: int) -> int:
        """首轮调用模型时还没有需求编号：记账后把这几行补上编号（只补还没有编号的行）。返回更新行数。"""
        ids = [int(i) for i in (ids or []) if i]
        if not ids or not req_id:
            return 0
        marks = ",".join("?" * len(ids))
        with self._lock:
            cur = self.conn.execute(
                f"UPDATE llm_calls SET req_id=? WHERE id IN ({marks}) AND COALESCE(req_id,0)=0", (int(req_id), *ids))
            self.conn.commit()
            return int(cur.rowcount or 0)

    def recent(self, n: int = 30) -> list[dict]:
        with self._lock:
            cur = self.conn.execute(
                "SELECT id,ts,purpose,channel,backend,host,model,duration_ms,prompt_chars,completion_chars,redacted_tags,plain_hits,ok,error,req_id "
                "FROM llm_calls ORDER BY id DESC LIMIT ?", (n,))
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def export_rows(self) -> list[dict]:
        with self._lock:
            cur = self.conn.execute(
                "SELECT id,ts,purpose,channel,backend,host,model,duration_ms,prompt_chars,completion_chars,redacted_tags,plain_hits,ok,error,req_id "
                "FROM llm_calls ORDER BY id")
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def stats(self) -> dict:
        with self._lock:
            n, ok, avg_ms, tags, plain = self.conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(ok),0), COALESCE(AVG(duration_ms),0), COALESCE(SUM(redacted_tags),0), COALESCE(SUM(plain_hits),0) FROM llm_calls"
            ).fetchone()
            models = {m: k for m, k in self.conn.execute("SELECT COALESCE(NULLIF(model,''),'—'), COUNT(*) FROM llm_calls GROUP BY 1 ORDER BY 2 DESC")}
            hosts = {h: k for h, k in self.conn.execute("SELECT COALESCE(NULLIF(host,''),'—'), COUNT(*) FROM llm_calls GROUP BY 1 ORDER BY 2 DESC")}
        return {"calls": int(n), "ok": int(ok), "failed": int(n) - int(ok), "avg_ms": int(avg_ms or 0),
                "redacted_tags": int(tags), "plain_hits": int(plain), "models": models, "hosts": hosts}


class RecordingSink:
    """包一层审计：照常落库，同时记下本次写入的行 id。首轮调模型时还没有需求编号，记账后用 assign() 回填。
    其余属性（conn、stats…）转给底层审计。"""

    def __init__(self, sink) -> None:
        self.sink = sink
        self.ids: list[int] = []

    def record(self, **kw) -> int:
        row_id = self.sink.record(**kw)
        if row_id:
            self.ids.append(int(row_id))
        return row_id

    def assign(self, req_id: int) -> int:
        if not req_id or not self.ids or not hasattr(self.sink, "assign_req_id"):
            return 0
        try:
            return int(self.sink.assign_req_id(list(self.ids), req_id) or 0)
        except Exception as e:   # noqa: BLE001  回填失败不影响记账
            log.warning("模型调用审计回填需求编号失败：%s", e)
            return 0

    def __getattr__(self, name):
        if name == "sink" or name.startswith("__"):   # 复制 / 序列化时还没有 sink，别无限递归
            raise AttributeError(name)
        return getattr(self.sink, name)


def track(llm) -> RecordingSink | None:
    """给 api 模式的 LLM 套上 RecordingSink，返回这一层；mock、不审计（audit=False）或默认审计库打不开时返回 None。"""
    if getattr(llm, "mode", "") != "api" or getattr(llm, "audit", None) is False:
        return None
    current = getattr(llm, "audit", None)
    if isinstance(current, RecordingSink):
        return current
    sink = current if current is not None else default_audit()
    if sink is None:
        return None
    llm.audit = RecordingSink(sink)
    return llm.audit


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
