"""Unit tests for llm_classify_memory LLM fallback classification."""

import asyncio
import json

import pytest

from app.memory.memory_writer import (
    classify_memory_content,
    extract_memory_from_reply,
    llm_classify_memory,
)


# ─── llm_classify_memory direct tests ─────────────────────────────────────────


def test_llm_classify_valid_json():
    """LLM returns valid JSON with all fields."""
    def mock_generate(sys_prompt, user_msg):
        return json.dumps({
            "category": "episodic",
            "tags": ["event", "meeting"],
            "slot_hint": "recall_memory",
        })

    result = asyncio.run(llm_classify_memory(mock_generate, "昨天开了一个重要会议"))
    assert result == ("episodic", ["event", "meeting"], "recall_memory")


def test_llm_classify_code_fenced_json():
    """LLM returns code-fenced JSON; should be stripped and parsed."""
    def mock_generate(sys_prompt, user_msg):
        return '```json\n{"category":"fact","tags":["architecture"],"slot_hint":"recall_memory"}\n```'

    result = asyncio.run(llm_classify_memory(mock_generate, "项目使用微服务架构"))
    assert result == ("fact", ["architecture"], "recall_memory")


def test_llm_classify_invalid_json_fallback():
    """LLM returns non-JSON; should fall back to ("general", [], "")."""
    def mock_generate(sys_prompt, user_msg):
        return "I cannot classify this."

    result = asyncio.run(llm_classify_memory(mock_generate, "some content"))
    assert result == ("general", [], "")


def test_llm_classify_invalid_category_fallback():
    """LLM returns invalid category; should fall back to 'general'."""
    def mock_generate(sys_prompt, user_msg):
        return json.dumps({"category": "invalid_cat", "tags": ["t"], "slot_hint": "profile"})

    result = asyncio.run(llm_classify_memory(mock_generate, "content"))
    assert result[0] == "general"
    assert result[1] == ["t"]
    assert result[2] == ""  # invalid slot_hint should be empty


def test_llm_classify_no_generate_fn():
    """No generate_fn returns default."""
    result = asyncio.run(llm_classify_memory(None, "content"))
    assert result == ("general", [], "")


def test_llm_classify_empty_content():
    """Empty content returns default."""
    def mock_generate(sys_prompt, user_msg):
        return json.dumps({"category": "fact"})

    result = asyncio.run(llm_classify_memory(mock_generate, ""))
    assert result == ("general", [], "")


def test_llm_classify_llm_exception():
    """LLM call raises exception; should fall back gracefully."""
    def mock_generate(sys_prompt, user_msg):
        raise RuntimeError("API error")

    result = asyncio.run(llm_classify_memory(mock_generate, "content"))
    assert result == ("general", [], "")


# ─── Integration with extract_memory_from_reply ──────────────────────────────


class _MockLTM:
    """Minimal LTM stub for extract_memory_from_reply."""

    def __init__(self):
        self.stored = []

    async def store_classified(self, content, importance, emb, category, tags, slot_hint):
        self.stored.append({
            "content": content,
            "category": category,
            "tags": tags,
            "slot_hint": slot_hint,
        })
        return True


def test_rule_match_does_not_call_llm():
    """When rule classification hits, LLM is NOT called for classification."""
    llm_called = {"count": 0}

    def mock_generate(sys_prompt, user_msg):
        llm_called["count"] += 1
        # First call: extraction returns a key-value that matches a rule
        if llm_called["count"] == 1:
            return json.dumps({"名字": "Alice"})
        # Should never reach here for classification
        return json.dumps({"category": "episodic"})

    ltm = _MockLTM()
    asyncio.run(extract_memory_from_reply(mock_generate, None, ltm, "用户叫Alice"))
    assert len(ltm.stored) == 1
    assert ltm.stored[0]["category"] == "identity"
    # LLM should be called exactly once (extraction), NOT for classification
    assert llm_called["count"] == 1


def test_rule_miss_calls_llm_fallback():
    """When rule classification misses, LLM fallback is called."""
    call_count = {"count": 0}

    def mock_generate(sys_prompt, user_msg):
        call_count["count"] += 1
        if call_count["count"] == 1:
            # Extraction: returns a fact that doesn't match any rule
            return json.dumps({"项目架构": "微服务"})
        # Second call: LLM classification
        return json.dumps({"category": "fact", "tags": ["architecture"], "slot_hint": "recall_memory"})

    ltm = _MockLTM()
    asyncio.run(extract_memory_from_reply(mock_generate, None, ltm, "项目架构是微服务"))
    assert len(ltm.stored) == 1
    assert ltm.stored[0]["category"] == "fact"
    assert ltm.stored[0]["slot_hint"] == "recall_memory"
    # LLM called twice: extraction + classification
    assert call_count["count"] == 2


def test_llm_parse_failure_falls_back_to_general():
    """When LLM classification returns non-JSON, content is stored as 'general'."""
    call_count = {"count": 0}

    def mock_generate(sys_prompt, user_msg):
        call_count["count"] += 1
        if call_count["count"] == 1:
            return json.dumps({"未知信息": "一些内容"})
        # Classification: non-JSON response
        return "I cannot classify this."

    ltm = _MockLTM()
    asyncio.run(extract_memory_from_reply(mock_generate, None, ltm, "一些未知内容"))
    assert len(ltm.stored) == 1
    assert ltm.stored[0]["category"] == "general"
