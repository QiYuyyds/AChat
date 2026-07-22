"""Tests for crash recovery scan.

Covers: recovery with Stream present, recovery without Stream, no stuck messages,
and recovery when message row doesn't exist in PG but Redis Stream has events.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.db.engine import get_db
from app.db.models import Message
from app.services.recovery_scan import scan_interrupted_messages
from app.utils.clock import now_ms


async def _seed_conversation(conv_id: str, user_id: str, old_ts: int) -> None:
    from app.db.models import Conversation

    async with get_db() as session:
        conv = Conversation(
            id=conv_id,
            user_id=user_id,
            title="Stuck Test",
            mode="single",
            archived=False,
            fs_write_approval_mode="auto",
            created_at=old_ts,
            updated_at=old_ts,
        )
        conv.agent_ids_list = []
        conv.pinned_message_ids_list = []
        conv.bookmarked_message_ids_list = []
        session.add(conv)


async def _seed_stuck_message(
    user_id: str,
    msg_id: str = "msg_stuck",
    run_id: str = "run_stuck",
    conv_id: str = "conv_stuck",
    age_ms: int = 10 * 60 * 1000,
) -> None:
    """Seed a message with status=streaming older than the threshold."""
    old_ts = now_ms() - age_ms
    await _seed_conversation(conv_id, user_id, old_ts)

    async with get_db() as session:
        msg = Message(
            id=msg_id,
            conversation_id=conv_id,
            role="agent",
            agent_id=None,
            status="streaming",
            run_id=run_id,
            created_at=old_ts,
        )
        msg.parts_list = [{"type": "text", "content": "partial"}]
        session.add(msg)


@pytest.mark.asyncio
async def test_no_stuck_messages(db, test_user):
    """When no messages are stuck, scan returns 0 and does nothing."""
    count = await scan_interrupted_messages()
    assert count == 0


@pytest.mark.asyncio
async def test_recovery_without_stream(db, test_user):
    """When Redis Stream doesn't exist, message is marked as interrupted."""
    await _seed_stuck_message(test_user["id"])

    redis_mock = AsyncMock()
    redis_mock.exists = AsyncMock(return_value=0)
    redis_mock.scan = AsyncMock(return_value=(b"0", []))

    with patch("app.infra.factory.get_infrastructure") as mock_infra:
        mock_infra.return_value = type("Infra", (), {"redis_client": redis_mock})()

        count = await scan_interrupted_messages()

    assert count == 1

    async with get_db() as session:
        result = await session.execute(
            select(Message).where(Message.id == "msg_stuck")
        )
        msg = result.scalar_one_or_none()
        assert msg is not None
        assert msg.status == "interrupted"


@pytest.mark.asyncio
async def test_recovery_with_stream(db, test_user):
    """When Redis Stream exists, events are replayed and message marked complete."""
    await _seed_stuck_message(test_user["id"])

    redis_mock = AsyncMock()
    redis_mock.exists = AsyncMock(return_value=1)
    redis_mock.xrange = AsyncMock(return_value=[
        ("1-0", {"data": json.dumps({
            "type": "part.start",
            "messageId": "msg_stuck",
            "partIndex": 0,
            "part": {"type": "text", "content": "replayed"},
        })}),
    ])
    redis_mock.delete = AsyncMock()
    redis_mock.scan = AsyncMock(return_value=(b"0", []))

    with patch("app.infra.factory.get_infrastructure") as mock_infra:
        mock_infra.return_value = type("Infra", (), {"redis_client": redis_mock})()

        count = await scan_interrupted_messages()

    assert count == 1

    async with get_db() as session:
        result = await session.execute(
            select(Message).where(Message.id == "msg_stuck")
        )
        msg = result.scalar_one_or_none()
        assert msg is not None
        assert msg.status == "complete"
        assert msg.parts_list == [{"type": "text", "content": "replayed"}]

    # Stream should be cleaned up
    redis_mock.delete.assert_called()


