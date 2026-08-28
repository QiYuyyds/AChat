"""Unit tests for SessionMemory — incremental conversation summary layer.

Verifies:
- should_extract returns False when _generate_fn is None (degradation)
- should_extract returns False for short conversations (< 10K tokens)
- should_extract returns True when threshold reached (first extraction)
- should_extract checks incremental token/tool call thresholds
- should_extract defers during unresolved tool_use chain
- extract() creates a new session memory record (covers_up_to set)
- extract() updates existing record in-place (no duplicate)
- extract() degrades gracefully on LLM failure
- get() returns None when no session memory exists
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.memory.session_memory import (
    SessionMemory,
    SessionMemoryRecord,
    _at_natural_breakpoint,
    _count_tool_uses,
)


def _make_msg(msg_id, created_at, parts, role="agent", agent_id="ag1"):
    """Create a lightweight message mock matching the Message interface."""
    msg = MagicMock()
    msg.id = msg_id
    msg.created_at = created_at
    msg.role = role
    msg.agent_id = agent_id
    msg.parts_list = parts
    return msg


# ─── should_extract: degradation ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_should_extract_returns_false_without_generate_fn():
    """No _generate_fn → should_extract always returns False."""
    sm = SessionMemory(generate_fn=None)
    with patch.object(sm, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        with patch(
            "app.memory.session_memory._load_messages_since",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_load.return_value = [_make_msg("m1", 100, [{"type": "text", "content": "x" * 50000}])]
            assert await sm.should_extract("conv1") is False


# ─── should_extract: short conversation ───────────────────────────────────────


@pytest.mark.asyncio
async def test_should_extract_returns_false_for_short_conversation():
    """Total tokens < MINIMUM_TOKENS_TO_INIT → should_extract returns False."""
    sm = SessionMemory(generate_fn=lambda s, u: "summary")
    with patch.object(sm, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        with patch(
            "app.memory.session_memory._load_messages_since",
            new_callable=AsyncMock,
        ) as mock_load:
            # ~1000 tokens, well below 3000 (MINIMUM_TOKENS_TO_INIT)
            mock_load.return_value = [
                _make_msg("m1", 100, [{"type": "text", "content": "x" * 4000}])
            ]
            assert await sm.should_extract("conv1") is False


# ─── should_extract: first extraction at threshold ───────────────────────────


@pytest.mark.asyncio
async def test_should_extract_returns_true_at_threshold():
    """Total tokens >= MINIMUM_TOKENS_TO_INIT and no existing session memory → True."""
    sm = SessionMemory(generate_fn=lambda s, u: "summary")
    with patch.object(sm, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        with patch(
            "app.memory.session_memory._load_messages_since",
            new_callable=AsyncMock,
        ) as mock_load:
            # ~12000 tokens, above 3000 threshold (MINIMUM_TOKENS_TO_INIT)
            mock_load.return_value = [
                _make_msg("m1", 100, [{"type": "text", "content": "x" * 48000}])
            ]
            assert await sm.should_extract("conv1") is True


# ─── should_extract: incremental thresholds ───────────────────────────────────


@pytest.mark.asyncio
async def test_should_extract_returns_false_when_increment_too_small():
    """Existing session memory but incremental tokens < MINIMUM_TOKENS_BETWEEN_UPDATE → False."""
    sm = SessionMemory(generate_fn=lambda s, u: "summary")
    existing = SessionMemoryRecord(summary="x" * 48000, covers_up_to=100.0)
    with patch.object(sm, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = existing
        with patch(
            "app.memory.session_memory._load_messages_since",
            new_callable=AsyncMock,
        ) as mock_load:
            # Small increment after covers_up_to=100, ~1000 tokens
            mock_load.return_value = [
                _make_msg("m1", 50, [{"type": "text", "content": "x" * 48000}]),
                _make_msg("m2", 200, [{"type": "text", "content": "x" * 4000}]),
            ]
            assert await sm.should_extract("conv1") is False


@pytest.mark.asyncio
async def test_should_extract_returns_true_when_tool_calls_threshold_met():
    """Existing session memory, tool calls >= TOOL_CALLS_BETWEEN_UPDATES → True."""
    sm = SessionMemory(generate_fn=lambda s, u: "summary")
    existing = SessionMemoryRecord(summary="existing summary", covers_up_to=100.0)
    with patch.object(sm, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = existing
        with patch(
            "app.memory.session_memory._load_messages_since",
            new_callable=AsyncMock,
        ) as mock_load:
            # 3 tool_use parts (>= TOOL_CALLS_BETWEEN_UPDATES = 2)
            mock_load.return_value = [
                _make_msg("m1", 50, [{"type": "text", "content": "x" * 48000}]),
                _make_msg("m2", 200, [
                    {"type": "tool_use", "callId": "c1", "toolName": "bash"},
                    {"type": "tool_use", "callId": "c2", "toolName": "fs_read"},
                    {"type": "tool_use", "callId": "c3", "toolName": "fs_write"},
                ]),
                _make_msg("m3", 300, [
                    {"type": "tool_result", "callId": "c1", "result": "ok", "isError": False},
                    {"type": "tool_result", "callId": "c2", "result": "ok", "isError": False},
                    {"type": "tool_result", "callId": "c3", "result": "ok", "isError": False},
                ]),
            ]
            assert await sm.should_extract("conv1") is True


# ─── should_extract: natural breakpoint ───────────────────────────────────────


@pytest.mark.asyncio
async def test_should_extract_defers_during_tool_chain():
    """Last message has unresolved tool_use → should_extract returns False."""
    sm = SessionMemory(generate_fn=lambda s, u: "summary")
    with patch.object(sm, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        with patch(
            "app.memory.session_memory._load_messages_since",
            new_callable=AsyncMock,
        ) as mock_load:
            # Last message has tool_use without matching tool_result
            mock_load.return_value = [
                _make_msg("m1", 100, [{"type": "text", "content": "x" * 48000}]),
                _make_msg("m2", 200, [{"type": "tool_use", "callId": "c1", "toolName": "bash"}]),
            ]
            assert await sm.should_extract("conv1") is False


# ─── _at_natural_breakpoint ──────────────────────────────────────────────────


def test_at_natural_breakpoint_no_tool_use():
    """No tool_use in last message → True (natural breakpoint)."""
    msgs = [_make_msg("m1", 100, [{"type": "text", "content": "hello"}])]
    assert _at_natural_breakpoint(msgs) is True


def test_at_natural_breakpoint_resolved_tool_use():
    """Last message has tool_use with matching tool_result → True."""
    msgs = [
        _make_msg("m1", 100, [{"type": "tool_use", "callId": "c1", "toolName": "bash"}]),
        _make_msg("m2", 200, [{"type": "tool_result", "callId": "c1", "result": "ok", "isError": False}]),
    ]
    assert _at_natural_breakpoint(msgs) is True


def test_at_natural_breakpoint_unresolved_tool_use():
    """Last message has tool_use without matching tool_result → False."""
    msgs = [
        _make_msg("m1", 100, [{"type": "text", "content": "hello"}]),
        _make_msg("m2", 200, [{"type": "tool_use", "callId": "c1", "toolName": "bash"}]),
    ]
    assert _at_natural_breakpoint(msgs) is False


# ─── _count_tool_uses ────────────────────────────────────────────────────────


def test_count_tool_uses():
    """Count tool_use parts across messages."""
    msgs = [
        _make_msg("m1", 100, [{"type": "tool_use", "callId": "c1"}, {"type": "text", "content": "hi"}]),
        _make_msg("m2", 200, [{"type": "tool_use", "callId": "c2"}, {"type": "tool_use", "callId": "c3"}]),
    ]
    assert _count_tool_uses(msgs) == 3


def test_count_tool_uses_empty():
    """No tool_use parts → 0."""
    msgs = [_make_msg("m1", 100, [{"type": "text", "content": "hello"}])]
    assert _count_tool_uses(msgs) == 0


# ─── get: returns None when no session memory ────────────────────────────────


@pytest.mark.asyncio
async def test_get_returns_none_when_no_session_memory(db):
    """No session memory record in DB → get() returns None."""
    from app.db.engine import get_db

    # Create a conversation first
    from app.db.models import Conversation
    from app.utils.clock import now_ms

    async with get_db() as session:
        conv = Conversation(
            id="conv_test_get",
            title="test",
            mode="single",
            agent_ids=[],
            pinned_message_ids=[],
            bookmarked_message_ids=[],
            archived=False,
            fs_write_approval_mode="review",
            rag_enabled=False,
            dispatch_mode="solo",
            created_at=now_ms(),
            updated_at=now_ms(),
        )
        session.add(conv)

    sm = SessionMemory()
    result = await sm.get("conv_test_get")
    assert result is None


# ─── get: returns session memory record ──────────────────────────────────────


@pytest.mark.asyncio
async def test_get_returns_session_memory_record(db):
    """Session memory record exists in DB → get() returns it."""
    from app.db.engine import get_db
    from app.db.models import ContextSummary, Conversation
    from app.utils.clock import now_ms

    now = now_ms()
    async with get_db() as session:
        conv = Conversation(
            id="conv_test_get2",
            title="test",
            mode="single",
            agent_ids=[],
            pinned_message_ids=[],
            bookmarked_message_ids=[],
            archived=False,
            fs_write_approval_mode="review",
            rag_enabled=False,
            dispatch_mode="solo",
            created_at=now,
            updated_at=now,
        )
        session.add(conv)

        sm_row = ContextSummary(
            id="cs_session_1",
            conversation_id="conv_test_get2",
            summary="test session summary",
            covered_until_message_id="session",
            covered_until_created_at=now,
            source_message_count=5,
            token_estimate=100,
            model_provider=None,
            model_id=None,
            summary_type="session",
            covers_up_to=200.0,
            created_at=now,
        )
        session.add(sm_row)

    sm = SessionMemory()
    result = await sm.get("conv_test_get2")
    assert result is not None
    assert result.summary == "test session summary"
    assert result.covers_up_to == 200.0


# ─── extract: creates new record ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_extract_creates_new_record(db):
    """extract() creates a new session memory record when none exists."""
    from app.db.engine import get_db
    from app.db.models import Conversation, Message
    from app.utils.clock import now_ms

    now = now_ms()
    conv_id = "conv_test_extract_new"

    async with get_db() as session:
        conv = Conversation(
            id=conv_id,
            title="test",
            mode="single",
            agent_ids=[],
            pinned_message_ids=[],
            bookmarked_message_ids=[],
            archived=False,
            fs_write_approval_mode="review",
            rag_enabled=False,
            dispatch_mode="solo",
            created_at=now,
            updated_at=now,
        )
        session.add(conv)

        msg = Message(
            id="msg_extract_1",
            conversation_id=conv_id,
            role="user",
            agent_id=None,
            status="complete",
            parent_message_id=None,
            mentioned_agent_ids=[],
            run_id=None,
            created_at=now,
        )
        msg.parts_list = [{"type": "text", "content": "hello world"}]
        session.add(msg)

    def mock_generate(system_prompt, user_msg):
        return """```yaml
title: new session
current_state: started
key_decisions: []
files_touched: []
commands_run: []
artifacts_produced: []
blockers: []
open_questions: []
next_steps: []
architecture_understanding: ""
```"""

    sm = SessionMemory(generate_fn=mock_generate)
    await sm.extract(conv_id)

    result = await sm.get(conv_id)
    assert result is not None
    assert result.summary is not None
    assert result.covers_up_to is not None


# ─── extract: updates existing record ────────────────────────────────────────


@pytest.mark.asyncio
async def test_extract_updates_existing_record(db):
    """extract() updates the existing session memory record in-place."""
    from sqlalchemy import select

    from app.db.engine import get_db
    from app.db.models import ContextSummary, Conversation, Message
    from app.utils.clock import now_ms

    now = now_ms()
    conv_id = "conv_test_extract_update"

    async with get_db() as session:
        conv = Conversation(
            id=conv_id,
            title="test",
            mode="single",
            agent_ids=[],
            pinned_message_ids=[],
            bookmarked_message_ids=[],
            archived=False,
            fs_write_approval_mode="review",
            rag_enabled=False,
            dispatch_mode="solo",
            created_at=now,
            updated_at=now,
        )
        session.add(conv)

        existing = ContextSummary(
            id="cs_session_existing",
            conversation_id=conv_id,
            summary="old summary",
            covered_until_message_id="session",
            covered_until_created_at=now - 1000,
            source_message_count=3,
            token_estimate=50,
            model_provider=None,
            model_id=None,
            summary_type="session",
            covers_up_to=float(now - 1000),
            created_at=now - 1000,
        )
        session.add(existing)

        msg = Message(
            id="msg_extract_2",
            conversation_id=conv_id,
            role="agent",
            agent_id=None,
            status="complete",
            parent_message_id=None,
            mentioned_agent_ids=[],
            run_id=None,
            created_at=now,
        )
        msg.parts_list = [{"type": "text", "content": "new content"}]
        session.add(msg)

    def mock_generate(system_prompt, user_msg):
        return """```yaml
title: updated session
current_state: updated
key_decisions: []
files_touched: []
commands_run: []
artifacts_produced: []
blockers: []
open_questions: []
next_steps: []
architecture_understanding: ""
```"""

    sm = SessionMemory(generate_fn=mock_generate)
    await sm.extract(conv_id)

    # Verify only one session record exists (updated in-place)
    async with get_db() as session:
        result = await session.execute(
            select(ContextSummary).where(
                ContextSummary.conversation_id == conv_id,
                ContextSummary.summary_type == "session",
            )
        )
        rows = result.scalars().all()
        assert len(rows) == 1
        assert "updated session" in rows[0].summary


# ─── extract: degradation on LLM failure ─────────────────────────────────────


@pytest.mark.asyncio
async def test_extract_degrades_on_llm_failure(db):
    """extract() silently skips when LLM raises an exception."""
    from app.db.engine import get_db
    from app.db.models import Conversation, Message
    from app.utils.clock import now_ms

    now = now_ms()
    conv_id = "conv_test_extract_fail"

    async with get_db() as session:
        conv = Conversation(
            id=conv_id,
            title="test",
            mode="single",
            agent_ids=[],
            pinned_message_ids=[],
            bookmarked_message_ids=[],
            archived=False,
            fs_write_approval_mode="review",
            rag_enabled=False,
            dispatch_mode="solo",
            created_at=now,
            updated_at=now,
        )
        session.add(conv)

        msg = Message(
            id="msg_extract_fail",
            conversation_id=conv_id,
            role="user",
            agent_id=None,
            status="complete",
            parent_message_id=None,
            mentioned_agent_ids=[],
            run_id=None,
            created_at=now,
        )
        msg.parts_list = [{"type": "text", "content": "hello"}]
        session.add(msg)

    def failing_generate(system_prompt, user_msg):
        raise RuntimeError("LLM API error")

    sm = SessionMemory(generate_fn=failing_generate)
    # Should not raise
    await sm.extract(conv_id)

    # No session memory should have been created
    result = await sm.get(conv_id)
    assert result is None


# ─── extract: degradation without generate_fn ────────────────────────────────


@pytest.mark.asyncio
async def test_extract_skips_without_generate_fn():
    """extract() is a no-op when _generate_fn is None."""
    sm = SessionMemory(generate_fn=None)
    # Should not raise, should not attempt any DB operation
    await sm.extract("conv_nonexistent")


# ─── extract: empty summary from LLM ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_extract_skips_on_empty_llm_response(db):
    """extract() skips when LLM returns empty string."""
    from app.db.engine import get_db
    from app.db.models import Conversation, Message
    from app.utils.clock import now_ms

    now = now_ms()
    conv_id = "conv_test_extract_empty"

    async with get_db() as session:
        conv = Conversation(
            id=conv_id,
            title="test",
            mode="single",
            agent_ids=[],
            pinned_message_ids=[],
            bookmarked_message_ids=[],
            archived=False,
            fs_write_approval_mode="review",
            rag_enabled=False,
            dispatch_mode="solo",
            created_at=now,
            updated_at=now,
        )
        session.add(conv)

        msg = Message(
            id="msg_extract_empty",
            conversation_id=conv_id,
            role="user",
            agent_id=None,
            status="complete",
            parent_message_id=None,
            mentioned_agent_ids=[],
            run_id=None,
            created_at=now,
        )
        msg.parts_list = [{"type": "text", "content": "hello"}]
        session.add(msg)

    sm = SessionMemory(generate_fn=lambda s, u: "")
    await sm.extract(conv_id)

    result = await sm.get(conv_id)
    assert result is None


# ─── tool-aware transcript integration ───────────────────────────────────────


@pytest.mark.asyncio
async def test_should_extract_uses_full_token_estimate():
    """should_extract counts tool_result tokens, not just text.

    A conversation with small text but large tool_result should trigger
    extraction — the legacy text-only estimate would have missed it.
    """
    sm = SessionMemory(generate_fn=lambda s, u: "summary")
    with patch.object(sm, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        with patch(
            "app.memory.session_memory._load_messages_since",
            new_callable=AsyncMock,
        ) as mock_load:
            # Text is only ~1000 tokens (below 10000 threshold),
            # but tool_result adds ~12500 tokens (above threshold).
            mock_load.return_value = [
                _make_msg("m1", 100, [
                    {"type": "text", "content": "x" * 4000},  # ~1000 tokens
                    {"type": "tool_use", "callId": "c1", "toolName": "fs_read", "args": {"path": "big.ts"}},
                    {"type": "tool_result", "callId": "c1", "result": "y" * 50000, "isError": False},  # ~12500 tokens
                ]),
            ]
            assert await sm.should_extract("conv1") is True


@pytest.mark.asyncio
async def test_extract_transcript_contains_tool_info():
    """extract() passes a tool-aware transcript to the generate_fn.

    The user_msg should contain ↳ tool_use: and ↳ tool_result: lines
    when the conversation includes tool calls.
    """
    captured_args: dict = {}

    def capturing_generate(system_prompt: str, user_msg: str) -> str:
        captured_args["system_prompt"] = system_prompt
        captured_args["user_msg"] = user_msg
        return "summary with tool info"

    sm = SessionMemory(generate_fn=capturing_generate)
    with patch.object(sm, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        with patch(
            "app.memory.session_memory._load_messages_since",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_load.return_value = [
                _make_msg("m1", 100, [
                    {"type": "text", "content": "让我看看项目结构"},
                    {"type": "tool_use", "callId": "c1", "toolName": "fs_list", "args": {"path": "src", "depth": 3}},
                    {"type": "tool_result", "callId": "c1", "result": [{"name": "index.ts", "relativePath": "src/index.ts", "isDirectory": False}], "isError": False},
                ]),
            ]
            with patch.object(sm, "_upsert", new_callable=AsyncMock):
                await sm.extract("conv1")

    assert "↳ tool_use: fs_list" in captured_args.get("user_msg", "")
    assert "↳ tool_result: [fs_list]" in captured_args.get("user_msg", "")


# ─── extract: produces YAML from LLM output ──────────────────────────────


_VALID_YAML_OUTPUT = """```yaml
title: 优化压缩系统
current_state: 正在讨论结构化 Session Note
key_decisions:
  - "[14:32] 用 Milvus BM25"
files_touched:
  - "backend/app/memory/session_note.py (已改)"
commands_run: []
artifacts_produced: []
blockers: []
open_questions: []
next_steps:
  - "实现 to_xml"
architecture_understanding: ""
```"""


@pytest.mark.asyncio
async def test_extract_produces_yaml():
    """extract() with valid YAML output → summary is YAML format (SessionNote)."""
    sm = SessionMemory(generate_fn=lambda s, u: _VALID_YAML_OUTPUT)
    with patch.object(sm, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        with patch(
            "app.memory.session_memory._load_messages_since",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_load.return_value = [
                _make_msg("m1", 100, [{"type": "text", "content": "hello"}]),
            ]
            with patch.object(sm, "_upsert", new_callable=AsyncMock) as mock_upsert:
                await sm.extract("conv1")

    assert mock_upsert.called
    call_args = mock_upsert.call_args
    summary = call_args[0][1]
    assert "title:" in summary
    assert "优化压缩系统" in summary
    assert "key_decisions:" in summary

    from app.memory.session_note import SessionNote
    parsed = SessionNote.from_yaml(summary)
    assert parsed is not None
    assert parsed.title == "优化压缩系统"
    assert parsed.key_decisions == ["[14:32] 用 Milvus BM25"]


@pytest.mark.asyncio
async def test_extract_fallback_on_yaml_failure():
    """extract() with non-YAML output → existing note not updated."""
    existing = SessionMemoryRecord(summary="existing yaml content", covers_up_to=100.0)
    sm = SessionMemory(generate_fn=lambda s, u: "This is just plain text, not YAML.")
    with patch.object(sm, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = existing
        with patch(
            "app.memory.session_memory._load_messages_since",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_load.return_value = [
                _make_msg("m1", 200, [{"type": "text", "content": "new stuff"}]),
            ]
            with patch.object(sm, "_upsert", new_callable=AsyncMock) as mock_upsert:
                await sm.extract("conv1")

    assert not mock_upsert.called


@pytest.mark.asyncio
async def test_extract_merges_contradictory_decisions():
    """extract() with merge YAML → old decision marked [已更新]."""
    existing_yaml = """```yaml
title: 数据库选型
current_state: 讨论中
key_decisions:
  - "[14:32] 使用 PostgreSQL"
files_touched: []
commands_run: []
artifacts_produced: []
blockers: []
open_questions: []
next_steps: []
architecture_understanding: ""
```"""

    merged_yaml = """```yaml
title: 数据库选型
current_state: 改用 SQLite
key_decisions:
  - "[14:32] [已更新] 使用 PostgreSQL"
  - "[15:07] 改用 SQLite"
files_touched: []
commands_run: []
artifacts_produced: []
blockers: []
open_questions: []
next_steps: []
architecture_understanding: ""
```"""

    existing = SessionMemoryRecord(summary=existing_yaml, covers_up_to=100.0)
    sm = SessionMemory(generate_fn=lambda s, u: merged_yaml)
    with patch.object(sm, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = existing
        with patch(
            "app.memory.session_memory._load_messages_since",
            new_callable=AsyncMock,
        ) as mock_load:
            mock_load.return_value = [
                _make_msg("m1", 200, [{"type": "text", "content": "改用 SQLite"}]),
            ]
            with patch.object(sm, "_upsert", new_callable=AsyncMock) as mock_upsert:
                await sm.extract("conv1")

    assert mock_upsert.called
    call_args = mock_upsert.call_args
    summary = call_args[0][1]

    from app.memory.session_note import SessionNote
    parsed = SessionNote.from_yaml(summary)
    assert parsed is not None
    assert any("[已更新]" in d for d in parsed.key_decisions)
    assert any("SQLite" in d for d in parsed.key_decisions)
