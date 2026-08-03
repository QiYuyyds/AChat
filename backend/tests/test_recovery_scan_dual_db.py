"""Tests for recovery_scan in dual-DB mode.

Covers:
- scan_interrupted_messages finds messages stuck in "streaming" status
- Stuck messages are marked as "interrupted" (using direct DB update)
- Recent streaming messages are NOT marked (below 5min threshold)
- Recovery uses local SQLite (get_local_db), not Redis Stream replay
"""

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.db.models import Conversation, Message
from app.services.recovery_scan import scan_interrupted_messages
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


async def _seed_conversation(dual_db, conv_id="conv1"):
    """Seed a conversation for message FK."""
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


@pytest.mark.asyncio
async def test_stuck_streaming_marked_interrupted(dual_db):
    """Messages stuck in streaming for >5min are marked interrupted."""
    await _seed_conversation(dual_db)

    # Create a message with streaming status, old timestamp (>5min ago)
    old_ts = now_ms() - (6 * 60 * 1000)  # 6 minutes ago
    async with dual_db.get_local_db() as session:
        msg = Message(
            id="msg_stuck",
            conversation_id="conv1",
            role="agent",
            status="streaming",
            created_at=old_ts,
        )
        msg.parts_list = []
        msg.mentioned_agent_ids_list = []
        session.add(msg)

    # Run recovery scan
    resolved = await scan_interrupted_messages()
    assert resolved == 1

    # Verify message is now interrupted
    async with dual_db.get_local_db() as session:
        result = await session.execute(select(Message).where(Message.id == "msg_stuck"))
        msg = result.scalar_one()
        assert msg.status == "interrupted"


@pytest.mark.asyncio
async def test_recent_streaming_not_marked(dual_db):
    """Messages streaming for <5min are NOT marked as interrupted."""
    await _seed_conversation(dual_db)

    # Create a message with streaming status, recent timestamp (1min ago)
    recent_ts = now_ms() - (1 * 60 * 1000)  # 1 minute ago
    async with dual_db.get_local_db() as session:
        msg = Message(
            id="msg_recent",
            conversation_id="conv1",
            role="agent",
            status="streaming",
            created_at=recent_ts,
        )
        msg.parts_list = []
        msg.mentioned_agent_ids_list = []
        session.add(msg)

    # Run recovery scan
    resolved = await scan_interrupted_messages()
    assert resolved == 0

    # Verify message is still streaming
    async with dual_db.get_local_db() as session:
        result = await session.execute(select(Message).where(Message.id == "msg_recent"))
        msg = result.scalar_one()
        assert msg.status == "streaming"


@pytest.mark.asyncio
async def test_complete_messages_not_affected(dual_db):
    """Messages with status=complete are not affected by recovery scan."""
    await _seed_conversation(dual_db)

    old_ts = now_ms() - (10 * 60 * 1000)  # 10 minutes ago
    async with dual_db.get_local_db() as session:
        msg = Message(
            id="msg_complete",
            conversation_id="conv1",
            role="agent",
            status="complete",
            created_at=old_ts,
        )
        msg.parts_list = [{"type": "text", "content": "done"}]
        msg.mentioned_agent_ids_list = []
        session.add(msg)

    # Run recovery scan
    resolved = await scan_interrupted_messages()
    assert resolved == 0

    # Verify message is still complete
    async with dual_db.get_local_db() as session:
        result = await session.execute(select(Message).where(Message.id == "msg_complete"))
        msg = result.scalar_one()
        assert msg.status == "complete"


@pytest.mark.asyncio
async def test_multiple_stuck_messages(dual_db):
    """Multiple stuck messages are all resolved in one scan."""
    await _seed_conversation(dual_db)

    old_ts = now_ms() - (7 * 60 * 1000)  # 7 minutes ago
    msg_ids = [f"msg_stuck_{i}" for i in range(3)]

    async with dual_db.get_local_db() as session:
        for mid in msg_ids:
            msg = Message(
                id=mid,
                conversation_id="conv1",
                role="agent",
                status="streaming",
                created_at=old_ts,
            )
            msg.parts_list = []
            msg.mentioned_agent_ids_list = []
            session.add(msg)

    resolved = await scan_interrupted_messages()
    assert resolved == 3

    # Verify all are interrupted
    async with dual_db.get_local_db() as session:
        for mid in msg_ids:
            result = await session.execute(select(Message).where(Message.id == mid))
            msg = result.scalar_one()
            assert msg.status == "interrupted"
