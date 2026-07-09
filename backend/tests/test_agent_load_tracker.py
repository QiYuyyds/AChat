"""Unit tests for AgentLoadTracker.

Tests:
- AgentLoadTracker acquire/release/get_load basic operations
- Process restart degradation (empty tracker)
- record_duration rolling average
"""

from __future__ import annotations

import pytest

from app.services.agent_load_tracker import AgentLoadTracker, agent_load_tracker


@pytest.fixture(autouse=True)
def _reset_tracker():
    """Reset the singleton before and after each test."""
    agent_load_tracker.reset()
    yield
    agent_load_tracker.reset()


# ─── AgentLoadTracker basic operations ────────────────────────────────────────


def test_acquire_increments_count():
    tracker = AgentLoadTracker()
    assert tracker.acquire("ag_a") == 1
    assert tracker.acquire("ag_a") == 2
    assert tracker.acquire("ag_a") == 3


def test_release_decrements_count():
    tracker = AgentLoadTracker()
    tracker.acquire("ag_a")
    tracker.acquire("ag_a")
    assert tracker.release("ag_a") == 1
    assert tracker.release("ag_a") == 0


def test_release_below_zero_stays_zero():
    tracker = AgentLoadTracker()
    assert tracker.release("ag_a") == 0
    tracker.acquire("ag_a")
    tracker.release("ag_a")
    assert tracker.release("ag_a") == 0


def test_get_load_returns_zero_for_untracked():
    tracker = AgentLoadTracker()
    assert tracker.get_load("ag_unknown") == 0


def test_get_load_returns_current_count():
    tracker = AgentLoadTracker()
    tracker.acquire("ag_a")
    tracker.acquire("ag_a")
    assert tracker.get_load("ag_a") == 2


def test_release_cleans_up_zero_entries():
    tracker = AgentLoadTracker()
    tracker.acquire("ag_a")
    tracker.release("ag_a")
    # After release to 0, the agent should not be in _current
    assert "ag_a" not in tracker._current


# ─── Process restart degradation ──────────────────────────────────────────────def test_reset_clears_all_state():
    """reset() clears current_tasks and avg_duration_ms."""
    tracker = AgentLoadTracker()
    tracker.acquire("ag_a")
    tracker.record_duration("ag_a", 1000.0)
    tracker.reset()
    assert tracker.get_load("ag_a") == 0
    assert tracker.get_avg_duration_ms("ag_a") == 0.0


# ─── record_duration ──────────────────────────────────────────────────────────


def test_record_duration_first_value():
    tracker = AgentLoadTracker()
    tracker.record_duration("ag_a", 5000.0)
    assert tracker.get_avg_duration_ms("ag_a") == 5000.0


def test_record_duration_rolling_average():
    tracker = AgentLoadTracker()
    tracker.record_duration("ag_a", 10000.0)
    tracker.record_duration("ag_a", 5000.0)
    # 10000 * 0.8 + 5000 * 0.2 = 9000.0
    assert tracker.get_avg_duration_ms("ag_a") == 9000.0


def test_get_avg_duration_ms_unknown_agent():
    tracker = AgentLoadTracker()
    assert tracker.get_avg_duration_ms("ag_unknown") == 0.0


# ─── snapshot ─────────────────────────────────────────────────────────────────


def test_snapshot_returns_all_agents():
    tracker = AgentLoadTracker()
    tracker.acquire("ag_a")
    tracker.acquire("ag_b")
    tracker.record_duration("ag_c", 3000.0)
    snap = tracker.snapshot()
    assert "ag_a" in snap
    assert "ag_b" in snap
    assert "ag_c" in snap
    assert snap["ag_a"].current_tasks == 1
    assert snap["ag_b"].current_tasks == 1
    assert snap["ag_c"].current_tasks == 0
    assert snap["ag_c"].avg_duration_ms == 3000.0
