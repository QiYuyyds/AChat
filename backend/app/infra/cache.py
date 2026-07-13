"""Redis KV metadata cache for low-churn entities.

Read-through cache with write-invalidation: check Redis first; on miss,
call the loader function and backfill Redis with TTL. On write, DEL the key.
All methods are no-ops when Redis is unavailable (graceful degradation).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)


class MetadataCache:
    """Wraps Redis KV operations with graceful degradation."""

    def __init__(self, redis_client: Any | None = None) -> None:
        self._redis = redis_client

    @property
    def available(self) -> bool:
        return self._redis is not None

    async def get(self, key: str) -> Any | None:
        """Return cached JSON value, or None on miss / Redis unavailable."""
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except Exception as e:
            logger.warning("[cache] get(%s) failed: %s", key, e)
            return None

    async def set(self, key: str, value: Any, ttl: int) -> None:
        """Store value as JSON with TTL seconds."""
        if self._redis is None:
            return
        try:
            await self._redis.setex(key, ttl, json.dumps(value, default=str))
        except Exception as e:
            logger.warning("[cache] set(%s) failed: %s", key, e)

    async def delete(self, key: str) -> None:
        """Remove a key from cache (write-invalidation)."""
        if self._redis is None:
            return
        try:
            await self._redis.delete(key)
        except Exception as e:
            logger.warning("[cache] delete(%s) failed: %s", key, e)

    async def get_or_load(
        self,
        key: str,
        ttl: int,
        loader_fn: Callable[[], Awaitable[Any]],
    ) -> Any:
        """Check Redis → on miss call loader_fn → backfill Redis → return value.

        On Redis error, fall through to loader_fn directly.
        """
        if self._redis is not None:
            cached = await self.get(key)
            if cached is not None:
                return cached
        value = await loader_fn()
        if value is not None and self._redis is not None:
            await self.set(key, value, ttl)
        return value


_cache_instance: MetadataCache | None = None


def get_cache() -> MetadataCache:
    """Return the singleton MetadataCache (may be a no-op if Redis is None)."""
    global _cache_instance
    if _cache_instance is None:
        from app.infra.factory import get_infrastructure

        infra = get_infrastructure()
        redis_client = infra.redis_client if infra else None
        _cache_instance = MetadataCache(redis_client)
    return _cache_instance


def init_cache(redis_client: Any | None) -> None:
    """Initialize the cache singleton with a Redis client (called at startup)."""
    global _cache_instance
    _cache_instance = MetadataCache(redis_client)
