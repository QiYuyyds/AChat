"""Unit tests for memory_writer double-write, importance grading, prefix dedup,
and the LLM preference extraction overlay (Tasks 4.4, 5.3, 6.2, 7.4, 8.2)."""

import asyncio
import json
from unittest.mock import patch

import pytest

from app.memory.memory_writer import (
    _IMPORTANCE_BY_CATEGORY,
    _extract_rule_based,
    extract_memory_from_reply,
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

    async def store_classified(self, content, importance, emb, category, tags, slot_hint, scope="global", agent_id=""):
        self.stored.append({
            "content": content,
            "importance": importance,
            "category": category,
            "tags": tags,
            "slot_hint": slot_hint,
        })
        return True


class _MockPreference:
    """Minimal preference stub recording set() calls."""

    def __init__(self):
        self.set_calls = []

    async def set(self, key, value):
        self.set_calls.append((key, value))


# ─── Category routing: single-store ownership, no double-write ────────────────


def test_identity_class_routes_to_preference_only():
    """An identity/preference-class fact goes to the preference store only, not LTM."""
    def mock_generate(sys_prompt, user_msg):
        return json.dumps({"姓名": "涵涵"})

    ltm = _MockLTM()
    pref = _MockPreference()
    asyncio.run(extract_memory_from_reply(
        mock_generate, None, ltm, "用户叫涵涵", preference=pref,
    ))
    # Routed to preference…
    assert ("姓名", "涵涵") in pref.set_calls
    # …and NOT to LTM (no double-write).
    assert ltm.stored == []


def test_fact_class_routes_to_ltm_only():
    """A fact/tool_failure/policy-class fact goes to LTM only, not the preference store."""
    def mock_generate(sys_prompt, user_msg):
        return json.dumps({"报错": "请求超时"})

    ltm = _MockLTM()
    pref = _MockPreference()
    asyncio.run(extract_memory_from_reply(
        mock_generate, None, ltm, "工具报错请求超时", preference=pref,
    ))
    # "报错" matches the tool_failure rule → LTM only.
    assert len(ltm.stored) == 1
    assert ltm.stored[0]["category"] == "tool_failure"
    assert pref.set_calls == []


def test_general_class_is_dropped():
    """A 'general'-classified fact is dropped from both stores."""
    calls = {"n": 0}

    def mock_generate(sys_prompt, user_msg):
        calls["n"] += 1
        if calls["n"] == 1:
            return json.dumps({"天气": "今天晴天"})
        return json.dumps({"category": "general", "tags": [], "slot_hint": ""})

    ltm = _MockLTM()
    pref = _MockPreference()
    asyncio.run(extract_memory_from_reply(
        mock_generate, None, ltm, "今天晴天", preference=pref,
    ))
    assert ltm.stored == []
    assert pref.set_calls == []


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


def test_importance_policy_via_rule():
    """A policy-classified fact (rule match on 必须) gets importance 0.8 in LTM."""
    def mock_generate(sys_prompt, user_msg):
        return json.dumps({"规则": "必须用中文回复"})

    ltm = _MockLTM()
    asyncio.run(extract_memory_from_reply(mock_generate, None, ltm, "必须用中文回复"))
    assert ltm.stored[0]["category"] == "policy"
    assert ltm.stored[0]["importance"] == _IMPORTANCE_BY_CATEGORY["policy"]


def test_importance_fact_via_llm_fallback():
    """A fact classified as 'fact' by LLM fallback gets importance 0.5 in LTM."""
    call_count = {"n": 0}

    def mock_generate(sys_prompt, user_msg):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return json.dumps({"项目数据库": "PostgreSQL"})
        return json.dumps({"category": "fact", "tags": [], "slot_hint": ""})

    ltm = _MockLTM()
    asyncio.run(extract_memory_from_reply(mock_generate, None, ltm, "项目用PostgreSQL"))
    assert ltm.stored[0]["category"] == "fact"
    assert ltm.stored[0]["importance"] == _IMPORTANCE_BY_CATEGORY["fact"]


# ─── fact_content 格式：LTM 事实不加"用户"前缀 ───────────────────────────────


def test_fact_content_has_no_user_prefix():
    """LTM fact content is a plain 'key: value' — no misleading '用户' prefix.

    Weather/tool/world facts are not user attributes, so the content must read
    naturally (e.g. '气温: 15°C'), not '用户气温: ...'.
    """
    calls = {"n": 0}

    def mock_generate(sys_prompt, user_msg):
        calls["n"] += 1
        if calls["n"] == 1:
            return json.dumps({"气温": "15°C ~ 27°C"})
        return json.dumps({"category": "fact", "tags": [], "slot_hint": ""})

    ltm = _MockLTM()
    asyncio.run(extract_memory_from_reply(mock_generate, None, ltm, "今天天气"))
    assert ltm.stored[0]["content"] == "气温: 15°C ~ 27°C"


def test_fact_content_strips_leading_user_in_key():
    """A leading '用户' the LLM put in the key is stripped, not doubled."""
    calls = {"n": 0}

    def mock_generate(sys_prompt, user_msg):
        calls["n"] += 1
        if calls["n"] == 1:
            return json.dumps({"用户数据库": "PostgreSQL"})
        return json.dumps({"category": "fact", "tags": [], "slot_hint": ""})

    ltm = _MockLTM()
    asyncio.run(extract_memory_from_reply(mock_generate, None, ltm, "数据库选型"))
    assert ltm.stored[0]["content"] == "数据库: PostgreSQL"


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


# ─── Task 8.2: end-to-end conversation verification ───────────────────────────


@pytest.mark.asyncio
async def test_e2e_conversation_memory_quality():
    """Simulate one conversation turn and assert the three quality invariants:

    1. Preference table has no oversized garbage (length cap holds).
    2. LTM importance is graded by category, not hardcoded 0.7.
    3. The static profile prompt is byte-identical across two assembly runs.
    """
    pref = Preference(user_id="default_user")

    # DB is mocked away; set() still updates the in-memory cache.
    with patch("app.memory.preference.get_db") as mock_db:
        mock_db.side_effect = Exception("no db")

        # Step 1: user message → LLM preference overlay → save_batch.
        # The LLM returns a clean, precise value for 喜好 even though the raw
        # message contained a long conversation fragment.
        long_fragment = "唱跳rap" + "而且" * 400  # well over the 200 cap
        user_msg = f"我叫涵涵，我喜欢{long_fragment}"

        def overlay_generate(sys_prompt, user_msg_inner):
            # LLM提炼出精准值，覆盖规则提取的粗糙长串
            return json.dumps({"姓名": "涵涵", "喜好": "唱跳rap"})

        prefs = await extract_preferences(overlay_generate, user_msg)
        await pref.save_batch(prefs)

        # Step 2: assistant reply → memory extraction with category routing.
        # "姓名" (identity rule) → preference only; "项目数据库" (llm→fact) → LTM only.
        ltm = _MockLTM()

        def extract_generate(sys_prompt, user_msg_inner):
            if "分类" in sys_prompt:
                # classify call for the non-rule key → fact
                return json.dumps({"category": "fact", "tags": [], "slot_hint": ""})
            return json.dumps({"姓名": "涵涵", "项目数据库": "PostgreSQL"})

        await extract_memory_from_reply(
            extract_generate, None, ltm, "好的，记住了", preference=pref,
        )

    # Invariant 1: no preference value exceeds the 200-char cap.
    for k, v in pref.get_all().items():
        assert len(v) <= 200, f"preference {k} exceeds cap: {len(v)}"
        assert "而且" not in v  # garbage fragment didn't leak through

    # The LLM overlay value for 喜好 won over the rule-extracted long fragment.
    assert pref.get("喜好") == "唱跳rap"
    assert pref.get("姓名") == "涵涵"

    # Invariant 2: category routing — identity went to preference (not LTM),
    # the fact went to LTM with category-graded importance.
    assert pref.get("姓名") == "涵涵"
    assert all(row["category"] != "identity" for row in ltm.stored)
    by_cat = {row["category"]: row for row in ltm.stored}
    assert by_cat["fact"]["importance"] == _IMPORTANCE_BY_CATEGORY["fact"]
    assert by_cat["fact"]["importance"] != 0.7
    # fact_content is a plain "key: value" with no misleading 用户 prefix.
    assert by_cat["fact"]["content"] == "项目数据库: PostgreSQL"

    # Invariant 3: dynamic profile prompt is byte-stable across two runs.
    # Profile is now static=False, so content is in render_dynamic(), not render_static().
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
    assert "已有的偏好 key" in captured["sys_prompt"]


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
    assert "已有的偏好 key" not in captured["sys_prompt"]


# ─── optimize-preference-system: Task 7.8 (_consolidate_preferences) ────────


@pytest.mark.asyncio
async def test_7_8_consolidate_preferences_merges_duplicates():
    """Task 7.8: _consolidate_preferences() merges duplicate keys when count > threshold.

    Sets up a MemoryService with 16+ preferences (above threshold), mocks the
    LLM to return a merged dict with fewer keys, and verifies that removed
    keys are deleted from the in-memory cache.
    """
    from unittest.mock import MagicMock

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
         patch("app.services.agent_runner.get_db") as mock_get_db:
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
         patch("app.services.agent_runner.get_db") as mock_get_db:
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

