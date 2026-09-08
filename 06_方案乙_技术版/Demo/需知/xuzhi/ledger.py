"""台账：SQLite 记录每一次分析（卡片 / 清单 / 估算 / 决策）、业务答复与反馈——既是审计留痕，也是估算飞轮的数据。"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from . import config

MINUTES_SAVED = {"问清": 60, "写单": 90, "估量": 30, "定架": 60, "出样": 120}   # 与人工相比的估计节省，方案文档里需校准


class Ledger:
    def __init__(self, path=None) -> None:
        self.path = str(path or config.LEDGER_DB)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS requirements(
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, title TEXT, source TEXT, req_type TEXT, requester TEXT,
            engine TEXT, n_questions INTEGER, n_answered INTEGER, mid_days REAL, confidence TEXT, redacted INTEGER, payload TEXT);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, req_id INTEGER, action TEXT, detail TEXT);
        CREATE TABLE IF NOT EXISTS feedback(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, req_id INTEGER, skill TEXT, score INTEGER, note TEXT);
        """)

    def log_analysis(self, a) -> int:
        cur = self.conn.execute(
            "INSERT INTO requirements(ts,title,source,req_type,requester,engine,n_questions,n_answered,mid_days,confidence,redacted,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), a.card.title, a.card.source, a.card.req_type, a.card.requester, a.card.engine,
             len(a.questions), sum(1 for q in a.questions if q.answer.strip()), a.estimate.mid, a.estimate.confidence, a.redacted,
             json.dumps({"card": json.loads(a.card.to_json()), "questions": [q.__dict__ for q in a.questions],
                         "estimate": {"mid": a.estimate.mid, "low": a.estimate.low, "high": a.estimate.high, "dims": a.estimate.dims},
                         "decisions": [d.recommended for d in a.architecture.decisions]}, ensure_ascii=False)))
        self.conn.commit()
        return int(cur.lastrowid)

    def event(self, req_id: int, action: str, detail: str = "") -> None:
        self.conn.execute("INSERT INTO events(ts,req_id,action,detail) VALUES(?,?,?,?)", (datetime.now().isoformat(timespec="seconds"), req_id, action, detail))
        self.conn.commit()

    def feedback(self, req_id: int, skill: str, score: int, note: str = "") -> None:
        self.conn.execute("INSERT INTO feedback(ts,req_id,skill,score,note) VALUES(?,?,?,?,?)", (datetime.now().isoformat(timespec="seconds"), req_id, skill, score, note))
        self.conn.commit()

    def recent(self, n: int = 20) -> list[dict]:
        cur = self.conn.execute("SELECT id,ts,title,source,req_type,requester,engine,n_questions,n_answered,mid_days,confidence,redacted FROM requirements ORDER BY id DESC LIMIT ?", (n,))
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def stats(self) -> dict:
        n, q, ans, red = self.conn.execute("SELECT COUNT(*), COALESCE(SUM(n_questions),0), COALESCE(SUM(n_answered),0), COALESCE(SUM(redacted),0) FROM requirements").fetchone()
        return {"count": n, "questions": q, "answered": ans, "redacted": red, "minutes_saved": n * sum(MINUTES_SAVED.values())}
