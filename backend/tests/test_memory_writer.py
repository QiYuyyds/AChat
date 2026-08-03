"""Unit tests for memory_writer — LTM extraction, preference extraction,
importance grading, and end-to-end conversation verification.

Updated for the V3-based LTM extraction pipeline (extract_ltm_memories).
"""

import asyncio
import json
from unittest.mock import patch

import pytest

from app.memory.memory_writer import (
    _IMPORTANCE_BY_CATEGORY,
    _extract_rule_based,
    extract_ltm_memories,
    extract_preferences,
)
from app.memory.preference import Preference
from app.services.prompt_assembler import (
    ContextAssembler,
    ProfileSource,
    Query,
    SourceRegistry,
)


class _MockLTM:
    """Minimal LTM stub recording store_classified() calls."""

    def __init__(self):
        self.stored = []

    async def store_classified(
        self, content, importance, emb, category, tags, slot_hint,
        scope="global", agent_id="", user_id=None,
        summary="", keywords=None, content_scope="",
    ):
        self.stored.append({
            "content": content,
            "importance": importance,
            "category": category,
            "tags": tags,
            "slot_hint": slot_hint,
            "scope": scope,
            "agent_id": agent_id,
            "summary": summary,
            "keywords": keywords or [],
            "content_scope": content_scope,
        })
        return True


# ─── V3 LTM extraction: natural language memories, no routing ────────────────


def test_ltm_extracts_natural_language_from_full_conversation():
    """V3 extraction produces self-contained natural language memories."""
    def mock_generate(sys_prompt, user_msg):
        return json.dumps({
            "memory": [
                {"id": "0", "text": "User's name is Zhang San", "attributed_to": "user"},
                {"id": "1", "text": "User's project uses React 19", "attributed_to": "user"},
                {"id": "2", "text": "User plans to refactor the auth module next week", "attributed_to": "user"},
            ]
        })

    ltm = _MockLTM()
    asyncio.run(extract_ltm_memories(
        mock_generate, None, ltm,
        "我叫张三，我们项目用 React 19，打算下周重构认证模块",
        "好的，记住了",
    ))
    assert len(ltm.stored) == 3
    for item in ltm.stored:
        assert item["category"] == ""
        assert item["importance"] == 0.5
        assert "user" in item["tags"]


def test_ltm_trivial_conversation_produces_no_memories():
    """Trivial conversation → empty memory list → no LTM items."""
    def mock_generate(sys_prompt, user_msg):
        return json.dumps({"memory": []})

    ltm = _MockLTM()
    asyncio.run(extract_ltm_memories(
        mock_generate, None, ltm,
        "你好", "你好！有什么可以帮你的？",
    ))
    assert ltm.stored == []


def test_ltm_identity_memory_enters_ltm_not_redirected():
    """Identity-type memory goes directly to LTM, not redirected to Preference."""
    def mock_generate(sys_prompt, user_msg):
        return json.dumps({
            "memory": [
                {"id": "0", "text": "User's name is Zhang San", "attributed_to": "user"},
            ]
        })

    ltm = _MockLTM()
    asyncio.run(extract_ltm_memories(
        mock_generate, None, ltm, "我叫张三", "好的",
    ))
    assert len(ltm.stored) == 1
    assert ltm.stored[0]["content"] == "User's name is Zhang San"


def test_ltm_existing_keys_included_in_prompt():
    """existing_keys are passed to the LLM via the modified system prompt."""
    captured = {}

    def mock_generate(sys_prompt, user_msg):
        captured["sys_prompt"] = sys_prompt
        return json.dumps({"memory": []})

    ltm = _MockLTM()
    asyncio.run(extract_ltm_memories(
        mock_generate, None, ltm, "msg", "reply",
        existing_keys=["姓名", "职业"],
    ))
    assert "姓名" in captured["sys_prompt"]
    assert "职业" in captured["sys_prompt"]


# ─── Task 5.3: importance grading by category ────────────────────────────────


