"""记忆与留痕：每次技能调用记一条审计（不存原文，只存摘要哈希与脱敏计数）；反馈用于个性化与评测。"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import config


def _digest(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()[:12]


class Memory:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path or config.MEMORY_DB)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._c = sqlite3.connect(self.path, check_same_thread=False)
        self._c.executescript(
            "CREATE TABLE IF NOT EXISTS audit(ts TEXT, skill TEXT, action TEXT, in_digest TEXT, out_digest TEXT, "
            "redacted INTEGER, engine TEXT, note TEXT);"
            "CREATE TABLE IF NOT EXISTS feedback(ts TEXT, skill TEXT, item TEXT, vote INTEGER, comment TEXT);"
            "CREATE TABLE IF NOT EXISTS saved_minutes(ts TEXT, skill TEXT, minutes REAL);"
        )
        self._c.commit()

    def log(self, skill: str, action: str, inp: str = "", out: str = "", redacted: int = 0,
            engine: str = "", note: str = "", minutes: float = 0.0) -> None:
        ts = datetime.now().isoformat(timespec="seconds")
        self._c.execute("INSERT INTO audit VALUES (?,?,?,?,?,?,?,?)",
                        (ts, skill, action, _digest(inp), _digest(out), redacted, engine, note))
        if minutes:
            self._c.execute("INSERT INTO saved_minutes VALUES (?,?,?)", (ts, skill, minutes))
        self._c.commit()

    def feedback(self, skill: str, item: str, vote: int, comment: str = "") -> None:
        self._c.execute("INSERT INTO feedback VALUES (?,?,?,?,?)",
                        (datetime.now().isoformat(timespec="seconds"), skill, item, vote, comment))
        self._c.commit()

    def stats(self) -> dict:
        n = self._c.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
        mins = self._c.execute("SELECT COALESCE(SUM(minutes),0) FROM saved_minutes").fetchone()[0]
        today = datetime.now().date().isoformat()
        mins_today = self._c.execute("SELECT COALESCE(SUM(minutes),0) FROM saved_minutes WHERE ts LIKE ?",
                                     (today + "%",)).fetchone()[0]
        fb = self._c.execute("SELECT COUNT(*), COALESCE(SUM(vote),0) FROM feedback").fetchone()
        return {"calls": n, "minutes_total": round(mins), "minutes_today": round(mins_today),
                "feedback_n": fb[0], "feedback_up": fb[1]}

    def recent(self, limit: int = 20) -> list[tuple]:
        return self._c.execute("SELECT ts, skill, action, redacted, engine, note FROM audit ORDER BY ts DESC LIMIT ?",
                               (limit,)).fetchall()

    def clear(self) -> None:
        self._c.executescript("DELETE FROM audit; DELETE FROM feedback; DELETE FROM saved_minutes;")
        self._c.commit()
