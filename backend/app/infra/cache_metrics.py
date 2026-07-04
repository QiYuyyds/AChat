"""Cache metrics aggregator — sliding-window prompt cache hit rate monitoring.

Tracks per-call cache_read_tokens, cache_creation_tokens, and input_tokens
over a rolling window. Provides aggregate hit rate and alerting for detecting
cache performance degradation.

Integration point: CustomAdapter calls ``cache_metrics.record()`` after each
LLM usage chunk is processed (see custom_adapter.py).
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Sliding window size for recent LLM calls.
WINDOW_SIZE = 20
# Hit rate threshold below which we alert.
ALERT_THRESHOLD = 0.5


@dataclass
class _CacheRecord:
    """A single LLM call's cache metrics."""

    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    input_tokens: int = 0


class CacheMetrics:
    """Sliding-window aggregator for prompt cache hit rates.

    Tracks cache_read_tokens, cache_creation_tokens, and input_tokens per call
    over a rolling window. Provides aggregate hit rate and alerting.

    Thread-safe via a threading.Lock (fast enough for the record() hot path;
    avoids asyncio.Lock since record() is called synchronously from the stream
    processing loop).
    """

    def __init__(
        self,
        window_size: int = WINDOW_SIZE,
        alert_threshold: float = ALERT_THRESHOLD,
    ) -> None:
        self._window: deque[_CacheRecord] = deque(maxlen=window_size)
        self._lock = threading.Lock()
        self._alert_threshold = alert_threshold

    def record(
        self,
        cache_read: int = 0,
        cache_creation: int = 0,
        input_tokens: int = 0,
    ) -> None:
        """Record a single LLM call's cache metrics.

        Args:
            cache_read: Tokens served from cache (cache hit).
            cache_creation: Tokens written to cache (cache miss that creates cache).
            input_tokens: Total input tokens for this call.
        """
        with self._lock:
            self._window.append(
                _CacheRecord(
                    cache_read_tokens=cache_read,
                    cache_creation_tokens=cache_creation,
                    input_tokens=input_tokens,
                )
            )

    def recent_hit_rate(self) -> float:
        """Average cache hit rate over the recent window.

        Hit rate = sum(cache_read_tokens) / sum(input_tokens).
        Returns 0.0 when no data or total input is zero.
        """
        with self._lock:
            if not self._window:
                return 0.0
            total_read = sum(r.cache_read_tokens for r in self._window)
            total_input = sum(r.input_tokens for r in self._window)
            if total_input == 0:
                return 0.0
            return total_read / total_input

    def should_alert(self) -> bool:
        """True when recent hit rate is below the alert threshold.

        Returns False when:
        - the window is empty (no data to alert on)
        - fewer than 2 records (first call always has 0% hit rate — cache
          is created on first call, not before)
        - all input_tokens are zero (no meaningful data)
        """
        with self._lock:
            if not self._window:
                return False
            if len(self._window) < 2:
                return False  # first call is always a cache miss
            # Don't alert if all input_tokens are zero (no meaningful data).
            total_input = sum(r.input_tokens for r in self._window)
            if total_input == 0:
                return False
        return self.recent_hit_rate() < self._alert_threshold

    @property
    def recent_count(self) -> int:
        """Number of records in the current window."""
        with self._lock:
            return len(self._window)


# Global singleton instance.
cache_metrics = CacheMetrics()
