"""Tests for event persistence routing (Redis Stream vs synchronous DB).

Covers: deferrable event goes to Stream when Redis available, goes to DB
when Redis unavailable, message.start/message.end always synchronous.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.db.engine import get_db
from app.db.models import Conversation, Message
from app.schemas.events import (
    MessageEndEvent,
    MessageStartEvent,
    PartStartEvent,
)
from app.services.agent_runner import (
    _persist_or_stream,
    persist_event,
)
from app.utils.clock import now_ms


async def _seed_conversation(conv_id: str = "conv_test", user_id: str = "test_user_1") -> None:
    """Seed a conversation so messages can reference it."""
    async with get_db() as session:
        conv = Conversation(
            id=conv_id,
            user_id=user_id,
            title="Test",
            mode="single",
            archived=False,
            fs_write_approval_mode="auto",
            created_at=now_ms(),
            updated_at=now_ms(),
        )
        conv.agent_ids_list = []
        conv.pinned_message_ids_list = []
        conv.bookmarked_message_ids_list = []
        session.add(conv)


async def _seed_agent(agent_id: str = "ag_1", user_id: str = "test_user_1") -> None:
    """Seed a minimal agent so FK constraints on messages pass."""
    from app.db.models import Agent

    async with get_db() as session:
        agent = Agent(
            id=agent_id,
            name="Test Agent",
            avatar="T",
            description="test",
            system_prompt="test",
            adapter_name="mock",
            is_builtin=False,
            is_orchestrator=False,
            supports_vision=False,
            created_at=now_ms(),
            user_id=user_id,
        )
        agent.capabilities_list = []
        agent.tool_names_list = []
        session.add(agent)


@pytest.mark.asyncio
async def test_deferrable_event_goes_to_stream_when_redis_available(db, test_user):
    """When Redis is available, part.start XADDs to Stream instead of DB write."""
    redis_mock = AsyncMock()
    redis_mock.xadd = AsyncMock(return_value="1-0")

    event = PartStartEvent(
        conversation_id="conv_1",
        timestamp=now_ms(),
        message_id="msg_1",
        part_index=0,
        part={"type": "text", "content": "hello"},
    )

    await _persist_or_stream(redis_mock, "run_1", event, [event.part], use_stream=True)

    redis_mock.xadd.assert_called_once()
    args = redis_mock.xadd.call_args
    assert "achat:run:run_1" in str(args)


@pytest.mark.asyncio
async def test_deferrable_event_goes_to_db_when_redis_unavailable(db, test_user):
    """When Redis is None, _persist_or_stream falls back to synchronous DB write."""
    await _seed_conversation("conv_sync", test_user["id"])
    await _seed_agent("ag_1", test_user["id"])

    async with get_db() as session:
        msg = Message(
            id="msg_sync_test",
            conversation_id="conv_sync",
            role="agent",
            agent_id="ag_1",
            status="streaming",
            run_id="run_sync",
            created_at=now_ms(),
        )
        msg.parts_list = []
        session.add(msg)

    event = PartStartEvent(
        conversation_id="conv_sync",
        timestamp=now_ms(),
        message_id="msg_sync_test",
        part_index=0,
        part={"type": "text", "content": "sync hello"},
    )

    await _persist_or_stream(None, "run_sync", event, [event.part], use_stream=False)

    async with get_db() as session:
        result = await session.execute(
            select(Message).where(Message.id == "msg_sync_test")
        )
        msg = result.scalar_one_or_none()
        assert msg is not None
        assert msg.parts_list == [{"type": "text", "content": "sync hello"}]


@pytest.mark.asyncio
async def test_message_start_always_synchronous(db, test_user):
    """message.start always does a synchronous INSERT, regardless of Redis."""
    await _seed_conversation("conv_start", test_user["id"])
    await _seed_agent("ag_1", test_user["id"])

    redis_mock = AsyncMock()

    event = MessageStartEvent(
        conversation_id="conv_start",
        timestamp=now_ms(),
        message_id="msg_start_test",
        agent_id="ag_1",
        run_id="run_start",
    )

    parts_buffer: dict[str, list[dict]] = {}
    output_message_ids: list[str] = []

    with patch(
        "app.services.agent_runner._get_redis_client", return_value=redis_mock
    ):
        await persist_event(
            event, parts_buffer, "run_start", "ag_1", output_message_ids, [], False
        )

    async with get_db() as session:
        result = await session.execute(
            select(Message).where(Message.id == "msg_start_test")
        )
        msg = result.scalar_one_or_none()
        assert msg is not None
        assert msg.status == "streaming"

    redis_mock.xadd.assert_not_called()


@pytest.mark.asyncio
async def test_message_end_always_synchronous(db, test_user):
    """message.end always does a synchronous status UPDATE + final parts flush."""
    await _seed_conversation("conv_end", test_user["id"])
    await _seed_agent("ag_1", test_user["id"])

    async with get_db() as session:
        msg = Message(
            id="msg_end_test",
            conversation_id="conv_end",
            role="agent",
            agent_id="ag_1",
            status="streaming",
            run_id="run_end",
            created_at=now_ms(),
        )
        msg.parts_list = []
        session.add(msg)

    redis_mock = AsyncMock()
    redis_mock.delete = AsyncMock()

    event = MessageEndEvent(
        conversation_id="conv_end",
        timestamp=now_ms(),
        message_id="msg_end_test",
    )

    parts_buffer: dict[str, list[dict]] = {
        "msg_end_test": [{"type": "text", "content": "final text"}]
    }

    with patch(
        "app.services.agent_runner._get_redis_client", return_value=redis_mock
    ):
        await persist_event(
            event, parts_buffer, "run_end", "ag_1", [], [], False
        )

    async with get_db() as session:
        result = await session.execute(
            select(Message).where(Message.id == "msg_end_test")
        )
        msg = result.scalar_one_or_none()
        assert msg is not None
        assert msg.status == "complete"
        assert msg.parts_list == [{"type": "text", "content": "final text"}]

    redis_mock.delete.assert_called_once_with("achat:run:run_end")


class _FakeRedisStreams:
    """Minimal async Redis mock with Stream operations for integration tests."""

    def __init__(self):
        self._streams: dict[str, list[tuple[str, dict]]] = {}
        self._groups: dict[str, set[str]] = {}
        self._counter = 0

    async def xadd(self, key, fields, maxlen=None, approximate=False):
        self._counter += 1
        entry_id = f"{self._counter}-0"
        self._streams.setdefault(key, []).append((entry_id, fields))
        return entry_id

    async def xgroup_create(self, key, group, id="0", mkstream=False):
        self._groups.setdefault(key, set())
        if group in self._groups[key]:
            raise Exception("BUSYGROUP Consumer Group name already exists")
        self._groups[key].add(group)

    async def xreadgroup(self, group, consumer, streams, count=10, block=0):
        results = []
        for key, _id in streams.items():
            entries = self._streams.get(key, [])
            if entries:
                results.append((key, entries[:count]))
        return results if results else None

    async def xack(self, key, group, *ids):
        if key in self._streams:
            self._streams[key] = [
                (eid, f) for eid, f in self._streams[key] if eid not in ids
            ]
        return len(ids)

    async def xlen(self, key):
        return len(self._streams.get(key, []))

    async def delete(self, key):
        self._streams.pop(key, None)
        self._groups.pop(key, None)

    async def exists(self, key):
        return 1 if key in self._streams else 0


@pytest.mark.asyncio
async def test_integration_redis_available_full_flow(db, test_user):
    """Integration: Redis available → deferrable events go to Stream, consumer
    flushes to PG, message.end does synchronous final flush, SSE is real-time."""
    import asyncio

    from app.services.agent_runner import publish
    from app.services.async_db_writer import (
        DBWriterConsumer,
        register_parts_buffer,
        stream_key,
        unregister_parts_buffer,
    )
    from app.services.event_bus import event_bus

    await _seed_conversation("conv_int", test_user["id"])
    await _seed_agent("ag_1", test_user["id"])

    redis = _FakeRedisStreams()
    run_id = "run_int"
    msg_id = "msg_int"
    parts_buffer: dict[str, list[dict]] = {}
    output_message_ids: list[str] = []

    register_parts_buffer(run_id, parts_buffer)

    collected: list = []

    async def _drain(queue):
        try:
            while True:
                collected.append(await asyncio.wait_for(queue.get(), timeout=1.0))
        except TimeoutError:
            return

    async with event_bus.subscribe(user_id=test_user["id"]) as queue:
        drainer = asyncio.create_task(_drain(queue))

        with patch("app.services.agent_runner._get_redis_client", return_value=redis):
            # 1. message.start — synchronous INSERT, publish to SSE
            start_event = MessageStartEvent(
                conversation_id="conv_int",
                timestamp=now_ms(),
                message_id=msg_id,
                agent_id="ag_1",
                run_id=run_id,
            )
            await persist_event(start_event, parts_buffer, run_id, "ag_1", output_message_ids, [], False)
            publish(start_event, user_id=test_user["id"])

            # 2. part.start — XADD to Stream (deferred DB write), publish to SSE
            part_event = PartStartEvent(
                conversation_id="conv_int",
                timestamp=now_ms(),
                message_id=msg_id,
                part_index=0,
                part={"type": "text", "content": "hello from stream"},
            )
            await persist_event(part_event, parts_buffer, run_id, "ag_1", output_message_ids, [], False)
            publish(part_event, user_id=test_user["id"])

            # SSE should be real-time: events received before DB flush
            await asyncio.sleep(0.05)
            sse_types = {getattr(e, "type", None) for e in collected}
            assert "message.start" in sse_types
            assert "part.start" in sse_types

            # PG parts NOT updated yet (deferred to Stream)
            async with get_db() as session:
                result = await session.execute(select(Message).where(Message.id == msg_id))
                msg = result.scalar_one_or_none()
                assert msg.parts_list == []

            # Stream has 1 event
            key = stream_key(run_id)
            assert await redis.xlen(key) == 1

            # 3. Consumer flushes Stream → PG
            consumer = DBWriterConsumer(redis)
            await consumer._process_once()

            # PG now has parts (flushed by consumer)
            async with get_db() as session:
                result = await session.execute(select(Message).where(Message.id == msg_id))
                msg = result.scalar_one_or_none()
                assert msg.parts_list == [{"type": "text", "content": "hello from stream"}]

            # 4. message.end — synchronous final flush + delete Stream
            end_event = MessageEndEvent(
                conversation_id="conv_int",
                timestamp=now_ms(),
                message_id=msg_id,
            )
            await persist_event(end_event, parts_buffer, run_id, "ag_1", output_message_ids, [], False)
            publish(end_event, user_id=test_user["id"])

            # PG has final state
            async with get_db() as session:
                result = await session.execute(select(Message).where(Message.id == msg_id))
                msg = result.scalar_one_or_none()
                assert msg.status == "complete"
                assert msg.parts_list == [{"type": "text", "content": "hello from stream"}]

            # Stream was deleted by message.end
            assert await redis.exists(key) == 0

        await asyncio.sleep(0.05)
        await drainer

    # SSE received all event types
    final_sse_types = {getattr(e, "type", None) for e in collected}
    assert "message.end" in final_sse_types

    unregister_parts_buffer(run_id)
