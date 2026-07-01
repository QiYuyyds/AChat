"""Unit tests for TaskMemBuffer and ToolStateTracker async ring buffers."""

import asyncio

import pytest

from app.services.prompt_assembler import (
    StepObservation,
    TaskMemBuffer,
    ToolCallTrace,
    ToolStateTracker,
)


# ─── TaskMemBuffer tests (Task 2.3) ──────────────────────────────────────────


def _make_obs(step_id: str = "1", tool_name: str = "bash", success: bool = True) -> StepObservation:
    return StepObservation(
        step_id=step_id,
        tool_name=tool_name,
        result="ok" if success else "",
        error="" if success else "failed",
        success=success,
    )


def test_task_mem_push_beyond_max_discards_oldest():
    """Pushing 25 entries into a max_size=20 buffer keeps only the last 20."""
    buf = TaskMemBuffer(max_size=20)
    for i in range(25):
        asyncio.run(buf.push(_make_obs(step_id=str(i))))
    snap = asyncio.run(buf.snapshot())
    assert len(snap) == 20
    # First 5 (step_id 0..4) should be discarded
    step_ids = [s.step_id for s in snap]
    assert step_ids[0] == "5"
    assert step_ids[-1] == "24"


def test_task_mem_reset_clears_all():
    """reset() empties the buffer."""
    buf = TaskMemBuffer(max_size=20)
    for i in range(15):
        asyncio.run(buf.push(_make_obs(step_id=str(i))))
    assert len(asyncio.run(buf.snapshot())) == 15
    asyncio.run(buf.reset())
    assert asyncio.run(buf.snapshot()) == []


def test_task_mem_snapshot_returns_copy():
    """snapshot() returns a copy; mutating it doesn't affect the buffer."""
    buf = TaskMemBuffer(max_size=20)
    asyncio.run(buf.push(_make_obs(step_id="1")))
    snap = asyncio.run(buf.snapshot())
    snap.clear()
    # Buffer should still have the entry
    assert len(asyncio.run(buf.snapshot())) == 1


def test_task_mem_empty_buffer_snapshot():
    """Empty buffer returns empty list."""
    buf = TaskMemBuffer()
    assert asyncio.run(buf.snapshot()) == []


# ─── ToolStateTracker tests (Task 3.3) ────────────────────────────────────────


def test_tool_state_record_truncates_long_summary():
    """Summaries over 120 chars are truncated to 120 + '…'."""
    tracker = ToolStateTracker(max_size=10)
    long_summary = "x" * 200
    trace = ToolCallTrace(tool_name="bash", success=True, summary=long_summary)
    asyncio.run(tracker.record(trace))
    snap = asyncio.run(tracker.snapshot())
    assert len(snap) == 1
    assert len(snap[0].summary) == 121  # 120 chars + "…"
    assert snap[0].summary.endswith("…")


def test_tool_state_record_short_summary_unchanged():
    """Short summaries are not truncated."""
    tracker = ToolStateTracker(max_size=10)
    short_summary = "command output"
    trace = ToolCallTrace(tool_name="bash", success=True, summary=short_summary)
    asyncio.run(tracker.record(trace))
    snap = asyncio.run(tracker.snapshot())
    assert snap[0].summary == short_summary


def test_tool_state_exceed_max_discards_oldest():
    """Exceeding max_size discards the oldest entries."""
    tracker = ToolStateTracker(max_size=3)
    for i in range(5):
        asyncio.run(tracker.record(ToolCallTrace(tool_name=f"tool_{i}", summary=f"trace_{i}")))
    snap = asyncio.run(tracker.snapshot())
    assert len(snap) == 3
    # Oldest two (tool_0, tool_1) should be discarded
    names = [t.tool_name for t in snap]
    assert names == ["tool_2", "tool_3", "tool_4"]


def test_tool_state_snapshot_returns_copy():
    """snapshot() returns a copy."""
    tracker = ToolStateTracker(max_size=10)
    asyncio.run(tracker.record(ToolCallTrace(tool_name="bash", summary="ok")))
    snap = asyncio.run(tracker.snapshot())
    snap.clear()
    assert len(asyncio.run(tracker.snapshot())) == 1


def test_tool_state_empty_snapshot():
    """Empty tracker returns empty list."""
    tracker = ToolStateTracker()
    assert asyncio.run(tracker.snapshot()) == []
