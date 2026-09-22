"""会话：以 群+人 为键记住"上一条需求"，这样同事可以直接在聊天里回答问清的问题并重算。"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

__all__ = ["Session", "SessionStore"]


@dataclass
class Session:
    key: str
    raw_text: str = ""                                     # 上一条被当作需求的原话
    answers: Dict[str, str] = field(default_factory=dict)  # 问题 id -> 业务答复
    numbered: List[Tuple[int, str]] = field(default_factory=list)  # (序号, 问题 id)，把"1 xxx"映射回 id
    analysis: Optional[Any] = None                         # 最近一次 Analysis，供重算
    mode: str = ""                                         # "" 跟随默认 | req 需求分析 | ask 问码
    ask_session_id: Optional[str] = None                   # 问码的 Agent SDK 会话 id，用于追问
    turns: int = 0
    last_active: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.last_active = time.time()

    def start(self, raw_text: str) -> None:
        """收到一条新需求：清掉旧答复，重新开始。"""
        self.raw_text = raw_text
        self.answers.clear()
        self.numbered.clear()
        self.analysis = None
        self.turns = 0
        self.touch()

    def remember_questions(self, questions: List[Any]) -> None:
        self.numbered = [(i + 1, q.id) for i, q in enumerate(questions)]
        self.touch()

    def qid_for(self, number: int) -> Optional[str]:
        for n, qid in self.numbered:
            if n == number:
                return qid
        return None

    @property
    def has_requirement(self) -> bool:
        return bool(self.raw_text)


class SessionStore:
    def __init__(self, ttl_minutes: int = 120, max_sessions: int = 500) -> None:
        self.ttl = ttl_minutes * 60
        self.max_sessions = max_sessions
        self._store: Dict[str, Session] = {}

    def get(self, key: str) -> Session:
        self._cleanup()
        s = self._store.get(key)
        if s is None:
            s = Session(key=key)
            self._store[key] = s
        return s

    def reset(self, key: str) -> None:
        self._store.pop(key, None)

    def _cleanup(self) -> None:
        now = time.time()
        for k in [k for k, s in self._store.items() if now - s.last_active > self.ttl]:
            del self._store[k]
        if len(self._store) > self.max_sessions:
            oldest = sorted(self._store, key=lambda k: self._store[k].last_active)
            for k in oldest[: len(self._store) - self.max_sessions]:
                del self._store[k]

    def __len__(self) -> int:
        return len(self._store)
