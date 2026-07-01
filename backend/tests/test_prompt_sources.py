"""Unit tests for PlannerSource, TaskMemSource, ToolStateSource, ProfileSource."""

import asyncio
import time

import pytest

from app.memory.consolidation import Item
from app.services.prompt_assembler import (
    ContextItem,
    PlannerSnapshot,
    PlannerSource,
    ProfileSource,
    Query,
    Slot,
    SlotFilter,
    SlotPlanner,
    SlotProfile,
    SlotTaskMem,
    SlotToolState,
    StepObservation,
    TaskMemBuffer,
    TaskMemSource,
    ToolCallTrace,
    ToolStateSource,
    ToolStateTracker,
)


# ─── PlannerSource tests (Task 5.4) ───────────────────────────────────────────


def test_planner_source_active_plan():
    """PlannerSource with a provider returning a running snapshot emits status items."""
    snap = PlannerSnapshot(
        task_id="task_1",
        status="running",
        phase="executing",
        total_steps=5,
        current_step=2,
        next_step_name="实现接口",
        next_step_tool="bash",
    )
    src = PlannerSource(provider=lambda: snap)
    slot = Slot(kind=SlotPlanner, filter=SlotFilter(top_k=5))
    items = asyncio.run(src.fetch(slot, Query()))
    assert len(items) >= 2
    assert any("task_1" in i.text and "running" in i.text for i in items)
    assert any("3/5" in i.text for i in items)
    assert any("实现接口" in i.text and "bash" in i.text for i in items)


def test_planner_source_no_plan():
    """Provider returning None yields empty list."""
    src = PlannerSource(provider=lambda: None)
    slot = Slot(kind=SlotPlanner, filter=SlotFilter())
    items = asyncio.run(src.fetch(slot, Query()))
    assert items == []


def test_planner_source_no_provider():
    """No provider set yields empty list."""
    src = PlannerSource(provider=None)
    slot = Slot(kind=SlotPlanner, filter=SlotFilter())
    items = asyncio.run(src.fetch(slot, Query()))
    assert items == []


def test_planner_source_interrupted():
    """Interrupted snapshot includes recovery hint."""
    snap = PlannerSnapshot(
        task_id="task_2",
        status="interrupted",
        phase="executing",
        total_steps=3,
        current_step=1,
        interrupted_at="step 2 failed",
    )
    src = PlannerSource(provider=lambda: snap)
    slot = Slot(kind=SlotPlanner, filter=SlotFilter(top_k=5))
    items = asyncio.run(src.fetch(slot, Query()))
    assert any("中断恢复" in i.text for i in items)


# ─── TaskMemSource tests (Task 5.4) ───────────────────────────────────────────


def test_task_mem_source_with_observations():
    """Buffer with 3 observations yields 3 ContextItems."""
    buf = TaskMemBuffer(max_size=20)
    asyncio.run(buf.push(StepObservation(step_id="1", tool_name="fs_read", result="content", success=True)))
    asyncio.run(buf.push(StepObservation(step_id="2", tool_name="fs_write", result="ok", success=True)))
    asyncio.run(buf.push(StepObservation(step_id="3", tool_name="bash", error="timeout", success=False)))

    src = TaskMemSource(buffer=buf)
    slot = Slot(kind=SlotTaskMem, filter=SlotFilter(top_k=5))
    items = asyncio.run(src.fetch(slot, Query()))
    assert len(items) == 3
    assert any("步骤1" in i.text and "fs_read" in i.text for i in items)
    assert any("步骤3" in i.text and "失败" in i.text for i in items)


def test_task_mem_source_empty_buffer():
    """Empty buffer yields empty list."""
    buf = TaskMemBuffer()
    src = TaskMemSource(buffer=buf)
    slot = Slot(kind=SlotTaskMem, filter=SlotFilter())
    items = asyncio.run(src.fetch(slot, Query()))
    assert items == []


def test_task_mem_source_no_buffer():
    """No buffer set yields empty list."""
    src = TaskMemSource(buffer=None)
    slot = Slot(kind=SlotTaskMem, filter=SlotFilter())
    items = asyncio.run(src.fetch(slot, Query()))
    assert items == []


def test_task_mem_source_top_k_truncation():
    """top_k truncation keeps the most recent observations."""
    buf = TaskMemBuffer(max_size=20)
    for i in range(10):
        asyncio.run(buf.push(StepObservation(step_id=str(i), tool_name="bash", result=f"r{i}", success=True)))
    src = TaskMemSource(buffer=buf)
    slot = Slot(kind=SlotTaskMem, filter=SlotFilter(top_k=5))
    items = asyncio.run(src.fetch(slot, Query()))
    assert len(items) == 5
    # Most recent 5 (step 5..9)
    assert "步骤5" in items[0].text
    assert "步骤9" in items[-1].text


