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

    async def store_classified(self, content, importance, emb, category, tags, slot_hint):
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


# ─── Task 4.4: double-write to preference + LTM ──────────────────────────────


def test_double_write_preference_and_ltm():
    """LLM-extracted k-v is mirrored into preference AND LTM."""
    def mock_generate(sys_prompt, user_msg):
        return json.dumps({"视觉风格": "editorial/magazine"})

    ltm = _MockLTM()
    pref = _MockPreference()
    asyncio.run(extract_memory_from_reply(
        mock_generate, None, ltm, "用户喜欢editorial风格", preference=pref,
    ))
    # Preference double-write
    assert ("视觉风格", "editorial/magazine") in pref.set_calls
    # LTM write
    assert len(ltm.stored) == 1
    assert "视觉风格" in ltm.stored[0]["content"]


def test_preference_set_failure_does_not_block_ltm():
    """If preference.set raises, LTM store still proceeds."""
    class _FailingPref:
        async def set(self, key, value):
            raise RuntimeError("pref store down")

    def mock_generate(sys_prompt, user_msg):
        return json.dumps({"字体": "Noto Serif SC"})

    ltm = _MockLTM()
    asyncio.run(extract_memory_from_reply(
        mock_generate, None, ltm, "字体", preference=_FailingPref(),
    ))
    assert len(ltm.stored) == 1
    assert "字体" in ltm.stored[0]["content"]


def test_no_preference_param_still_writes_ltm():
    """Omitting preference (back-compat) still stores to LTM only."""
    def mock_generate(sys_prompt, user_msg):
        return json.dumps({"项目": "AChat"})

    ltm = _MockLTM()
    asyncio.run(extract_memory_from_reply(mock_generate, None, ltm, "项目"))
    assert len(ltm.stored) == 1


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


def test_importance_identity_via_rule():
    """An identity-classified fact (rule match on 姓名) gets importance 0.9."""
    def mock_generate(sys_prompt, user_msg):
        return json.dumps({"姓名": "涵涵"})

    ltm = _MockLTM()
    asyncio.run(extract_memory_from_reply(mock_generate, None, ltm, "用户叫涵涵"))
    assert ltm.stored[0]["category"] == "identity"
    assert ltm.stored[0]["importance"] == 0.9


def test_importance_general_via_llm_fallback():
    """A fact classified as 'general' by LLM fallback gets importance 0.3."""
    call_count = {"n": 0}

    def mock_generate(sys_prompt, user_msg):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return json.dumps({"天气": "今天晴天"})
        return json.dumps({"category": "general", "tags": [], "slot_hint": ""})

    ltm = _MockLTM()
    asyncio.run(extract_memory_from_reply(mock_generate, None, ltm, "今天晴天"))
    assert ltm.stored[0]["category"] == "general"
    assert ltm.stored[0]["importance"] == 0.3


# ─── Task 6.2: fact_content double-prefix dedup ──────────────────────────────


def test_fact_content_strips_existing_prefix():
    """LLM key already containing '用户' is not double-prefixed."""
    def mock_generate(sys_prompt, user_msg):
        return json.dumps({"用户名称": "涵涵"})

    ltm = _MockLTM()
    asyncio.run(extract_memory_from_reply(mock_generate, None, ltm, "用户叫涵涵"))
    assert ltm.stored[0]["content"] == "用户名称: 涵涵"


def test_fact_content_adds_prefix_when_missing():
    """LLM key without '用户' prefix gets it added normally."""
    def mock_generate(sys_prompt, user_msg):
        return json.dumps({"字体": "Noto Serif SC"})

    ltm = _MockLTM()
    asyncio.run(extract_memory_from_reply(mock_generate, None, ltm, "字体设置"))
    assert ltm.stored[0]["content"] == "用户字体: Noto Serif SC"


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

        # Step 2: assistant reply → memory extraction (double-write to pref + LTM).
        ltm = _MockLTM()

        def extract_generate(sys_prompt, user_msg_inner):
            # "姓名" matches the identity rule; fact_content becomes "用户姓名: 涵涵".
            return json.dumps({"姓名": "涵涵", "视觉风格": "editorial"})

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

    # Invariant 2: LTM importance is graded by category, not hardcoded 0.7.
    by_cat = {row["category"]: row for row in ltm.stored}
    # "姓名: 涵涵" matches the identity rule → importance 0.9.
    assert by_cat["identity"]["importance"] == _IMPORTANCE_BY_CATEGORY["identity"]
    assert by_cat["identity"]["importance"] != 0.7
    # fact_content added the "用户" prefix once (no double-prefix).
    assert by_cat["identity"]["content"] == "用户姓名: 涵涵"

    # Invariant 3: static profile prompt is byte-stable across two runs.
    registry = SourceRegistry()
    registry.register(ProfileSource(preference_provider=pref, ltm=None))
    assembler = ContextAssembler(registry=registry)
    rc1 = await assembler.assemble(Query(text="hello", mode="chat"))
    rc2 = await assembler.assemble(Query(text="hello", mode="chat"))
    assert rc1.render_static() == rc2.render_static()
    assert "涵涵" in rc1.render_static()

