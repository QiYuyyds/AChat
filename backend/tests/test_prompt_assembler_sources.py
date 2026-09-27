"""Tests for prompt_assembler source behaviors.

Covers:
- multi-source resilience: one source raising must not block later sources
- SlotFilter.max_age_hours actually filters stale task-memory observations
- SlotFilter.min_score actually filters low-relevance recall items
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from app.services.prompt_assembler import (
    ContextAssembler,
    ContextItem,
    ContextSource,
    Query,
    RecallSource,
    Slot,
    SlotFilter,
    SlotRecall,
    SlotTaskMem,
    SourceRegistry,
    StepObservation,
    TaskMemBuffer,
    TaskMemSource,
)


class _ExplodingSource(ContextSource):
    def id(self) -> str:
        return "explode"

    def supports(self, kind: str) -> bool:
        return kind == SlotTaskMem

    async def fetch(self, slot: Slot, q: Query) -> list[ContextItem]:
        raise RuntimeError("source down")


class _StubSource(ContextSource):
    def __init__(self, items: list[ContextItem]) -> None:
        self._items = items

    def id(self) -> str:
        return "stub"

    def supports(self, kind: str) -> bool:
        return kind == SlotTaskMem

    async def fetch(self, slot: Slot, q: Query) -> list[ContextItem]:
        return self._items


@pytest.mark.asyncio
async def test_source_error_does_not_block_later_sources():
    reg = SourceRegistry()
    reg.register(_ExplodingSource())
    reg.register(_StubSource([ContextItem(text="kept item", source="stub")]))
    assembler = ContextAssembler(registry=reg)

    slot = Slot(kind=SlotTaskMem, filter=SlotFilter(token_budget=0))
    fs = await assembler._fill_slot(slot, Query(mode="chat"))

    assert [i.text for i in fs.items] == ["kept item"]
    assert fs.skipped is False


@pytest.mark.asyncio
async def test_task_mem_max_age_filters_stale_observations():
    buf = TaskMemBuffer()
    await buf.push(
        StepObservation(
            step_id="s1",
            tool_name="fs_read",
            result="old",
            created_at=time.time() - 48 * 3600,
        )
    )
    await buf.push(StepObservation(step_id="s2", tool_name="fs_read", result="new"))
    src = TaskMemSource(buffer=buf)

    slot = Slot(kind=SlotTaskMem, filter=SlotFilter(max_age_hours=24, top_k=8))
    items = await src.fetch(slot, Query(mode="react"))

    assert len(items) == 1
    assert "s2" in items[0].text


@pytest.mark.asyncio
async def test_task_mem_no_max_age_keeps_all():
    buf = TaskMemBuffer()
    await buf.push(
        StepObservation(
            step_id="s1",
            tool_name="fs_read",
            result="old",
            created_at=time.time() - 48 * 3600,
        )
    )
    await buf.push(StepObservation(step_id="s2", tool_name="fs_read", result="new"))
    src = TaskMemSource(buffer=buf)

    slot = Slot(kind=SlotTaskMem, filter=SlotFilter(top_k=8))
    items = await src.fetch(slot, Query(mode="react"))

    assert len(items) == 2


class _FakeMemory:
    def __init__(self) -> None:
        self.records = [
            SimpleNamespace(
                content="high",
                score=0.9,
                frontmatter={"status": "active"},
                name="mem_high",
            ),
            SimpleNamespace(
                content="low",
                score=0.2,
                frontmatter={"status": "active"},
                name="mem_low",
            ),
        ]

    async def recall(self, text: str, top_k: int, agent_id: str):
        return self.records


@pytest.mark.asyncio
async def test_recall_min_score_filters_low_relevance():
    src = RecallSource(_FakeMemory())
    slot = Slot(kind=SlotRecall, filter=SlotFilter(min_score=0.5, top_k=3))

    items = await src.fetch(slot, Query(text="query", mode="chat"))

    assert [i.text for i in items] == ["high"]


@pytest.mark.asyncio
async def test_recall_no_min_score_keeps_all():
    src = RecallSource(_FakeMemory())
    slot = Slot(kind=SlotRecall, filter=SlotFilter(top_k=3))

    items = await src.fetch(slot, Query(text="query", mode="chat"))

    assert [i.text for i in items] == ["high", "low"]
