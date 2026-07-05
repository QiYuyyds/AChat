"""Unit tests for Prompt Assembler — schema selection, source assembly, budget trimming."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from app.services.prompt_assembler import (
    CHAT_SCHEMA, TOOL_SCHEMA, REACT_SCHEMA, RAG_SCHEMA,
    ContextAssembler, ContextItem, ConstraintsSource,
    FilledSlot, ProfileSource, Query, RecallSource,
    RuntimeContext, RuntimeContextSchema, SourceRegistry,
    Slot, SlotConstraints, SlotFilter, SlotKind, SlotPlanner,
    SlotProfile, SlotRecall, SlotTaskMem, SlotToolState,
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
        assert len(CHAT_SCHEMA.slots) == 2
        kinds = [s.kind for s in CHAT_SCHEMA.slots]
        assert SlotConstraints in kinds
        assert SlotProfile in kinds
        assert SlotRecall not in kinds

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

    def test_partial_exceed_drops_rest(self):
        """When a later item pushes the total over budget, it and the rest are dropped."""
        items = [ContextItem(text="a" * 200), ContextItem(text="b" * 400), ContextItem(text="c" * 50)]
        result = _trim_by_budget(items, 500)
        # item[0] fits (200<500); item[1] pushes total to 600>500 → dropped along with item[2]
        assert len(result) == 1
        assert result[0].text == "a" * 200

    def test_first_item_exceeds_budget_returns_empty(self):
        """A single item larger than the budget is dropped, not truncated."""
        items = [ContextItem(text="x" * 600)]
        result = _trim_by_budget(items, 200)
        assert result == []

    def test_exact_fit_kept(self):
        """Items whose cumulative length exactly equals the budget are kept (cap is >, not >=)."""
        items = [ContextItem(text="a" * 200), ContextItem(text="b" * 300)]
        result = _trim_by_budget(items, 500)
        assert len(result) == 2

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
        assert len(rc.filled) == 2  # CHAT_SCHEMA has 2 slots (Recall removed)

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


# ─── Task 9.1: Slot.static field + render_static/render_dynamic ──────────────


class TestStaticDynamicSplit:
    def test_slot_static_field_default(self):
        """Slot.static defaults to False."""
        slot = Slot(kind=SlotConstraints)
        assert slot.static is False

    def test_slot_static_field_true(self):
        """Slot.static can be set to True."""
        slot = Slot(kind=SlotConstraints, static=True)
        assert slot.static is True

    def test_render_static_only_static_slots(self):
        """render_static() returns only static=True slot content.

        Profile is now static=False, so only Constraints content appears
        in render_static().
        """
        rc = RuntimeContext(
            schema=CHAT_SCHEMA,
            filled=[
                FilledSlot(kind=SlotConstraints, items=[ContextItem(text="Rule 1")]),
                FilledSlot(kind=SlotProfile, items=[ContextItem(text="Name: Alice")]),
            ],
        )
        static = rc.render_static()
        assert "Rule 1" in static
        assert "Name: Alice" not in static

    def test_render_dynamic_includes_profile(self):
        """render_dynamic() includes Profile content (now dynamic) wrapped in system-reminder."""
        rc = RuntimeContext(
            schema=CHAT_SCHEMA,
            filled=[
                FilledSlot(kind=SlotConstraints, items=[ContextItem(text="Rule 1")]),
                FilledSlot(kind=SlotProfile, items=[ContextItem(text="Name: Alice")]),
            ],
        )
        dynamic = rc.render_dynamic()
        assert "<system-reminder>" in dynamic
        assert "Name: Alice" in dynamic
        assert "Rule 1" not in dynamic

    def test_render_dynamic_includes_dynamic_slots(self):
        """render_dynamic() returns dynamic slot content wrapped in system-reminder."""
        rc = RuntimeContext(
            schema=REACT_SCHEMA,
            filled=[
                FilledSlot(kind=SlotConstraints, items=[ContextItem(text="Rule 1")]),
                FilledSlot(kind=SlotPlanner, items=[ContextItem(text="Task in progress")]),
            ],
        )
        dynamic = rc.render_dynamic()
        assert "<system-reminder>" in dynamic
        assert "</system-reminder>" in dynamic
        assert "Task in progress" in dynamic
        assert "Rule 1" not in dynamic

    def test_render_static_excludes_dynamic_slots(self):
        """render_static() does not include dynamic slot content."""
        rc = RuntimeContext(
            schema=REACT_SCHEMA,
            filled=[
                FilledSlot(kind=SlotConstraints, items=[ContextItem(text="Rule 1")]),
                FilledSlot(kind=SlotPlanner, items=[ContextItem(text="Task in progress")]),
            ],
        )
        static = rc.render_static()
        assert "Rule 1" in static
        assert "Task in progress" not in static

    def test_get_slot_def_returns_none_for_missing(self):
        """_get_slot_def returns None for a kind not in schema."""
        rc = RuntimeContext(schema=CHAT_SCHEMA)
        assert rc._get_slot_def("nonexistent") is None

    def test_get_slot_def_returns_slot(self):
        """_get_slot_def returns the Slot definition for a known kind."""
        rc = RuntimeContext(schema=CHAT_SCHEMA)
        slot = rc._get_slot_def(SlotConstraints)
        assert slot is not None
        assert slot.kind == SlotConstraints
        assert slot.static is True


# ─── Task 9.2: Schema static classification ──────────────────────────────────


class TestSchemaStaticClassification:
    def test_chat_schema_static_classification(self):
        for slot in CHAT_SCHEMA.slots:
            if slot.kind == SlotConstraints:
                assert slot.static is True, f"{slot.kind} should be static=True"
            elif slot.kind == SlotProfile:
                assert slot.static is False, f"{slot.kind} should be static=False"

    def test_tool_schema_static_classification(self):
        for slot in TOOL_SCHEMA.slots:
            if slot.kind == SlotConstraints:
                assert slot.static is True, f"{slot.kind} should be static=True"
            elif slot.kind == SlotProfile:
                assert slot.static is False, f"{slot.kind} should be static=False"
            elif slot.kind == SlotToolState:
                assert slot.static is False, f"{slot.kind} should be static=False"

    def test_react_schema_static_classification(self):
        for slot in REACT_SCHEMA.slots:
            if slot.kind == SlotConstraints:
                assert slot.static is True, f"{slot.kind} should be static=True"
            elif slot.kind == SlotProfile:
                assert slot.static is False, f"{slot.kind} should be static=False"
            elif slot.kind in (SlotPlanner, SlotTaskMem, SlotToolState):
                assert slot.static is False, f"{slot.kind} should be static=False"

    def test_rag_schema_static_classification(self):
        for slot in RAG_SCHEMA.slots:
            if slot.kind == SlotConstraints:
                assert slot.static is True, f"{slot.kind} should be static=True"
            elif slot.kind == SlotProfile:
                assert slot.static is False, f"{slot.kind} should be static=False"


# ─── Task 9.3: No SlotRecall in schemas ──────────────────────────────────────


class TestSchemaNoRecall:
    def test_chat_schema_no_recall(self):
        kinds = [s.kind for s in CHAT_SCHEMA.slots]
        assert SlotRecall not in kinds

    def test_tool_schema_no_recall(self):
        kinds = [s.kind for s in TOOL_SCHEMA.slots]
        assert SlotRecall not in kinds

    def test_react_schema_no_recall(self):
        kinds = [s.kind for s in REACT_SCHEMA.slots]
        assert SlotRecall not in kinds

    def test_rag_schema_no_recall(self):
        kinds = [s.kind for s in RAG_SCHEMA.slots]
        assert SlotRecall not in kinds


# ─── optimize-preference-system: Tasks 7.1-7.4 ──────────────────────────────


class TestProfileSlotDynamicAndScored:
    """Tests for the optimize-preference-system change:
    Profile slot is static=False, ProfileSource reads only from Preference,
    computes score dynamically, and sorts by score descending.
    """

    def test_7_1_profile_slot_static_false_all_schemas(self):
        """Task 7.1: ProfileSlot is static=False in all 4 Schemas."""
        for schema in (CHAT_SCHEMA, TOOL_SCHEMA, REACT_SCHEMA, RAG_SCHEMA):
            profile_slots = [s for s in schema.slots if s.kind == SlotProfile]
            assert len(profile_slots) == 1, f"{schema.mode} should have exactly one Profile slot"
            assert profile_slots[0].static is False, \
                f"{schema.mode}: SlotProfile should be static=False"
            # top_k must not be set on Profile filter
            assert profile_slots[0].filter.top_k == 0, \
                f"{schema.mode}: SlotProfile should not set top_k"

    @pytest.mark.asyncio
    async def test_7_2_profile_source_no_ltm(self):
        """Task 7.2: ProfileSource.fetch() returns items only from preference_provider."""
        mock_pref = MagicMock()
        mock_pref.get_all.return_value = {"姓名": "小明"}
        src = ProfileSource(preference_provider=mock_pref)
        slot = Slot(kind=SlotProfile, filter=SlotFilter(token_budget=600))
        items = await src.fetch(slot, Query(text="hello"))
        # Should return data from preference_provider only
        assert len(items) == 1
        assert "小明" in items[0].text
        # Verify no LTM attribute exists
        assert not hasattr(src, "_ltm")

    @pytest.mark.asyncio
    async def test_7_3_profile_source_correct_scores(self):
        """Task 7.3: ProfileSource.fetch() assigns correct scores.

        identity (姓名) → 0.9, preference (偏好) → 0.7, unclassified (天气) → 0.3.
        Keys are chosen to match classify_memory_content rules.
        """
        mock_pref = MagicMock()
        mock_pref.get_all.return_value = {
            "姓名": "张三",        # identity → 0.9
            "偏好": "编程",        # preference → 0.7 (contains '偏好')
            "天气": "晴",          # unclassified → 0.3
        }
        src = ProfileSource(preference_provider=mock_pref)
        slot = Slot(kind=SlotProfile, filter=SlotFilter(token_budget=600))
        items = await src.fetch(slot, Query(text="hello"))
        scores = {item.text.split(": ")[0]: item.score for item in items}
        assert scores["姓名"] == 0.9
        assert scores["偏好"] == 0.7
        assert scores["天气"] == 0.3

    @pytest.mark.asyncio
    async def test_7_4_profile_source_sorted_by_score_desc(self):
        """Task 7.4: ProfileSource.fetch() sorts items by score descending."""
        mock_pref = MagicMock()
        mock_pref.get_all.return_value = {
            "天气": "晴",          # 0.3 (general)
            "姓名": "张三",        # 0.9 (identity)
            "偏好": "编程",        # 0.7 (preference)
        }
        src = ProfileSource(preference_provider=mock_pref)
        slot = Slot(kind=SlotProfile, filter=SlotFilter(token_budget=600))
        items = await src.fetch(slot, Query(text="hello"))
        # Should be sorted: identity(0.9) > preference(0.7) > general(0.3)
        assert items[0].score >= items[1].score >= items[2].score
        assert "姓名" in items[0].text   # highest score first
        assert "天气" in items[2].text    # lowest score last

    def test_profile_source_supports_only_profile(self):
        """ProfileSource.supports() returns True only for SlotProfile, not SlotRecall."""
        src = ProfileSource(preference_provider=None)
        assert src.supports(SlotProfile) is True
        assert src.supports(SlotRecall) is False