def test_importance_table_values():
    """The importance table maps each category to its expected weight."""
    assert _IMPORTANCE_BY_CATEGORY["identity"] == 0.9
    assert _IMPORTANCE_BY_CATEGORY["policy"] == 0.8
    assert _IMPORTANCE_BY_CATEGORY["preference"] == 0.7
    assert _IMPORTANCE_BY_CATEGORY["fact"] == 0.5
    assert _IMPORTANCE_BY_CATEGORY["episodic"] == 0.4
    assert _IMPORTANCE_BY_CATEGORY["tool_failure"] == 0.3
    assert _IMPORTANCE_BY_CATEGORY["general"] == 0.3


# ─── Task 7.4: extract_preferences LLM overlay ────────────────────────────────


def test_extract_preferences_llm_success():
    """LLM returns valid preference JSON → returned as-is."""
    def mock_generate(sys_prompt, user_msg):
        return json.dumps({"姓名": "涵涵", "喜好": "清新沉浸感的设计风格"})

    result = asyncio.run(extract_preferences(mock_generate, "我叫涵涵，我喜欢清新沉浸感的设计风格"))
    assert result == {"姓名": "涵涵", "喜好": "清新沉浸感的设计风格"}


def test_extract_preferences_invalid_json_falls_back_to_rules():
    """LLM returns non-JSON → rule-based fallback."""
    def mock_generate(sys_prompt, user_msg):
        return "这不是JSON"

    result = asyncio.run(extract_preferences(mock_generate, "我喜欢清新风格"))
    assert result == {"喜好": "清新风格"}


def test_extract_preferences_none_generate_fn_uses_rules():
    """No generate_fn → direct rule-based extraction."""
    result = asyncio.run(extract_preferences(None, "我叫涵涵"))
    assert result == {"姓名": "涵涵"}


def test_extract_preferences_empty_msg_returns_empty():
    assert asyncio.run(extract_preferences(None, "")) == {}


def test_extract_preferences_code_fenced_json():
    """Code-fenced JSON is stripped and parsed."""
    def mock_generate(sys_prompt, user_msg):
        return '```json\n{"姓名": "涵涵"}\n```'

    result = asyncio.run(extract_preferences(mock_generate, "我叫涵涵"))
    assert result == {"姓名": "涵涵"}


def test_extract_rule_based_returns_first_match():
    """_extract_rule_based ports the original single-match semantics."""
    result = _extract_rule_based("我喜欢清新风格，我叫涵涵")
    # First-matching rule wins ("我喜欢" → 喜好), value is the rest of the line.
    assert result == {"喜好": "清新风格，我叫涵涵"}


def test_extract_rule_based_no_match():
    assert _extract_rule_based("今天天气不错") == {}


# ─── End-to-end conversation verification ─────────────────────────────────────


@pytest.mark.asyncio
async def test_e2e_conversation_memory_quality():
    """Simulate one conversation turn and assert quality invariants:

    1. Preference table has no oversized garbage (length cap holds).
    2. LTM memories are natural language with default importance 0.5.
    3. The static profile prompt is byte-identical across two assembly runs.
    """
    pref = Preference(user_id="default_user")

    # DB is mocked away; set() still updates the in-memory cache.
    with patch("app.memory.preference.get_db") as mock_db:
        mock_db.side_effect = Exception("no db")

        # Step 1: user message → LLM preference overlay → save_batch.
        long_fragment = "唱跳rap" + "而且" * 400  # well over the 200 cap
        user_msg = f"我叫涵涵，我喜欢{long_fragment}"

        def overlay_generate(sys_prompt, user_msg_inner):
            return json.dumps({"姓名": "涵涵", "喜好": "唱跳rap"})

        prefs = await extract_preferences(overlay_generate, user_msg)
        await pref.save_batch(prefs)

        # Step 2: full conversation → LTM extraction (V3 natural language).
        ltm = _MockLTM()

        def extract_generate(sys_prompt, user_msg_inner):
            return json.dumps({
                "memory": [
                    {"id": "0", "text": "User's name is Hanhan", "attributed_to": "user"},
                    {"id": "1", "text": "User's project uses PostgreSQL", "attributed_to": "user"},
                ]
            })

        await extract_ltm_memories(
            extract_generate, None, ltm,
            user_msg, "好的，记住了",
        )

    # Invariant 1: no preference value exceeds the 200-char cap.
    for k, v in pref.get_all().items():
        assert len(v) <= 200, f"preference {k} exceeds cap: {len(v)}"
        assert "而且" not in v  # garbage fragment didn't leak through

    # The LLM overlay value for 喜好 won over the rule-extracted long fragment.
    assert pref.get("喜好") == "唱跳rap"
    assert pref.get("姓名") == "涵涵"

    # Invariant 2: LTM has natural language memories with default importance.
    assert len(ltm.stored) == 2
    for item in ltm.stored:
        assert item["importance"] == 0.5
        assert item["category"] == ""
        assert "user" in item["tags"]

    # Invariant 3: dynamic profile prompt is byte-stable across two runs.
    registry = SourceRegistry()
    registry.register(ProfileSource(preference_provider=pref))
    assembler = ContextAssembler(registry=registry)
    rc1 = await assembler.assemble(Query(text="hello", mode="chat"))
    rc2 = await assembler.assemble(Query(text="hello", mode="chat"))
    assert rc1.render_dynamic() == rc2.render_dynamic()
    assert "涵涵" in rc1.render_dynamic()


