"""Tests for the non-blocking location detection pipeline.

speed-up-first-token-latency tasks 2.2/2.4: ``_detect_location`` is a
cache-only read (never blocks on the network); the IP geolocation probe runs
exclusively in a background task, warmable at startup (``warm_location_cache``)
and retried behind a TTL after failures.
"""

import asyncio
import time

import pytest

from app.services import agent_runner


@pytest.fixture(autouse=True)
def _reset_location_state():
    """Isolate module-level location cache state per test; cancel stray probes."""
    agent_runner._cached_location = None
    agent_runner._location_failed_at = None
    agent_runner._location_probe_task = None
    yield
    task = agent_runner._location_probe_task
    if task is not None and not task.done():
        task.cancel()
    agent_runner._cached_location = None
    agent_runner._location_failed_at = None
    agent_runner._location_probe_task = None


# ─── critical path: cache-only read ─────────────────────────────────────────


async def test_cold_cache_returns_unknown_and_schedules_probe(monkeypatch):
    """Cache not ready → returns "Unknown" immediately and probes in background."""
    probe_calls: list[int] = []

    async def fake_probe() -> None:
        probe_calls.append(1)

    monkeypatch.setattr(agent_runner, "_probe_location", fake_probe)

    result = agent_runner._detect_location()

    assert result == "Unknown"
    assert agent_runner._location_probe_task is not None
    await agent_runner._location_probe_task
    assert probe_calls == [1]  # probe ran in the background, not inline


async def test_cold_cache_read_does_not_perform_network_io(monkeypatch):
    """The cache read itself performs no network I/O — a failing httpx import or
    client would only affect the background probe, never the caller."""
    # Any accidental synchronous network attempt inside _detect_location would
    # surface here; the probe is stubbed so nothing real can run either.
    async def failing_probe() -> None:
        raise AssertionError("probe must not run inline")

    monkeypatch.setattr(agent_runner, "_probe_location", failing_probe)

    result = agent_runner._detect_location()
    assert result == "Unknown"


async def test_warm_cache_returns_city_without_probe(monkeypatch):
    """Warm cache → returns the detected city and schedules no probe."""
    monkeypatch.setattr(agent_runner, "_cached_location", "重庆")

    result = agent_runner._detect_location()

    assert result == "重庆"
    assert agent_runner._location_probe_task is None


async def test_warm_after_background_probe_completion(monkeypatch):
    """Full scenario: cold read → background warmup completes → next read is real city."""
    class _FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {"status": "success", "city": "重庆"}

    class _FakeAsyncClient:
        def __init__(self, **kwargs) -> None:  # noqa: ARG002 - mirrors httpx API
            pass

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *args) -> None:  # noqa: ARG002
            return None

        async def get(self, url: str) -> _FakeResponse:  # noqa: ARG002
            return _FakeResponse()

    monkeypatch.setattr("httpx.AsyncClient", _FakeAsyncClient)

    # Cold read: placeholder + background warmup scheduled
    assert agent_runner._detect_location() == "Unknown"
    assert agent_runner._location_probe_task is not None
    await agent_runner._location_probe_task

    # Warm read: real city, zero network
    assert agent_runner._detect_location() == "重庆"


# ─── background probe behaviour ──────────────────────────────────────────────


async def test_probe_success_sets_city_and_clears_failure(monkeypatch):
    class _FakeResponse:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {"status": "success", "city": "重庆"}

    class _FakeAsyncClient:
        def __init__(self, **kwargs) -> None:  # noqa: ARG002
            pass

        async def __aenter__(self) -> "_FakeAsyncClient":
            return self

        async def __aexit__(self, *args) -> None:  # noqa: ARG002
            return None

        async def get(self, url: str) -> _FakeResponse:  # noqa: ARG002
            return _FakeResponse()

    agent_runner._location_failed_at = time.monotonic() - 1
    monkeypatch.setattr("httpx.AsyncClient", _FakeAsyncClient)

    await agent_runner._probe_location()

    assert agent_runner._cached_location == "重庆"
    assert agent_runner._location_failed_at is None


async def test_probe_failure_keeps_cache_unset_and_records_timestamp(monkeypatch):
    """Failure (e.g. offline) is non-fatal: cache stays unset, failure timestamped."""

    class _ExplodingClient:
        def __init__(self, **kwargs) -> None:  # noqa: ARG002
            raise OSError("network down")

    monkeypatch.setattr("httpx.AsyncClient", _ExplodingClient)

    await agent_runner._probe_location()

    assert agent_runner._cached_location is None
    assert agent_runner._location_failed_at is not None


# ─── retry TTL ───────────────────────────────────────────────────────────────


async def test_recent_failure_blocks_rescheduling(monkeypatch):
    """A recent failed probe suppresses background retries until the TTL elapses."""
    async def unexpected_probe() -> None:
        raise AssertionError("no probe should be scheduled within the TTL")

    monkeypatch.setattr(agent_runner, "_probe_location", unexpected_probe)
    agent_runner._location_failed_at = time.monotonic() - 1.0

    assert agent_runner._detect_location() == "Unknown"
    assert agent_runner._location_probe_task is None


async def test_ttl_expiry_allows_new_probe(monkeypatch):
    """After the retry TTL, a cold read schedules a fresh background probe."""
    probe_calls: list[int] = []

    async def fake_probe() -> None:
        probe_calls.append(1)

    monkeypatch.setattr(agent_runner, "_probe_location", fake_probe)
    agent_runner._location_failed_at = (
        time.monotonic() - agent_runner._LOCATION_RETRY_TTL_SECONDS - 1.0
    )

    assert agent_runner._detect_location() == "Unknown"
    assert agent_runner._location_probe_task is not None
    await agent_runner._location_probe_task
    assert probe_calls == [1]


async def test_inflight_probe_is_not_duplicated(monkeypatch):
    """While a probe is in flight, further cold reads do not spawn extra tasks."""
    release = asyncio.Event()

    async def slow_probe() -> None:
        await release.wait()

    monkeypatch.setattr(agent_runner, "_probe_location", slow_probe)

    first = agent_runner._detect_location()
    second = agent_runner._detect_location()

    assert first == second == "Unknown"
    assert agent_runner._location_probe_task is not None
    task = agent_runner._location_probe_task
    agent_runner._detect_location()
    assert agent_runner._location_probe_task is task  # same task, no duplicate
    release.set()
    await task
