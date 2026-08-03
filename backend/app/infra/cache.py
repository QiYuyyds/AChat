"""Metadata cache — removed in dual-DB migration.

Redis KV cache has been replaced by:
- Agent/Workspace: direct local SQLite read (0.1ms)
- UserSettings/GlobalSettings: process-internal dict TTL cache (see cache_helpers.py)

This module is kept as a stub for backward-compat imports (get_cache / init_cache).
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class MetadataCache:
    """No-op cache stub for backward compatibility."""

    def __init__(self, redis_client: Any | None = None) -> None:
        self._redis = None  # Redis removed

    @property
    def available(self) -> bool:
        return False

    async def get(self, key: str) -> Any | None:
        return None

    async def set(self, key: str, value: Any, ttl: int) -> None:
        pass

    async def delete(self, key: str) -> None:
        pass

    async def get_or_load(
        self,
        key: str,
        ttl: int,
        loader_fn: Any,
    ) -> Any:
        """Fall through to loader_fn directly (no cache)."""
        return await loader_fn()


_cache_instance: MetadataCache | None = None


def get_cache() -> MetadataCache:
    """Return the singleton MetadataCache (no-op stub)."""
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = MetadataCache()
    return _cache_instance


def init_cache(redis_client: Any | None = None) -> None:
    """No-op: Redis cache removed. Kept for backward-compat."""
    global _cache_instance
    _cache_instance = MetadataCache()