# ─── ToolStateSource tests (Task 5.4) ─────────────────────────────────────────


class _MockTool:
    def __init__(self, name, desc):
        self.name = name
        self.description = desc


def test_tool_state_source_registry_and_tracker():
    """Both registry and tracker produce ContextItems."""
    registry = {
        "fs_read": _MockTool("fs_read", "read a file"),
        "bash": _MockTool("bash", "run a command"),
    }
    tracker = ToolStateTracker(max_size=10)
    asyncio.run(tracker.record(ToolCallTrace(tool_name="bash", success=True, summary="build ok")))
    asyncio.run(tracker.record(ToolCallTrace(tool_name="fs_read", success=False, summary="not found")))

    src = ToolStateSource(registry_provider=lambda: registry, tracker=tracker)
    slot = Slot(kind=SlotToolState, filter=SlotFilter(top_k=10))
    items = asyncio.run(src.fetch(slot, Query()))
    # 2 registry items + 2 trace items
    assert len(items) == 4
    assert any("fs_read" in i.text and "read a file" in i.text for i in items)
    assert any("bash" in i.text and "run a command" in i.text for i in items)
    assert any("近期调用 bash" in i.text and "成功" in i.text for i in items)
    assert any("近期调用 fs_read" in i.text and "失败" in i.text for i in items)


def test_tool_state_source_neither_configured():
    """No registry and no tracker yields empty list."""
    src = ToolStateSource(registry_provider=None, tracker=None)
    slot = Slot(kind=SlotToolState, filter=SlotFilter())
    items = asyncio.run(src.fetch(slot, Query()))
    assert items == []


def test_tool_state_source_only_registry():
    """Only registry configured yields tool list only."""
    registry = {"fs_read": _MockTool("fs_read", "read a file")}
    src = ToolStateSource(registry_provider=lambda: registry, tracker=None)
    slot = Slot(kind=SlotToolState, filter=SlotFilter(top_k=5))
    items = asyncio.run(src.fetch(slot, Query()))
    assert len(items) == 1
    assert "fs_read" in items[0].text


# ─── ProfileSource tests (Task 6.3) ───────────────────────────────────────────


class _MockPreference:
    def __init__(self, data):
        self._data = data

    def get_all(self):
        return self._data


class _MockLTM:
    def __init__(self, items):
        self._items = items

    async def filter_by_category(self, categories, limit):
        cat_set = set(categories)
        matched = [it for it in self._items if (it.category or "general") in cat_set]
        matched.sort(key=lambda x: x.importance, reverse=True)
        return matched[:limit] if limit > 0 else matched


def test_profile_source_dual_data():
    """Both preference and LTM produce ContextItems."""
    pref = _MockPreference({"name": "Alice", "theme": "dark"})
    ltm_items = [
        Item(content="用户姓名: Alice", importance=0.9, category="identity"),
        Item(content="偏好深色模式", importance=0.7, category="preference"),
    ]
    ltm = _MockLTM(ltm_items)

    src = ProfileSource(preference_provider=pref, ltm=ltm)
    slot = Slot(kind=SlotProfile, filter=SlotFilter(categories=["identity", "preference"], top_k=10))
    items = asyncio.run(src.fetch(slot, Query()))
    # 2 preference + 2 LTM
    assert len(items) == 4
    pref_items = [i for i in items if i.score == 1.0]
    ltm_items_out = [i for i in items if i.score != 1.0]
    assert len(pref_items) == 2
    assert len(ltm_items_out) == 2


def test_profile_source_only_preference():
    """Only preference available yields preference items only."""
    pref = _MockPreference({"name": "Alice"})
    src = ProfileSource(preference_provider=pref, ltm=None)
    slot = Slot(kind=SlotProfile, filter=SlotFilter(categories=["identity"], top_k=5))
    items = asyncio.run(src.fetch(slot, Query()))
    assert len(items) == 1
    assert "name: Alice" in items[0].text
    assert items[0].score == 1.0


def test_profile_source_only_ltm():
    """Only LTM available yields LTM items only."""
    ltm_items = [
        Item(content="用户姓名: Bob", importance=0.8, category="identity"),
    ]
    ltm = _MockLTM(ltm_items)
    src = ProfileSource(preference_provider=None, ltm=ltm)
    slot = Slot(kind=SlotProfile, filter=SlotFilter(categories=["identity"], top_k=5))
    items = asyncio.run(src.fetch(slot, Query()))
    assert len(items) == 1
    assert "用户姓名: Bob" in items[0].text
    assert items[0].score == 0.8
    assert items[0].meta.get("category") == "identity"