# ─── optimize-preference-system: Task 7.7 (existing_keys in prompt) ──────────


def test_7_7_extract_preferences_with_existing_keys():
    """Task 7.7: extract_preferences() with existing_keys produces prompt containing the key list.

    The mock generate_fn captures the system_prompt and verifies that the
    existing keys are included in the prompt text.
    """
    captured = {}

    def mock_generate(sys_prompt, user_msg):
        captured["sys_prompt"] = sys_prompt
        return json.dumps({"喜好": "函数式编程"})

    result = asyncio.run(
        extract_preferences(mock_generate, "我偏爱函数式编程", existing_keys=["喜好", "姓名"])
    )
    assert result == {"喜好": "函数式编程"}
    # The prompt must include the existing keys
    assert "喜好" in captured["sys_prompt"]
    assert "姓名" in captured["sys_prompt"]


def test_7_7_extract_preferences_without_existing_keys():
    """When existing_keys is None, the prompt does NOT include the key list rule."""
    captured = {}

    def mock_generate(sys_prompt, user_msg):
        captured["sys_prompt"] = sys_prompt
        return json.dumps({"喜好": "函数式编程"})

    result = asyncio.run(
        extract_preferences(mock_generate, "我偏爱函数式编程")
    )
    assert result == {"喜好": "函数式编程"}
    assert "Existing Keys" not in captured["sys_prompt"]


# ─── optimize-preference-system: Task 7.8 (_consolidate_preferences) ────────


@pytest.mark.asyncio
async def test_7_8_consolidate_preferences_merges_duplicates():
    """Task 7.8: _consolidate_preferences() merges duplicate keys when count > threshold.

    Sets up a MemoryService with 16+ preferences (above threshold), mocks the
    LLM to return a merged dict with fewer keys, and verifies that removed
    keys are deleted from the in-memory cache.
    """

    from app.memory.memory_service import MemoryService

    # Build a minimal MemoryService with mocked internals
    ms = MemoryService.__new__(MemoryService)
    ms._generate_fn = lambda sys_p, msg: json.dumps({"姓名": "小明", "喜好": "编程"})
    ms.preference = Preference(user_id="default_user")

    # Populate with 16 keys (above threshold of 15), including synonyms
    for i in range(14):
        ms.preference.preferences[f"key_{i}"] = f"val_{i}"
    ms.preference.preferences["喜欢"] = "Python"   # synonym of 喜好
    ms.preference.preferences["偏好"] = "Java"     # synonym of 喜好
    assert len(ms.preference.data) == 16

    # Mock get_db to avoid PG operations
    with patch("app.memory.memory_service.get_db") as mock_db:
        mock_db.side_effect = Exception("no db")
        await ms._consolidate_preferences()

    # After consolidation: LLM returned {"姓名": "小明", "喜好": "编程"}
    # save_batch normalizes and updates, removed keys are deleted from in-memory
    final = ms.preference.get_all()
    assert "姓名" in final
    assert "喜好" in final
    assert "喜欢" not in final
    assert "偏好" not in final
    assert len(final) == 2


