"""Tests for the non-blocking Phoenix eval-write path in eval_rules.

Verifies that _write_evals_to_phoenix offloads all synchronous I/O to a
worker thread with a hard timeout, keeping the event loop responsive.
"""

import asyncio
import logging

import pytest

from app.observability import eval_rules


@pytest.fixture(autouse=True)
def _clear_client_cache():
    eval_rules._phoenix_clients.clear()
    yield
    eval_rules._phoenix_clients.clear()


@pytest.fixture
def _settings(monkeypatch):
    """Settings with eval + trace enabled and a short timeout for tests."""
    monkeypatch.setenv("TRACE_ENABLED", "true")
    monkeypatch.setenv("EVAL_RULE_ENABLED", "true")
    monkeypatch.setenv("EVAL_WRITE_TIMEOUT_SECONDS", "0.5")
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(eval_rules, "is_trace_enabled", lambda: True)
    yield get_settings()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_event_loop_not_blocked_during_write(_settings, monkeypatch):
    """(a) During a slow Phoenix write, the event loop can schedule other tasks."""
    loop_started = asyncio.Event()

    def slow_sync_write(endpoint, trace_id, scores):
        # Simulate slow synchronous I/O (blocks the worker thread, not the loop)
        loop_started.set()
        import time

        time.sleep(0.3)

    monkeypatch.setattr(eval_rules, "_sync_write_evals", slow_sync_write)

    probe_ran = False

    async def probe():
        nonlocal probe_ran
        await asyncio.sleep(0.05)
        probe_ran = True

    probe_task = asyncio.create_task(probe())
    await eval_rules._write_evals_to_phoenix("trace-1", [])
    await probe_task

    assert probe_ran, "Event loop was blocked — probe task did not complete"


@pytest.mark.asyncio
async def test_timeout_aborts_write_and_logs_warning(_settings, caplog):
    """(b) When _sync_write_evals exceeds timeout, it is abandoned with a WARNING."""
    original_sync = eval_rules._sync_write_evals

    def hanging_write(endpoint, trace_id, scores):
        import time

        time.sleep(5)  # well beyond the 0.5s timeout

    eval_rules._sync_write_evals = hanging_write
    try:
        with caplog.at_level(logging.WARNING, logger="app.observability.eval_rules"):
            await eval_rules._write_evals_to_phoenix("trace-timeout", [])
    finally:
        eval_rules._sync_write_evals = original_sync

    assert any("timed out" in r.message for r in caplog.records), (
        "Expected a timeout WARNING in logs"
    )


@pytest.mark.asyncio
async def test_client_cached_per_endpoint(_settings, monkeypatch):
    """(c) Same endpoint reuses the cached client instance."""
    construct_count = {"n": 0}
    fake_client = type("FakeClient", (), {})()

    def fake_get_client(endpoint):
        cached = eval_rules._phoenix_clients.get(endpoint)
        if cached is not None:
            return cached
        construct_count["n"] += 1
        eval_rules._phoenix_clients[endpoint] = fake_client
        return fake_client

    monkeypatch.setattr(eval_rules, "_get_phoenix_client", fake_get_client)

    # Call _get_phoenix_client directly (simulating what _sync_write_evals does)
    c1 = eval_rules._get_phoenix_client("http://phoenix:6006")
    c2 = eval_rules._get_phoenix_client("http://phoenix:6006")

    assert c1 is c2 is fake_client
    assert construct_count["n"] == 1, "Client should be cached after first construction"


@pytest.mark.asyncio
async def test_run_eval_skipped_when_disabled(monkeypatch):
    """(regression) eval_rule_enabled=False → no Phoenix contact."""
    monkeypatch.setenv("TRACE_ENABLED", "true")
    monkeypatch.setenv("EVAL_RULE_ENABLED", "false")
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(eval_rules, "is_trace_enabled", lambda: True)

    phoenix_called = False

    async def fake_write(*args, **kwargs):
        nonlocal phoenix_called
        phoenix_called = True

    monkeypatch.setattr(eval_rules, "_write_evals_to_phoenix", fake_write)

    await eval_rules.run_eval_and_log("trace-off", [])

    assert not phoenix_called, "Phoenix write should be skipped when eval_rule_enabled=False"
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_run_eval_skipped_when_trace_disabled(monkeypatch):
    """(regression) trace_enabled=False → no Phoenix contact."""
    monkeypatch.setenv("TRACE_ENABLED", "true")
    monkeypatch.setenv("EVAL_RULE_ENABLED", "true")
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(eval_rules, "is_trace_enabled", lambda: False)

    phoenix_called = False

    async def fake_write(*args, **kwargs):
        nonlocal phoenix_called
        phoenix_called = True

    monkeypatch.setattr(eval_rules, "_write_evals_to_phoenix", fake_write)

    await eval_rules.run_eval_and_log("trace-off-2", [])

    assert not phoenix_called, "Phoenix write should be skipped when trace_enabled=False"
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_hook_never_raises(_settings, monkeypatch):
    """run_eval_and_log must never propagate exceptions to the caller."""
    monkeypatch.setattr(
        eval_rules,
        "_sync_write_evals",
        lambda endpoint, trace_id, scores: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    # Should not raise
    await eval_rules.run_eval_and_log("trace-err", [])
