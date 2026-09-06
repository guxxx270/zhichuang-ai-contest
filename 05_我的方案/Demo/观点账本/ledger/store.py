"""观点账本存储：SQLite，一张表存观点卡（JSON），附来源溯源。"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

from . import config
from .schema import OpinionCard


class Ledger:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or config.DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS opinions (key TEXT PRIMARY KEY, analyst TEXT, symbol TEXT, "
            "published_on TEXT, direction TEXT, payload TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        self._conn.commit()

    def add(self, cards: Iterable[OpinionCard]) -> int:
        n = 0
        for c in cards:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO opinions(key, analyst, symbol, published_on, direction, payload) VALUES (?,?,?,?,?,?)",
                (c.key(), c.analyst, c.symbol, c.published_on.isoformat(), c.direction, c.model_dump_json()),
            )
            n += cur.rowcount
        self._conn.commit()
        return n

    def all(self) -> list[OpinionCard]:
        rows = self._conn.execute("SELECT payload FROM opinions ORDER BY published_on, analyst").fetchall()
        return [OpinionCard(**json.loads(r[0])) for r in rows]

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM opinions").fetchone()[0]

    def clear(self) -> None:
        self._conn.execute("DELETE FROM opinions")
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
