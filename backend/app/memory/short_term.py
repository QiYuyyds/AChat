"""Short-term memory — sliding window of recent conversation turns.

Ported from AGI-memory ``internal/memory/memory.py`` ShortTerm class.
Zero modification: pure in-memory deque + RLock, no DB I/O.
"""

import threading
import time
from collections import deque
from typing import Deque, Dict, List


class ShortTerm:
    """Sliding-window short-term memory storing the most recent N turns.

    Each entry is a ``{"role", "content", "timestamp"}`` dict. The underlying
    ``deque(maxlen=max_turns*2)`` automatically evicts the oldest entries once
    the capacity is reached. Thread-safe via RLock.
    """

    def __init__(self, max_turns: int = 10):
        self.max_turns = max(1, max_turns)
        self.messages: Deque[Dict[str, str]] = deque(maxlen=self.max_turns * 2)
        self._lock = threading.RLock()

    def add(self, role: str, content: str) -> None:
        ts = time.strftime("%H:%M:%S", time.localtime())
        with self._lock:
            self.messages.append({"role": role, "content": content, "timestamp": ts})

    def get(self) -> List[Dict[str, str]]:
        with self._lock:
            return [dict(m) for m in self.messages]

    def clear(self) -> None:
        with self._lock:
            self.messages.clear()

    def count(self) -> int:
        with self._lock:
            return len(self.messages)
