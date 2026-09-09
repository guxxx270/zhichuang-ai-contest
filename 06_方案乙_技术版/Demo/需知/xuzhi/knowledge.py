"""知识层：期货追问知识库、公司系统目录、历史需求库（都是 JSON，换成公司实际即可）。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache

from . import config


@dataclass(frozen=True)
class Probe:
    id: str
    category: str
    tag: str            # 期货 / 通用
    impact: str         # 高 / 中 / 低
    triggers: tuple[str, ...]
    question: str
    why: str
    default: str

    def hits(self, text: str) -> bool:
        return any(re.search(t, text) for t in self.triggers)


@dataclass(frozen=True)
class System:
    id: str
    name: str
    owner: str
    capabilities: tuple[str, ...]
    integration: str
    maturity: str


@dataclass(frozen=True)
class HistoryItem:
    id: str
    year: int
    title: str
    type: str
    dept: str
    keywords: tuple[str, ...]
    estimate_days: float
    actual_days: float
    note: str
    ai_assisted: bool = False
    ai_share: float = 0.0


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def probes() -> list[Probe]:
    raw = _load(config.PROBES_PATH)["probes"]
    return [Probe(p["id"], p["category"], p["tag"], p["impact"], tuple(p["triggers"]), p["question"], p["why"], p["default"]) for p in raw]


@lru_cache(maxsize=1)
def systems() -> list[System]:
    raw = _load(config.CATALOG_PATH)["systems"]
    return [System(s["id"], s["name"], s["owner"], tuple(s["capabilities"]), s["integration"], s["maturity"]) for s in raw]


@lru_cache(maxsize=1)
def history() -> list[HistoryItem]:
    raw = _load(config.HISTORY_PATH)["items"]
    return [HistoryItem(h["id"], h["year"], h["title"], h["type"], h["dept"], tuple(h["keywords"]), h["estimate_days"], h["actual_days"], h.get("note", ""), bool(h.get("ai_assisted", False)), float(h.get("ai_share", 0))) for h in raw]


IMPACT_ORDER = {"高": 0, "中": 1, "低": 2}
