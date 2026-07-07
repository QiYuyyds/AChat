"""Unit tests for AgentLoadTracker and load-aware dispatch (P2 O7).

Tests:
- AgentLoadTracker acquire/release/get_load basic operations
- _partition_by_load multi-agent greedy allocation
- Single-agent relaxation
- Process restart degradation (empty tracker)
- record_duration rolling average
"""

from __future__ import annotations

import pytest

from app.schemas.dispatch import DispatchPlanItem
from app.services.agent_load_tracker import AgentLoadTracker, agent_load_tracker
from app.services.orchestrator import _partition_by_load


@pytest.fixture(autouse=True)
def _reset_tracker():
    """Reset the singleton before and after each test."""
    agent_load_tracker.reset()
    yield
    agent_load_tracker.reset()


def _make_task(task_id: str, agent_id: str, task_kind: str = "code") -> DispatchPlanItem:
    return DispatchPlanItem(
        id=task_id,
        agent_id=agent_id,
        task=f"do {task_id}",
        task_kind=task_kind,
    )


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


# ─── _partition_by_load: multi-agent greedy allocation ────────────────────────


def test_multi_agent_greedy_allocation():
    """3 tasks for agent A, 1 for agent B, limit=2 → A gets 2, B gets 1, 1 deferred."""
    tasks = [
        _make_task("t1", "ag_a", "code"),
        _make_task("t2", "ag_a", "code"),
        _make_task("t3", "ag_a", "code"),
        _make_task("t4", "ag_b", "code"),
    ]
    wave, deferred = _partition_by_load(tasks, max_per_agent=2)
    deferred_ids = {t.id for t in deferred}

    # Agent A: 3 tasks, limit 2 → 2 in wave, 1 deferred
    a_in_wave = [t for t in wave if t.agent_id == "ag_a"]
    assert len(a_in_wave) == 2
    # Agent B: 1 task → 1 in wave
    b_in_wave = [t for t in wave if t.agent_id == "ag_b"]
    assert len(b_in_wave) == 1
    # 1 deferred (the 3rd task for agent A)
    assert len(deferred) == 1
    assert deferred[0].agent_id == "ag_a"
    # The deferred task should be t3 (last in priority order for agent A)
    assert "t3" in deferred_ids


def test_multi_agent_balanced_distribution():
    """2 tasks each for agents A and B, limit=2 → all in wave, none deferred."""
    tasks = [
        _make_task("t1", "ag_a"),
        _make_task("t2", "ag_a"),
        _make_task("t3", "ag_b"),
        _make_task("t4", "ag_b"),
    ]
    wave, deferred = _partition_by_load(tasks, max_per_agent=2)
    assert len(wave) == 4
    assert len(deferred) == 0


def test_load_tracker_acquires_during_partition():
    """_partition_by_load acquires load for wave tasks."""
    tasks = [
        _make_task("t1", "ag_a"),
        _make_task("t2", "ag_b"),
    ]
    wave, deferred = _partition_by_load(tasks, max_per_agent=2)
    assert len(wave) == 2
    assert agent_load_tracker.get_load("ag_a") == 1
    assert agent_load_tracker.get_load("ag_b") == 1
    # Clean up
    agent_load_tracker.release("ag_a")
    agent_load_tracker.release("ag_b")


def test_existing_load_affects_allocation():
    """Pre-existing load on agent A causes tasks to defer."""
    agent_load_tracker.acquire("ag_a")
    agent_load_tracker.acquire("ag_a")  # A is now at load 2

    tasks = [
        _make_task("t1", "ag_a"),
        _make_task("t2", "ag_b"),
    ]
    wave, deferred = _partition_by_load(tasks, max_per_agent=2)
    # A is at load 2 (>= limit), so t1 should be deferred
    assert "t1" in {t.id for t in deferred}
    assert "t2" in {t.id for t in wave}
    # Clean up
    agent_load_tracker.release("ag_a")
    agent_load_tracker.release("ag_a")
    agent_load_tracker.release("ag_b")


# ─── _partition_by_load: single-agent relaxation ──────────────────────────────


def test_single_agent_relaxes_limit():
    """All tasks for same agent → limit relaxed, all in wave."""
    tasks = [
        _make_task("t1", "ag_a"),
        _make_task("t2", "ag_a"),
        _make_task("t3", "ag_a"),
        _make_task("t4", "ag_a"),
        _make_task("t5", "ag_a"),
    ]
    wave, deferred = _partition_by_load(tasks, max_per_agent=2)
    assert len(wave) == 5
    assert len(deferred) == 0
    # Clean up
    for _ in range(5):
        agent_load_tracker.release("ag_a")


def test_single_agent_with_pre_existing_load_relaxes():
    """Single agent with pre-existing load still gets all tasks (relaxed)."""
    agent_load_tracker.acquire("ag_a")
    agent_load_tracker.acquire("ag_a")

    tasks = [
        _make_task("t1", "ag_a"),
        _make_task("t2", "ag_a"),
    ]
    wave, deferred = _partition_by_load(tasks, max_per_agent=2)
    assert len(wave) == 2
    assert len(deferred) == 0
    # Clean up
    for _ in range(4):
        agent_load_tracker.release("ag_a")


# ─── Process restart degradation ──────────────────────────────────────────────


def test_empty_tracker_allows_all_tasks():
    """Fresh tracker (process restart) → all tasks dispatched (static sorting)."""
    tasks = [
        _make_task("t1", "ag_a"),
        _make_task("t2", "ag_a"),
        _make_task("t3", "ag_a"),
        _make_task("t4", "ag_b"),
        _make_task("t5", "ag_b"),
    ]
    # Fresh tracker: all loads are 0
    wave, deferred = _partition_by_load(tasks, max_per_agent=2)
    # Agent A: 3 tasks, limit 2 → 2 in wave, 1 deferred (multi-agent, not relaxed)
    # Agent B: 2 tasks, limit 2 → 2 in wave
    assert len(wave) == 4
    assert len(deferred) == 1
    assert deferred[0].agent_id == "ag_a"
    # Clean up
    for t in wave:
        agent_load_tracker.release(t.agent_id)


def test_reset_clears_all_state():
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