@pytest.mark.asyncio
async def test_recovery_redis_unavailable(db, test_user):
    """When Redis is None, message is marked as interrupted."""
    await _seed_stuck_message(test_user["id"])

    with patch("app.infra.factory.get_infrastructure") as mock_infra:
        mock_infra.return_value = type("Infra", (), {"redis_client": None})()

        count = await scan_interrupted_messages()

    assert count == 1

    async with get_db() as session:
        result = await session.execute(
            select(Message).where(Message.id == "msg_stuck")
        )
        msg = result.scalar_one_or_none()
        assert msg is not None
        assert msg.status == "interrupted"


@pytest.mark.asyncio
async def test_recovery_message_not_in_pg_but_stream_has_events(db, test_user):
    """When message row doesn't exist in PG but Redis Stream has message.start,
    recovery scan INSERTs the message and replays events."""
    old_ts = now_ms() - 10 * 60 * 1000
    await _seed_conversation("conv_orphan", test_user["id"], old_ts)

    # Don't seed a message row — simulate async INSERT not flushed

    stream_events = [
        ("1-0", {"data": json.dumps({
            "type": "message.start",
            "messageId": "msg_orphan",
            "conversationId": "conv_orphan",
            "agentId": None,
            "runId": "run_orphan",
            "timestamp": old_ts,
            "hidden": False,
        })}),
        ("2-0", {"data": json.dumps({
            "type": "part.start",
            "messageId": "msg_orphan",
            "partIndex": 0,
            "part": {"type": "text", "content": "orphan replayed"},
        })}),
        ("3-0", {"data": json.dumps({
            "type": "message.end",
            "messageId": "msg_orphan",
            "conversationId": "conv_orphan",
            "timestamp": old_ts,
        })}),
    ]

    redis_mock = AsyncMock()
    redis_mock.exists = AsyncMock(return_value=0)
    redis_mock.xrange = AsyncMock(return_value=stream_events)
    redis_mock.delete = AsyncMock()
    redis_mock.scan = AsyncMock(return_value=(b"0", [b"achat:run:run_orphan"]))

    with patch("app.infra.factory.get_infrastructure") as mock_infra:
        mock_infra.return_value = type("Infra", (), {"redis_client": redis_mock})()

        count = await scan_interrupted_messages()

    # Should have resolved the orphaned message
    assert count >= 1

    # Message should now exist in PG with replayed parts and complete status
    async with get_db() as session:
        result = await session.execute(
            select(Message).where(Message.id == "msg_orphan")
        )
        msg = result.scalar_one_or_none()
        assert msg is not None
        assert msg.status == "complete"
        assert msg.parts_list == [{"type": "text", "content": "orphan replayed"}]


@pytest.mark.asyncio
async def test_recovery_message_not_in_pg_no_message_start(db, test_user):
    """When message row doesn't exist in PG and Stream has no message.start event,
    the orphaned stream is skipped without error."""
    old_ts = now_ms() - 10 * 60 * 1000
    await _seed_conversation("conv_orphan2", test_user["id"], old_ts)

    # Stream has only part events, no message.start
    stream_events = [
        ("1-0", {"data": json.dumps({
            "type": "part.start",
            "messageId": "msg_orphan2",
            "partIndex": 0,
            "part": {"type": "text", "content": "no start event"},
        })}),
    ]

    redis_mock = AsyncMock()
    redis_mock.exists = AsyncMock(return_value=0)
    redis_mock.xrange = AsyncMock(return_value=stream_events)
    redis_mock.delete = AsyncMock()
    redis_mock.scan = AsyncMock(return_value=(b"0", [b"achat:run:run_orphan2"]))

    with patch("app.infra.factory.get_infrastructure") as mock_infra:
        mock_infra.return_value = type("Infra", (), {"redis_client": redis_mock})()

        count = await scan_interrupted_messages()

    # No messages resolved (no message.start event to INSERT from)
    assert count == 0

    # Message should not exist in PG
    async with get_db() as session:
        result = await session.execute(
            select(Message).where(Message.id == "msg_orphan2")
        )
        msg = result.scalar_one_or_none()
        assert msg is None