@pytest.mark.asyncio
async def test_7_8_consolidate_preferences_skips_when_below_threshold():
    """When preference count <= 15, _safe_consolidate does not call _consolidate_preferences."""
    from unittest.mock import AsyncMock, MagicMock

    from app.memory.memory_service import MemoryService

    ms = MemoryService.__new__(MemoryService)
    ms._generate_fn = lambda sys_p, msg: json.dumps({})
    ms.preference = Preference(user_id="default_user")
    ms.preference.preferences = {f"key_{i}": f"val_{i}" for i in range(10)}
    ms.graph_memory = None
    ms.ltm = MagicMock()
    ms.ltm.consolidate = AsyncMock(return_value=None)
    ms.ltm.need_consolidation = MagicMock(return_value=True)
    ms._sync_consolidation_to_db = AsyncMock()

    # Spy on _consolidate_preferences
    ms._consolidate_preferences = AsyncMock()
    await ms._safe_consolidate()
    # Below threshold → not called
    ms._consolidate_preferences.assert_not_called()


# ─── optimize-preference-system: Tasks 7.9-7.10 (tool error memory hook) ────


@pytest.mark.asyncio
async def test_7_9_post_run_memory_hook_appends_tool_error():
    """Task 7.9: _post_run_memory_hook appends tool error block when isError=True parts exist."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.services.agent_runner import _post_run_memory_hook

    # Mock RunExecutionResult
    result = MagicMock()
    result.output_message_ids = ["msg_1"]

    # Mock memory service
    ms = MagicMock()
    ms.on_message_end = AsyncMock()

    # Mock message with tool_use + tool_result(isError=True) parts
    mock_msg = MagicMock()
    mock_msg.parts_list = [
        {"type": "text", "content": "I'll create the artifact now."},
        {"type": "tool_use", "callId": "call_1", "toolName": "write_artifact"},
        {"type": "tool_result", "callId": "call_1", "isError": True, "result": {"error": "Invalid args"}},
    ]

    with patch("app.services.agent_runner._get_memory_service", return_value=ms), \
         patch("app.services.agent_runner.get_local_db") as mock_get_db:
        mock_ctx = AsyncMock()
        mock_ctx.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=mock_msg)))
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=None)

        await _post_run_memory_hook("user prompt", result, "conv_1")

    # Verify on_message_end was called with assistant text containing tool error
    calls = ms.on_message_end.call_args_list
    assert len(calls) >= 2  # user + assistant
    assistant_call = calls[1]
    assert assistant_call.args[0] == "assistant"
    assistant_text = assistant_call.args[1]
    assert "[工具执行错误]" in assistant_text
    assert "write_artifact" in assistant_text
    assert "Invalid args" in assistant_text


@pytest.mark.asyncio
async def test_7_10_post_run_memory_hook_no_error_block_when_no_tool_errors():
    """Task 7.10: _post_run_memory_hook does not append error block when no tool errors."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.services.agent_runner import _post_run_memory_hook

    result = MagicMock()
    result.output_message_ids = ["msg_1"]

    ms = MagicMock()
    ms.on_message_end = AsyncMock()

    # Mock message with only successful tool calls (no isError=True)
    mock_msg = MagicMock()
    mock_msg.parts_list = [
        {"type": "text", "content": "Done!"},
        {"type": "tool_use", "callId": "call_1", "toolName": "read_file"},
        {"type": "tool_result", "callId": "call_1", "isError": False, "result": "file content"},
    ]

    with patch("app.services.agent_runner._get_memory_service", return_value=ms), \
         patch("app.services.agent_runner.get_local_db") as mock_get_db:
        mock_ctx = AsyncMock()
        mock_ctx.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=mock_msg)))
        mock_get_db.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_get_db.return_value.__aexit__ = AsyncMock(return_value=None)

        await _post_run_memory_hook("user prompt", result, "conv_1")

    calls = ms.on_message_end.call_args_list
    assert len(calls) >= 2
    assistant_call = calls[1]
    assert assistant_call.args[0] == "assistant"
    assistant_text = assistant_call.args[1]
    assert "[工具执行错误]" not in assistant_text
    assert assistant_text == "Done!"
