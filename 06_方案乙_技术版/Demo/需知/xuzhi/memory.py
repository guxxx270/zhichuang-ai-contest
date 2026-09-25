"""追问记忆：问过的不再问。

同一提出方（部门 · 角色）上次答过的口径记下来（"比较基准 = 上一交易日结算价"），
下次同类需求不再问、直接沿用并在清单里标"沿用上次口径，有出入请改"。
业务改口径 → 记忆随之更新；一本账页可查看、清空。

借鉴 OryxOS 的 per-agent 长期记忆思路，但只记"业务对追问的答复"这一种事实，
不记原话、不记客户信息；表放在一本账同一个 SQLite 里，便于审计与导出。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

from . import config

# 不进记忆的题：位置类（每条需求不同）、模型临时补问（id 不稳定）
_NO_MEMORY_IDS = {"C_POS"}
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


class Memory:
    """scope = 'requester'（部门·角色，最具体）| 'dept'（部门）。查时先具体后部门。"""

    def __init__(self, path=None) -> None:
        self.path = str(path or config.LEDGER_DB)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
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
        """业务答复后调用：把有答复且值得记的题写进记忆（提出方 + 部门两级各记一份）。返回写入条数。"""
        requester = (getattr(card, "requester", "") or "").strip()
        if not requester:
            return 0
        dept = _dept_of(requester)
        scopes = [("requester", requester)]
        if dept and dept != requester:
            scopes.append(("dept", dept))
        ts = datetime.now().isoformat(timespec="seconds")
        n = 0
        for q in questions:
            if not memorable(q):
                continue
            ans = q.answer.strip()
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
        """清一条 / 清一个提出方 / 全清。返回删除条数。"""
        if mem_id is not None:
            cur = self.conn.execute("DELETE FROM answer_memory WHERE id=?", (int(mem_id),))
        elif scope_key:
            cur = self.conn.execute("DELETE FROM answer_memory WHERE scope_key=?", (scope_key,))
        else:
            cur = self.conn.execute("DELETE FROM answer_memory")
        self.conn.commit()
        return int(cur.rowcount or 0)

    # ---------- 读 ----------
    def lookup(self, requester: str, qid: str) -> dict | None:
        """先查提出方（部门·角色），再查部门。命中返回 {answer, source, ts, id}。"""
        requester = (requester or "").strip()
        if not requester or not qid:
            return None
        keys = [("requester", requester)]
        dept = _dept_of(requester)
        if dept and dept != requester:
            keys.append(("dept", dept))
        for scope, key in keys:
            row = self.conn.execute(
                "SELECT id, answer, ts, scope_key FROM answer_memory WHERE scope=? AND scope_key=? AND qid=?", (scope, key, qid)).fetchone()
            if row and (row[1] or "").strip():
                return {"id": int(row[0]), "answer": row[1], "ts": row[2] or "", "source": row[3]}
        return None

    def recall(self, card, questions) -> int:
        """开工时调用：未答复的题若有记忆 → 填成答复并标 recalled。返回沿用条数（并累计 hits）。"""
        requester = (getattr(card, "requester", "") or "").strip()
        if not requester:
            return 0
        n = 0
        for q in questions:
            if (getattr(q, "answer", "") or "").strip():
                continue
            hit = self.lookup(requester, q.id)
            if not hit:
                continue
            q.answer = hit["answer"]
            q.recalled = f"{hit['source']} · {hit['ts'][:10]}"
            self.conn.execute("UPDATE answer_memory SET hits=hits+1 WHERE id=?", (hit["id"],))
            n += 1
        if n:
            self.conn.commit()
        return n

    def rows(self, limit: int = 200) -> list[dict]:
        cur = self.conn.execute(
            "SELECT id, scope, scope_key, qid, question, answer, req_type, ts, hits FROM answer_memory ORDER BY scope_key, qid LIMIT ?", (limit,))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def stats(self) -> dict:
        n, hits, keys = self.conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(hits),0), COUNT(DISTINCT scope_key) FROM answer_memory WHERE scope='requester' OR scope='dept'").fetchone()
        n_req = self.conn.execute("SELECT COUNT(*) FROM answer_memory WHERE scope='requester'").fetchone()[0]
        return {"entries": int(n_req), "hits": int(hits), "requesters": int(keys)}
