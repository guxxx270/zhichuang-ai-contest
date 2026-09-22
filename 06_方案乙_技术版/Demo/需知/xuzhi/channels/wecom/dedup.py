"""msgid 去重（企微会重试投递）。带 TTL 的小集合，内存即可。"""
from __future__ import annotations

import time
from collections import OrderedDict


class TTLSet:
    def __init__(self, ttl_seconds: float, max_items: int = 10000):
        self.ttl = ttl_seconds
        self.max_items = max_items
        self._items: "OrderedDict[str, float]" = OrderedDict()

    def _purge(self, now: float) -> None:
        while self._items:
            k, t = next(iter(self._items.items()))
            if now - t > self.ttl or len(self._items) > self.max_items:
                self._items.popitem(last=False)
            else:
                break

    def seen_or_add(self, key: str) -> bool:
        """已见过返回 True；否则记录并返回 False。"""
        now = time.time()
        self._purge(now)
        if key in self._items:
            return True
        self._items[key] = now
        return False

    def __len__(self) -> int:
        return len(self._items)
