"""Tests for persist_event in dual-DB mode.

Covers:
- message.start writes directly to local SQLite (not Redis Stream)
- message.end updates local SQLite
- part events write to local SQLite via _persist_or_stream
- usage events are fire-and-forget (asyncio.create_task) and write to local
- No Redis Stream involvement
"""

import asyncio
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.db.models import Agent, AgentRun, Conversation, Message
from app.schemas.events import (
    MessageEndEvent,
    MessageStartEvent,
    RunUsageEvent,
)
from app.schemas.messages import RunUsage
from app.services.agent_runner import (
    _persist_or_stream,
    persist_event,
)
from app.utils.clock import now_ms


@pytest_asyncio.fixture
async def dual_db(tmp_path, monkeypatch):
    """Dual-DB: local SQLite + remote SQLite."""
    local_db = tmp_path / "local.db"
    remote_db = tmp_path / "remote.db"

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{remote_db.as_posix()}")
    monkeypatch.setenv("DATABASE_LOCAL_URL", f"sqlite+aiosqlite:///{local_db.as_posix()}")
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "ws"))
    monkeypatch.setenv("JWT_SECRET", "test-secret-at-least-32-characters-long!!")

    from app.config import get_settings

    get_settings.cache_clear()
    from app.db import engine as engine_mod

    await engine_mod.init_db()
    try:
        yield engine_mod
    finally:
        await engine_mod.close_db()
        get_settings.cache_clear()


async def _seed(dual_db, conv_id="conv1", agent_id="ag1", run_id="run1"):
    """Seed conversation + agent + run for persist_event tests."""
    now = now_ms()
    async with dual_db.get_local_db() as session:
        conv = Conversation(
            id=conv_id, user_id="u1", title="T", mode="single",
            created_at=now, updated_at=now,
        )
        conv.agent_ids_list = []
        conv.pinned_message_ids_list = []
        conv.bookmarked_message_ids_list = []
        session.add(conv)

        agent = Agent(
            id=agent_id, name="A", avatar="A", description="d",
            system_prompt="p", adapter_name="mock", is_builtin=False,
            is_orchestrator=False,
            created_at=now, user_id="u1",
        )
        agent.capabilities_list = []
        agent.tool_names_list = []
        session.add(agent)

        session.add(AgentRun(
            id=run_id, conversation_id=conv_id, agent_id=agent_id,
            status="running", started_at=now,
        ))


@pytest.mark.asyncio
async def test_message_start_writes_local(dual_db):
    """message.start event writes Message directly to local SQLite."""
    await _seed(dual_db)
    now = now_ms()

    event = MessageStartEvent(
        type="message.start",
        conversationId="conv1",
        messageId="msg1",
        agentId="ag1",
        runId="run1",
        timestamp=now,
    )

    parts_buffer: dict[str, list[dict]] = {}
    await persist_event(
        event, parts_buffer, "run1", "ag1",
        output_message_ids=[], artifact_ids=[],
    )

    # Verify the message is in the LOCAL database
    async with dual_db.get_local_db() as session:
        result = await session.execute(select(Message).where(Message.id == "msg1"))
        msg = result.scalar_one()
        assert msg.status == "streaming"
        assert msg.conversation_id == "conv1"
        assert msg.agent_id == "ag1"
        assert msg.run_id == "run1"

    # Verify the message table doesn't exist on the remote engine
    # (in dual-DB mode, messages is a local-only table)
    from sqlalchemy import inspect as sa_inspect
    async with dual_db._remote_engine.connect() as conn:
        tables = await conn.run_sync(
            lambda sync_conn: set(sa_inspect(sync_conn).get_table_names())
        )
    assert "messages" not in tables


