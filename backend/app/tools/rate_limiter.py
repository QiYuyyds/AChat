"""Simple in-memory rate limiter with TTL for per-run throttling.

Used by ``memory_store`` to cap writes at 3 per agent run. Not persisted —
process restart resets all counters, which is acceptable for runtime throttling.
"""

from __future__ import annotations

import asyncio
import time


class SimpleRateLimiter:
    """In-memory rate limiter using dict with TTL."""

    def __init__(self) -> None:
        self._counts: dict[str, tuple[int, float]] = {}
        self._lock = asyncio.Lock()

    async def incr(self, key: str, ttl: int = 300) -> int:
        """Increment counter for *key* and return the new count.

        If the key has expired (past *ttl* seconds since first increment),
        the counter resets to 1.
        """
        async with self._lock:
            now = time.time()
            count, expires = self._counts.get(key, (0, now + ttl))
            if now > expires:
                count, expires = 0, now + ttl
            count += 1
            self._counts[key] = (count, expires)
            return count

    async def get(self, key: str) -> int:
        """Return current count for *key* (0 if missing or expired)."""
        async with self._lock:
            now = time.time()
            count, expires = self._counts.get(key, (0, 0))
            if now > expires:
                return 0
            return count


# Module-level singleton — shared across all memory_store invocations.
_rate_limiter = SimpleRateLimiter()
