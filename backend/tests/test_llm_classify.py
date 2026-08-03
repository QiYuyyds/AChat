"""Integration tests for extract_ltm_memories (replaces old llm_classify tests).

The classification routing step was removed — all extracted LTM memories go
directly to LTM via store_classified without category-based routing.
These tests verify the new V3-format extraction flow.
"""

import asyncio
import json

from app.memory.memory_writer import extract_ltm_memories


class _MockLTM:
    """Minimal LTM stub recording store_classified() calls."""

    def __init__(self):
        self.stored = []

    async def store_classified(
        self, content, importance, emb, category, tags, slot_hint,
        scope="global", agent_id="", user_id=None,
    ):
        self.stored.append({
            "content": content,
            "importance": importance,
            "category": category,
            "tags": tags,
            "slot_hint": slot_hint,
            "scope": scope,
            "agent_id": agent_id,
        })
        return True


# ─── extract_ltm_memories: V3-format extraction ──────────────────────────────


def test_extract_ltm_memories_parses_v3_json():
    """V3-format JSON with multiple memories is parsed and stored correctly."""
    def mock_generate(sys_prompt, user_msg):
        return json.dumps({
            "memory": [
                {"id": "0", "text": "User's name is Alice", "attributed_to": "user"},
                {"id": "1", "text": "User is a software engineer", "attributed_to": "user"},
            ]
        })

    ltm = _MockLTM()
    asyncio.run(extract_ltm_memories(
        mock_generate, None, ltm, "我叫Alice，我是软件工程师", "好的，记住了",
    ))
    assert len(ltm.stored) == 2
    assert ltm.stored[0]["content"] == "User's name is Alice"
    assert ltm.stored[0]["tags"] == ["user"]
    assert ltm.stored[0]["category"] == ""
    assert ltm.stored[0]["importance"] == 0.5
    assert ltm.stored[1]["content"] == "User is a software engineer"
    assert ltm.stored[1]["tags"] == ["user"]


def test_extract_ltm_memories_strips_code_fence():
    """Code-fenced JSON is stripped and parsed."""
    def mock_generate(sys_prompt, user_msg):
        return '```json\n{"memory": [{"id": "0", "text": "User uses PostgreSQL", "attributed_to": "user"}]}\n```'

    ltm = _MockLTM()
    asyncio.run(extract_ltm_memories(
        mock_generate, None, ltm, "我们用PostgreSQL", "好的",
    ))
    assert len(ltm.stored) == 1
    assert ltm.stored[0]["content"] == "User uses PostgreSQL"


def test_extract_ltm_memories_empty_memory_list():
    """Empty memory list → no LTM items created (trivial conversation)."""
    def mock_generate(sys_prompt, user_msg):
        return json.dumps({"memory": []})

    ltm = _MockLTM()
    asyncio.run(extract_ltm_memories(
        mock_generate, None, ltm, "你好", "你好！有什么可以帮你的？",
    ))
    assert ltm.stored == []


def test_extract_ltm_memories_invalid_json_returns_early():
    """Non-JSON LLM output → no items stored, no exception raised."""
    def mock_generate(sys_prompt, user_msg):
        return "I cannot extract memories from this."

    ltm = _MockLTM()
    asyncio.run(extract_ltm_memories(
        mock_generate, None, ltm, "some message", "some reply",
    ))
    assert ltm.stored == []


def test_extract_ltm_memories_no_generate_fn_returns_early():
    """No generate_fn → returns immediately, no LTM items."""
    ltm = _MockLTM()
    asyncio.run(extract_ltm_memories(
        None, None, ltm, "some message", "some reply",
    ))
    assert ltm.stored == []


def test_extract_ltm_memories_both_messages_empty_returns_early():
    """Both user and assistant messages empty → returns immediately."""
    def mock_generate(sys_prompt, user_msg):
        return json.dumps({"memory": []})

    ltm = _MockLTM()
    asyncio.run(extract_ltm_memories(
        mock_generate, None, ltm, "", "",
    ))
    assert ltm.stored == []


def test_extract_ltm_memories_attributed_to_assistant():
    """Memories attributed to assistant get 'assistant' tag."""
    def mock_generate(sys_prompt, user_msg):
        return json.dumps({
            "memory": [
                {"id": "0", "text": "Recommended using FastAPI for the API", "attributed_to": "assistant"},
            ]
        })

    ltm = _MockLTM()
    asyncio.run(extract_ltm_memories(
        mock_generate, None, ltm, "用什么框架", "建议用FastAPI",
    ))
    assert len(ltm.stored) == 1
    assert ltm.stored[0]["tags"] == ["assistant"]


def test_extract_ltm_memories_default_attributed_to_user():
    """When attributed_to is missing, defaults to 'user'."""
    def mock_generate(sys_prompt, user_msg):
        return json.dumps({
            "memory": [
                {"id": "0", "text": "User likes hiking"},
            ]
        })

    ltm = _MockLTM()
    asyncio.run(extract_ltm_memories(
        mock_generate, None, ltm, "I like hiking", "Cool!",
    ))
    assert ltm.stored[0]["tags"] == ["user"]


def test_extract_ltm_memories_with_existing_keys():
    """existing_keys are included in the system prompt passed to generate_fn."""
    captured = {}

    def mock_generate(sys_prompt, user_msg):
        captured["sys_prompt"] = sys_prompt
        return json.dumps({"memory": []})

    ltm = _MockLTM()
    asyncio.run(extract_ltm_memories(
        mock_generate, None, ltm, "message", "reply",
        existing_keys=["姓名", "职业"],
    ))
    assert "姓名" in captured["sys_prompt"]
    assert "职业" in captured["sys_prompt"]


def test_extract_ltm_memories_agent_scope():
    """When agent_id is set, memories are written with scope='agent'."""
    def mock_generate(sys_prompt, user_msg):
        return json.dumps({
            "memory": [
                {"id": "0", "text": "User uses React 19", "attributed_to": "user"},
            ]
        })

    ltm = _MockLTM()
    asyncio.run(extract_ltm_memories(
        mock_generate, None, ltm, "我们用React 19", "好的",
        agent_id="agent_123",
    ))
    assert ltm.stored[0]["scope"] == "agent"
    assert ltm.stored[0]["agent_id"] == "agent_123"
