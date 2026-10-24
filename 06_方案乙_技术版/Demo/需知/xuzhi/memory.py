"""追问记忆：问过的不再问。

同一提出方（部门 · 角色）上次答过的口径记下来（"比较基准 = 上一交易日结算价"），
下次同类需求不再问、直接沿用并在清单里标"沿用上次口径，有出入请改"。
业务改口径 → 记忆随之更新；一本账页可查看、清空。

借鉴 OryxOS 的 per-agent 长期记忆思路，但只记"业务对追问的答复"这一种事实，
不记原话、不记客户信息（答复里含手机号、账号、客户名等隐盾能识别的项就不记）；
表放在一本账同一个 SQLite 里，便于审计与导出。
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime

from . import config
from .privacy import Redactor

# 不进记忆的题：位置类与新增列的格式类（每条需求的列都不同，沿用到别的列会张冠李戴）、模型临时补问（id 不稳定）
_NO_MEMORY_IDS = {"C_POS", "C_FMT", "C_NULL", "C_SIGN"}
_NO_MEMORY_PREFIX = ("L",)


def _dept_of(requester: str) -> str:
    r = (requester or "").strip()
    return r.split("·")[0].split("/")[0].strip() if r else ""


def memorable(q) -> bool:
    """这道题的答复值不值得记：id 稳定、不是位置题、答复非空。"""
    qid = str(getattr(q, "id", "") or "")
    if not qid or qid in _NO_MEMORY_IDS or qid.startswith(_NO_MEMORY_PREFIX):
        return False
    return bool((getattr(q, "answer", "") or "").strip())


def _has_sensitive(text: str) -> bool:
    """答复里有隐盾能识别的敏感项（手机、账号、证件、客户名…；金额属业务数字不算）。这类答复不长期存、不跨需求沿用。"""
    counts = Redactor().redact(text or "").counts
    return any(n for kind, n in counts.items() if kind != "金额")


class Memory:
    """scope = 'requester'（部门·角色，最具体）| 'dept'（部门）。查时先具体后部门。

    一个实例共用一条连接，可能被多个线程同时用（REST / MCP / 企微后台线程），读写都在锁内。"""

    def __init__(self, path=None) -> None:
        self.path = str(path or config.LEDGER_DB)
        self._lock = threading.RLock()
        self.last_skipped = 0     # 最近一次 learn 因含敏感项没记的题数
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        with self._lock:
            self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS answer_memory(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT, scope_key TEXT, qid TEXT, question TEXT, answer TEXT,
                req_type TEXT, ts TEXT, hits INTEGER DEFAULT 0, req_id INTEGER,
                UNIQUE(scope, scope_key, qid));
            """)
            self.conn.commit()

    # ---------- 写 ----------
    def learn(self, card, questions, req_id: int = 0) -> int:
        """业务答复后调用：把有答复且值得记的题写进记忆（提出方 + 部门两级各记一份）。返回写入条数。
        答复含敏感项的题不记，条数见 last_skipped。"""
        self.last_skipped = 0
        requester = (getattr(card, "requester", "") or "").strip()
        if not requester:
            return 0
        dept = _dept_of(requester)
        scopes = [("requester", requester)]
        if dept and dept != requester:
            scopes.append(("dept", dept))
        ts = datetime.now().isoformat(timespec="seconds")
        n = 0
        with self._lock:
            for q in questions:
                if not memorable(q):
                    continue
                ans = q.answer.strip()
                if _has_sensitive(ans):
                    self.last_skipped += 1
                    continue
                for scope, key in scopes:
                    self.conn.execute(
                        "INSERT INTO answer_memory(scope,scope_key,qid,question,answer,req_type,ts,hits,req_id) VALUES(?,?,?,?,?,?,?,0,?) "
                        "ON CONFLICT(scope,scope_key,qid) DO UPDATE SET answer=excluded.answer, question=excluded.question, "
                        "req_type=excluded.req_type, ts=excluded.ts, req_id=excluded.req_id",
                        (scope, key, q.id, q.question, ans, getattr(card, "req_type", "") or "", ts, int(req_id or 0)))
                n += 1
            self.conn.commit()
        return n

    def forget(self, mem_id: int | None = None, scope_key: str | None = None) -> int:
        """清一条 / 清一个提出方 / 全清。返回删除条数。
        清提出方时，部门级里由他记下的副本（同题、同答复）一并清掉，否则同部门查询还会命中。"""
        with self._lock:
            if mem_id is not None:
                cur = self.conn.execute("DELETE FROM answer_memory WHERE id=?", (int(mem_id),))
                n = int(cur.rowcount or 0)
            elif scope_key:
                n = 0
                dept = _dept_of(scope_key)
                if dept and dept != scope_key:
                    cur = self.conn.execute(
                        "DELETE FROM answer_memory WHERE scope='dept' AND scope_key=? AND EXISTS ("
                        "SELECT 1 FROM answer_memory r WHERE r.scope='requester' AND r.scope_key=? "
                        "AND r.qid=answer_memory.qid AND r.answer=answer_memory.answer)", (dept, scope_key))
                    n += int(cur.rowcount or 0)
                cur = self.conn.execute("DELETE FROM answer_memory WHERE scope_key=?", (scope_key,))
                n += int(cur.rowcount or 0)
            else:
                cur = self.conn.execute("DELETE FROM answer_memory")
                n = int(cur.rowcount or 0)
            self.conn.commit()
        return n

    # ---------- 读 ----------
    def lookup(self, requester: str, qid: str) -> dict | None:
        """先查提出方（部门·角色），再查部门。命中返回 {answer, source, ts, id}。
        老库里含敏感项的记录一律不沿用。"""
        requester = (requester or "").strip()
        if not requester or not qid:
            return None
        keys = [("requester", requester)]
        dept = _dept_of(requester)
        if dept and dept != requester:
            keys.append(("dept", dept))
        with self._lock:
            for scope, key in keys:
                row = self.conn.execute(
                    "SELECT id, answer, ts, scope_key FROM answer_memory WHERE scope=? AND scope_key=? AND qid=?", (scope, key, qid)).fetchone()
                if row and (row[1] or "").strip() and not _has_sensitive(row[1]):
                    return {"id": int(row[0]), "answer": row[1], "ts": row[2] or "", "source": row[3]}
        return None

    def recall(self, card, questions, count_hits: bool = True) -> int:
        """开工时调用：未答复的题若有记忆 → 填成答复并标 recalled。返回沿用条数。
        count_hits=False 时不累计 hits（同一条需求重算，不再算一次「记忆省问」）。"""
        requester = (getattr(card, "requester", "") or "").strip()
        if not requester:
            return 0
        n = 0
        with self._lock:
            for q in questions:
                if (getattr(q, "answer", "") or "").strip():
                    continue
                hit = self.lookup(requester, q.id)
                if not hit:
                    continue
                q.answer = hit["answer"]
                q.recalled = f"{hit['source']} · {hit['ts'][:10]}"
                if count_hits:
                    self.conn.execute("UPDATE answer_memory SET hits=hits+1 WHERE id=?", (hit["id"],))
                n += 1
            if n and count_hits:
                self.conn.commit()
        return n

    def rows(self, limit: int = 200) -> list[dict]:
        with self._lock:
            cur = self.conn.execute(
                "SELECT id, scope, scope_key, qid, question, answer, req_type, ts, hits FROM answer_memory ORDER BY scope_key, qid LIMIT ?", (limit,))
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def stats(self) -> dict:
        """entries / requesters 只数提出方一级（部门级是副本，不重复计）；hits 两级合计。"""
        with self._lock:
            hits = self.conn.execute(
                "SELECT COALESCE(SUM(hits),0) FROM answer_memory WHERE scope='requester' OR scope='dept'").fetchone()[0]
            n_req, keys = self.conn.execute(
                "SELECT COUNT(*), COUNT(DISTINCT scope_key) FROM answer_memory WHERE scope='requester'").fetchone()
        return {"entries": int(n_req), "hits": int(hits), "requesters": int(keys)}
