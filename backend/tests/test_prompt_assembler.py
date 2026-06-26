"""Unit tests for Prompt Assembler — schema selection, source assembly, budget trimming."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.prompt_assembler import (
    CHAT_SCHEMA, TOOL_SCHEMA, REACT_SCHEMA, RAG_SCHEMA,
    ContextAssembler, ContextItem, ConstraintsSource,
    FilledSlot, ProfileSource, Query, RecallSource,
    RuntimeContext, RuntimeContextSchema, SourceRegistry,
    Slot, SlotConstraints, SlotFilter, SlotKind, SlotProfile, SlotRecall,
    default_schemas, slot_priority, _trim_by_budget,
)


class TestSchemas:
    def test_default_schemas_has_all_modes(self):
        schemas = default_schemas()
        assert "chat" in schemas
        assert "tool" in schemas
        assert "react" in schemas
        assert "rag" in schemas

    def test_chat_schema_slots(self):
        assert len(CHAT_SCHEMA.slots) == 3
        kinds = [s.kind for s in CHAT_SCHEMA.slots]
        assert SlotConstraints in kinds
        assert SlotProfile in kinds
        assert SlotRecall in kinds

    def test_react_schema_has_planner(self):
        kinds = [s.kind for s in REACT_SCHEMA.slots]
        assert "planner" in kinds
        assert "task_memory" in kinds

    def test_slot_priority_ordering(self):
        assert slot_priority(SlotConstraints) < slot_priority(SlotProfile)
        assert slot_priority(SlotProfile) < slot_priority(SlotRecall)


class TestTrimByBudget:
    def test_within_budget(self):
        items = [ContextItem(text="short"), ContextItem(text="also short")]
        result = _trim_by_budget(items, 100)
        assert len(result) == 2

    def test_exceeds_budget(self):
        items = [ContextItem(text="a" * 50), ContextItem(text="b" * 50), ContextItem(text="c" * 50)]
        result = _trim_by_budget(items, 80)
        assert len(result) == 1

    def test_empty_items(self):
        result = _trim_by_budget([], 100)
        assert result == []


class TestSources:
    @pytest.mark.asyncio
    async def test_profile_source_empty(self):
        src = ProfileSource(preference_provider=None)
        slot = Slot(kind=SlotProfile)
        items = await src.fetch(slot, Query(text="hello"))
        assert items == []

    @pytest.mark.asyncio
    async def test_profile_source_with_prefs(self):
        mock_pref = MagicMock()
        mock_pref.get_all.return_value = {"姓名": "小明", "喜好": "编程"}
        src = ProfileSource(preference_provider=mock_pref)
        slot = Slot(kind=SlotProfile, filter=SlotFilter(top_k=5))
        items = await src.fetch(slot, Query(text="hello"))
        assert len(items) == 2

    @pytest.mark.asyncio
    async def test_constraints_source(self):
        src = ConstraintsSource(constraints_text="No harmful content")
        slot = Slot(kind=SlotConstraints)
        items = await src.fetch(slot, Query(text="hello"))
        assert len(items) == 1
        assert items[0].text == "No harmful content"

    @pytest.mark.asyncio
    async def test_constraints_source_empty(self):
        src = ConstraintsSource(constraints_text="")
        slot = Slot(kind=SlotConstraints)
        items = await src.fetch(slot, Query(text="hello"))
        assert items == []

    @pytest.mark.asyncio
    async def test_recall_source_no_memory(self):
        src = RecallSource(memory_service=None)
        slot = Slot(kind=SlotRecall)
        items = await src.fetch(slot, Query(text="hello"))
        assert items == []


class TestAssembler:
    @pytest.mark.asyncio
    async def test_assemble_chat_mode(self):
        registry = SourceRegistry()
        registry.register(ConstraintsSource(constraints_text="Be helpful"))
        assembler = ContextAssembler(registry=registry)
        rc = await assembler.assemble(Query(text="hello", mode="chat"))
        assert rc.schema.mode == "chat"
        assert len(rc.filled) == 3  # CHAT_SCHEMA has 3 slots

    @pytest.mark.asyncio
    async def test_assemble_unknown_mode_fallback(self):
        assembler = ContextAssembler()
        rc = await assembler.assemble(Query(text="hello", mode="unknown"))
        assert rc.schema.mode == "chat"  # Falls back to chat

    @pytest.mark.asyncio
    async def test_assemble_empty_registry(self):
        assembler = ContextAssembler()
        rc = await assembler.assemble(Query(text="hello", mode="chat"))
        # All slots should be skipped (no sources)
        for fs in rc.filled:
            assert fs.skipped or len(fs.items) == 0

    @pytest.mark.asyncio
    async def test_global_budget_trimming(self):
        """When total content exceeds global limit, low-priority slots get trimmed."""
        registry = SourceRegistry()
        # Register a source that returns very long content
        mock_pref = MagicMock()
        mock_pref.get_all.return_value = {"key": "x" * 3000}
        registry.register(ProfileSource(preference_provider=mock_pref))
        registry.register(ConstraintsSource(constraints_text="important"))

        assembler = ContextAssembler(registry=registry, global_limit=500)
        rc = await assembler.assemble(Query(text="hello", mode="chat"))

        total = sum(len(item.text) for fs in rc.filled for item in fs.items)
        assert total <= 500


class TestRuntimeContext:
    def test_render_empty(self):
        rc = RuntimeContext(schema=CHAT_SCHEMA)
        assert rc.render() == ""

    def test_render_system_prompt(self):
        rc = RuntimeContext(
            schema=CHAT_SCHEMA,
            filled=[
                FilledSlot(kind=SlotConstraints, items=[ContextItem(text="Be helpful")]),
                FilledSlot(kind=SlotProfile, items=[ContextItem(text="Name: Alice")]),
                FilledSlot(kind=SlotRecall, skipped=True),
            ],
        )
        prompt = rc.render_system_prompt("You are a helpful assistant.")
        assert "You are a helpful assistant." in prompt
        assert "Be helpful" in prompt
        assert "Name: Alice" in prompt

    def test_render_history(self):
        rc = RuntimeContext(
            schema=CHAT_SCHEMA,
            filled=[
                FilledSlot(kind=SlotConstraints, items=[ContextItem(text="Rule 1")]),
            ],
        )
        history = rc.render_history()
        assert len(history) == 1
        assert history[0]["role"] == "system"
        assert "Rule 1" in history[0]["content"]

    def test_render_history_empty(self):
        rc = RuntimeContext(schema=CHAT_SCHEMA)
        assert rc.render_history() == []

    def test_slot_by_kind(self):
        rc = RuntimeContext(
            schema=CHAT_SCHEMA,
            filled=[
                FilledSlot(kind=SlotProfile, items=[ContextItem(text="test")]),
            ],
        )
        slot = rc.slot_by_kind(SlotProfile)
        assert slot is not None
        assert len(slot.items) == 1

        missing = rc.slot_by_kind("nonexistent")
        assert missing is None
