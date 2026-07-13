"""Tests for the Redis KV metadata cache layer.

Covers: cache hit, cache miss, invalidation, Redis unavailable degradation.
"""

import pytest

from app.infra.cache import MetadataCache


class FakeRedis:
    """Minimal async Redis mock for cache tests."""

    def __init__(self):
        self._store: dict[str, str] = {}
        self._broken = False

    async def get(self, key):
        if self._broken:
            raise ConnectionError("redis broken")
        return self._store.get(key)

    async def setex(self, key, ttl, value):
        if self._broken:
            raise ConnectionError("redis broken")
        self._store[key] = value

    async def delete(self, key):
        if self._broken:
            raise ConnectionError("redis broken")
        self._store.pop(key, None)

    async def aclose(self):
        pass


@pytest.mark.asyncio
async def test_cache_miss_then_hit():
    """On first get, loader is called; on second get, cached value is returned."""
    redis = FakeRedis()
    cache = MetadataCache(redis)
    call_count = 0

    async def loader():
        nonlocal call_count
        call_count += 1
        return {"name": "alice"}

    result1 = await cache.get_or_load("agent:1", 300, loader)
    assert result1 == {"name": "alice"}
    assert call_count == 1

    result2 = await cache.get_or_load("agent:1", 300, loader)
    assert result2 == {"name": "alice"}
    assert call_count == 1  # loader not called again


@pytest.mark.asyncio
async def test_cache_invalidation():
    """After delete, the next get_or_load calls the loader again."""
    redis = FakeRedis()
    cache = MetadataCache(redis)
    call_count = 0

    async def loader():
        nonlocal call_count
        call_count += 1
        return {"name": "bob"}

    await cache.get_or_load("agent:2", 300, loader)
    assert call_count == 1

    await cache.delete("agent:2")

    await cache.get_or_load("agent:2", 300, loader)
    assert call_count == 2  # loader called again after invalidation


@pytest.mark.asyncio
async def test_redis_unavailable_degradation():
    """When redis_client is None, get_or_load always calls the loader."""
    cache = MetadataCache(None)
    call_count = 0

    async def loader():
        nonlocal call_count
        call_count += 1
        return {"name": "charlie"}

    result1 = await cache.get_or_load("agent:3", 300, loader)
    assert result1 == {"name": "charlie"}
    assert call_count == 1

    result2 = await cache.get_or_load("agent:3", 300, loader)
    assert result2 == {"name": "charlie"}
    assert call_count == 2  # loader called every time (no cache)

    # delete is a no-op
    await cache.delete("agent:3")


@pytest.mark.asyncio
async def test_redis_error_falls_through():
    """When Redis raises an error, get falls through to loader."""
    redis = FakeRedis()
    redis._broken = True
    cache = MetadataCache(redis)

    async def loader():
        return {"name": "dave"}

    result = await cache.get_or_load("agent:4", 300, loader)
    assert result == {"name": "dave"}


@pytest.mark.asyncio
async def test_cache_set_and_get():
    """Direct set then get returns the stored value."""
    redis = FakeRedis()
    cache = MetadataCache(redis)

    await cache.set("key:1", {"data": [1, 2, 3]}, 60)
    result = await cache.get("key:1")
    assert result == {"data": [1, 2, 3]}


@pytest.mark.asyncio
async def test_cache_get_miss_returns_none():
    """Getting a non-existent key returns None."""
    redis = FakeRedis()
    cache = MetadataCache(redis)

    result = await cache.get("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_cache_available_property():
    """The 'available' property reflects whether redis_client is set."""
    assert MetadataCache(None).available is False
    assert MetadataCache(FakeRedis()).available is True