@pytest.mark.asyncio
async def test_message_end_writes_local(dual_db):
    """message.end event updates Message status + parts on local SQLite."""
    await _seed(dual_db)

    # First create the message via message.start
    now = now_ms()
    start_event = MessageStartEvent(
        type="message.start",
        conversationId="conv1",
        messageId="msg2",
        agentId="ag1",
        runId="run1",
        timestamp=now,
    )
    parts_buffer: dict[str, list[dict]] = {}
    await persist_event(
        start_event, parts_buffer, "run1", "ag1",
        output_message_ids=[], artifact_ids=[],
    )

    # Add some parts to the buffer
    parts_buffer["msg2"] = [{"type": "text", "content": "Hello"}]

    # Send message.end
    end_event = MessageEndEvent(
        type="message.end",
        conversationId="conv1",
        messageId="msg2",
        agentId="ag1",
        timestamp=now_ms(),
    )
    await persist_event(
        end_event, parts_buffer, "run1", "ag1",
        output_message_ids=[], artifact_ids=[],
    )

    # Verify status updated to complete
    async with dual_db.get_local_db() as session:
        result = await session.execute(select(Message).where(Message.id == "msg2"))
        msg = result.scalar_one()
        assert msg.status == "complete"
        assert len(msg.parts) == 1
        assert msg.parts[0]["content"] == "Hello"


@pytest.mark.asyncio
async def test_persist_or_stream_writes_local(dual_db):
    """_persist_or_stream writes parts to local SQLite (not Redis Stream)."""
    await _seed(dual_db)
    now = now_ms()

    # Create the message first
    start_event = MessageStartEvent(
        type="message.start",
        conversationId="conv1",
        messageId="msg3",
        agentId="ag1",
        runId="run1",
        timestamp=now,
    )
    parts_buffer: dict[str, list[dict]] = {}
    await persist_event(
        start_event, parts_buffer, "run1", "ag1",
        output_message_ids=[], artifact_ids=[],
    )

    # Update parts via _persist_or_stream
    parts = [{"type": "text", "content": "Updated"}]
    await _persist_or_stream(None, "run1", start_event, parts, False)

    # Verify parts are in local DB
    async with dual_db.get_local_db() as session:
        result = await session.execute(select(Message).where(Message.id == "msg3"))
        msg = result.scalar_one()
        assert len(msg.parts) == 1
        assert msg.parts[0]["content"] == "Updated"


@pytest.mark.asyncio
async def test_run_usage_fire_and_forget(dual_db):
    """run.usage event is fire-and-forget and writes to local SQLite."""
    await _seed(dual_db)

    usage = RunUsage(
        model="test-model",
        inputTokens=100,
        outputTokens=50,
        cacheCreationTokens=0,
        cacheReadTokens=0,
    )

    event = RunUsageEvent(
        type="run.usage",
        conversationId="conv1",
        runId="run1",
        usage=usage,
        timestamp=now_ms(),
    )

    parts_buffer: dict[str, list[dict]] = {}
    await persist_event(
        event, parts_buffer, "run1", "ag1",
        output_message_ids=[], artifact_ids=[],
    )

    # The fire-and-forget task should update the run usage
    # Give it a moment to complete
    await asyncio.sleep(0.1)

    async with dual_db.get_local_db() as session:
        result = await session.execute(select(AgentRun).where(AgentRun.id == "run1"))
        run = result.scalar_one()
        assert run.usage is not None
        assert run.usage["inputTokens"] == 100
        assert run.usage["outputTokens"] == 50


@pytest.mark.asyncio
async def test_no_redis_stream_used(dual_db):
    """persist_event does not invoke any Redis Stream (XADD) operations."""
    await _seed(dual_db)
    now = now_ms()

    # Mock to detect if any Redis call is made
    with patch("app.services.agent_runner._persist_or_stream", wraps=_persist_or_stream) as mock_persist:
        event = MessageStartEvent(
            type="message.start",
            conversationId="conv1",
            messageId="msg4",
            agentId="ag1",
            runId="run1",
            timestamp=now,
        )
        parts_buffer: dict[str, list[dict]] = {}
        await persist_event(
            event, parts_buffer, "run1", "ag1",
            output_message_ids=[], artifact_ids=[],
        )

    # _persist_or_stream should NOT have been called for message.start
    # (message.start writes directly via get_local_db)
    mock_persist.assert_not_called()

    # Verify message was written to local DB
    async with dual_db.get_local_db() as session:
        result = await session.execute(select(Message).where(Message.id == "msg4"))
        assert result.scalar_one() is not None
