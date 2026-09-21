"""知识层：期货追问知识库、公司系统目录（含数据域与接口 = 数据地图）、历史需求库（都是 JSON，换成公司实际即可）。"""
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
class Domain:
    """系统持有的数据域：需求里的数据项靠 keywords 定位到这里。"""
    name: str
    keywords: tuple[str, ...]
    freshness: str      # 实时 / T+1 / 日终 / 事件…

    @property
    def realtime(self) -> bool:
        return "实时" in self.freshness


@dataclass(frozen=True)
class Interface:
    name: str
    type: str           # REST / SQL 视图 / SDK / 文件 / 配置…
    status: str         # 可用 / 需申请… / 改动受控… / 试点…

    @property
    def ready(self) -> bool:
        return self.status.startswith("可用") or self.status.endswith("可用")


@dataclass(frozen=True)
class System:
    id: str
    name: str
    owner: str
    capabilities: tuple[str, ...]
    integration: str
    maturity: str
    domains: tuple[Domain, ...] = ()
    interfaces: tuple[Interface, ...] = ()


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
    out = []
    for s in raw:
        domains = tuple(
            Domain(str(d.get("name", "")), tuple(d.get("keywords") or [d.get("name", "")]), str(d.get("freshness", "")))
            for d in (s.get("data_domains") or []) if d.get("name")
        )
        interfaces = tuple(
            Interface(str(i.get("name", "")), str(i.get("type", "")), str(i.get("status", "可用")))
            for i in (s.get("interfaces") or []) if i.get("name")
        )
        out.append(System(s["id"], s["name"], s["owner"], tuple(s["capabilities"]), s["integration"], s["maturity"], domains, interfaces))
    return out


def data_domains() -> int:
    return sum(len(s.domains) for s in systems())


def interfaces() -> int:
    return sum(len(s.interfaces) for s in systems())


@lru_cache(maxsize=1)
def history() -> list[HistoryItem]:
    """样本 / 导入的历史需求库 + 台账对账回写的真实样本（后者同 id 覆盖前者）。"""
    raw = list(_load(config.HISTORY_PATH)["items"])
    learned = config.HISTORY_LEARNED_PATH
    if learned.exists():
        try:
            extra = _load(learned).get("items") or []
        except (OSError, ValueError):
            extra = []
        ids = {h.get("id") for h in extra}
        raw = [h for h in raw if h.get("id") not in ids] + list(extra)
    return [HistoryItem(h["id"], h["year"], h["title"], h["type"], h["dept"], tuple(h["keywords"]), h["estimate_days"], h["actual_days"], h.get("note", ""), bool(h.get("ai_assisted", False)), float(h.get("ai_share", 0))) for h in raw]


IMPACT_ORDER = {"高": 0, "中": 1, "低": 2}
