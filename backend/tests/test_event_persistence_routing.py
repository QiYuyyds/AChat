"""Tests for event persistence routing (Redis Stream vs synchronous DB).

Covers: deferrable event goes to Stream when Redis available, goes to DB
when Redis unavailable, message.start/end go to Stream when Redis available,
usage events are fire-and-forget, publish happens before persist.
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.db.engine import get_db
from app.db.models import AgentRun, Conversation, Message
from app.schemas.events import (
    MessageEndEvent,
    MessageStartEvent,
    PartStartEvent,
    RunUsageEvent,
)
from app.schemas.messages import MessageUsage, RunUsage
from app.services.agent_runner import (
    _persist_or_stream,
    _update_message_usage,
    _update_run_usage,
    persist_event,
    publish,
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


async def _seed_run(run_id: str, conv_id: str, agent_id: str) -> None:
    """Seed a minimal agent run for usage tests."""
    async with get_db() as session:
        session.add(AgentRun(
            id=run_id,
            conversation_id=conv_id,
            agent_id=agent_id,
            status="running",
            started_at=now_ms(),
        ))


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
async def test_message_start_goes_to_stream_when_redis_available(db, test_user):
    """When Redis is available, message.start XADDs to Stream instead of sync INSERT."""
    await _seed_conversation("conv_start", test_user["id"])
    await _seed_agent("ag_1", test_user["id"])

    redis_mock = AsyncMock()
    redis_mock.xadd = AsyncMock(return_value="1-0")

    event = MessageStartEvent(
        conversation_id="conv_start",
        timestamp=now_ms(),
        message_id="msg_start_stream",
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

    # message.start should XADD to Stream
    redis_mock.xadd.assert_called_once()
    xadd_args = redis_mock.xadd.call_args
    assert "achat:run:run_start" in str(xadd_args)

    # Verify hidden flag is included in the XADD'd JSON
    xadd_fields = xadd_args.kwargs.get("fields") or xadd_args[0][1] if len(xadd_args[0]) > 1 else xadd_args.kwargs.get("fields")
    if xadd_fields is None and len(xadd_args.args) > 1:
        xadd_fields = xadd_args.args[1]
    if xadd_fields:
        event_data = json.loads(xadd_fields["data"])
        assert event_data["hidden"] is False
        assert event_data["type"] == "message.start"

    # parts_buffer should be initialized
    assert "msg_start_stream" in parts_buffer
    assert parts_buffer["msg_start_stream"] == []

    # Message should NOT be in PG yet (async INSERT via Consumer)
    async with get_db() as session:
        result = await session.execute(
            select(Message).where(Message.id == "msg_start_stream")
        )
        msg = result.scalar_one_or_none()
        assert msg is None


@pytest.mark.asyncio
async def test_message_start_falls_back_to_sync_insert_when_redis_unavailable(db, test_user):
    """When Redis is None, message.start falls back to synchronous INSERT."""
    await _seed_conversation("conv_start_fb", test_user["id"])
    await _seed_agent("ag_1", test_user["id"])

    event = MessageStartEvent(
        conversation_id="conv_start_fb",
        timestamp=now_ms(),
        message_id="msg_start_fb",
        agent_id="ag_1",
        run_id="run_start_fb",
    )

    parts_buffer: dict[str, list[dict]] = {}
    output_message_ids: list[str] = []

    with patch(
        "app.services.agent_runner._get_redis_client", return_value=None
    ):
        await persist_event(
            event, parts_buffer, "run_start_fb", "ag_1", output_message_ids, [], False
        )

    # Message should be in PG (synchronous INSERT)
    async with get_db() as session:
        result = await session.execute(
            select(Message).where(Message.id == "msg_start_fb")
        )
        msg = result.scalar_one_or_none()
        assert msg is not None
        assert msg.status == "streaming"


@pytest.mark.asyncio
async def test_message_end_goes_to_stream_when_redis_available(db, test_user):
    """When Redis is available, message.end XADDs to Stream instead of sync UPDATE."""
    await _seed_conversation("conv_end", test_user["id"])
    await _seed_agent("ag_1", test_user["id"])

    async with get_db() as session:
        msg = Message(
            id="msg_end_stream",
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
    redis_mock.xadd = AsyncMock(return_value="1-0")

    event = MessageEndEvent(
        conversation_id="conv_end",
        timestamp=now_ms(),
        message_id="msg_end_stream",
    )

    parts_buffer: dict[str, list[dict]] = {
        "msg_end_stream": [{"type": "text", "content": "final text"}]
    }

    with patch(
        "app.services.agent_runner._get_redis_client", return_value=redis_mock
    ):
        await persist_event(
            event, parts_buffer, "run_end", "ag_1", [], [], False
        )

    # message.end should XADD to Stream
    redis_mock.xadd.assert_called_once()

    # PG should NOT have status="complete" yet (async UPDATE via Consumer)
    async with get_db() as session:
        result = await session.execute(
            select(Message).where(Message.id == "msg_end_stream")
        )
        msg = result.scalar_one_or_none()
        assert msg is not None
        assert msg.status == "streaming"

    # parts_buffer should NOT be popped (Consumer needs it)
    assert "msg_end_stream" in parts_buffer

    # Stream should NOT be deleted by message.end (finally block handles it)
    redis_mock.delete.assert_not_called()


@pytest.mark.asyncio
async def test_message_end_falls_back_to_sync_update_when_redis_unavailable(db, test_user):
    """When Redis is None, message.end falls back to synchronous UPDATE."""
    await _seed_conversation("conv_end_fb", test_user["id"])
    await _seed_agent("ag_1", test_user["id"])

    async with get_db() as session:
        msg = Message(
            id="msg_end_fb",
            conversation_id="conv_end_fb",
            role="agent",
            agent_id="ag_1",
            status="streaming",
            run_id="run_end_fb",
            created_at=now_ms(),
        )
        msg.parts_list = []
        session.add(msg)

    event = MessageEndEvent(
        conversation_id="conv_end_fb",
        timestamp=now_ms(),
        message_id="msg_end_fb",
    )

    parts_buffer: dict[str, list[dict]] = {
        "msg_end_fb": [{"type": "text", "content": "final text"}]
    }

    with patch(
        "app.services.agent_runner._get_redis_client", return_value=None
    ):
        await persist_event(
            event, parts_buffer, "run_end_fb", "ag_1", [], [], False
        )

    # PG should have final state (synchronous UPDATE)
    async with get_db() as session:
        result = await session.execute(
            select(Message).where(Message.id == "msg_end_fb")
        )
        msg = result.scalar_one_or_none()
        assert msg is not None
        assert msg.status == "complete"
        assert msg.parts_list == [{"type": "text", "content": "final text"}]


@pytest.mark.asyncio
async def test_run_usage_fire_and_forget(db, test_user):
    """run.usage is persisted via fire-and-forget asyncio.create_task, not blocking."""
    await _seed_conversation("conv_usage", test_user["id"])
    await _seed_agent("ag_1", test_user["id"])
    await _seed_run("run_usage_test", "conv_usage", "ag_1")

    usage = RunUsage(
        input_tokens=100,
        output_tokens=50,
        cache_creation_tokens=10,
        cache_read_tokens=5,
    )

    event = RunUsageEvent(
        conversation_id="conv_usage",
        timestamp=now_ms(),
        run_id="run_usage_test",
        usage=usage,
    )

    parts_buffer: dict[str, list[dict]] = {}
    output_message_ids: list[str] = []

    with patch(
        "app.services.agent_runner._get_redis_client", return_value=None
    ):
        await persist_event(
            event, parts_buffer, "run_usage_test", "ag_1", output_message_ids, [], False
        )

    # Allow fire-and-forget task to complete
    await asyncio.sleep(0.05)

    # Verify usage was written to PG
    async with get_db() as session:
        result = await session.execute(
            select(AgentRun).where(AgentRun.id == "run_usage_test")
        )
        run = result.scalar_one_or_none()
        assert run is not None
        assert run.usage is not None
        assert run.usage["inputTokens"] == 100
        assert run.usage["outputTokens"] == 50


@pytest.mark.asyncio
async def test_message_usage_fire_and_forget(db, test_user):
    """message.usage is persisted via fire-and-forget asyncio.create_task."""
    await _seed_conversation("conv_msg_usage", test_user["id"])
    await _seed_agent("ag_1", test_user["id"])

    async with get_db() as session:
        msg = Message(
            id="msg_usage_test",
            conversation_id="conv_msg_usage",
            role="agent",
            agent_id="ag_1",
            status="streaming",
            run_id="run_msg_usage",
            created_at=now_ms(),
        )
        msg.parts_list = []
        session.add(msg)

    from app.schemas.events import MessageUsageEventPayload

    usage = MessageUsage(
        input_tokens=200,
        output_tokens=100,
        cache_read_tokens=20,
    )

    event = MessageUsageEventPayload(
        conversation_id="conv_msg_usage",
        timestamp=now_ms(),
        message_id="msg_usage_test",
        usage=usage,
    )

    parts_buffer: dict[str, list[dict]] = {}
    output_message_ids: list[str] = []

    with patch(
        "app.services.agent_runner._get_redis_client", return_value=None
    ):
        await persist_event(
            event, parts_buffer, "run_msg_usage", "ag_1", output_message_ids, [], False
        )

    # Allow fire-and-forget task to complete
    await asyncio.sleep(0.05)

    # Verify usage was written to PG
    async with get_db() as session:
        result = await session.execute(
            select(Message).where(Message.id == "msg_usage_test")
        )
        msg = result.scalar_one_or_none()
        assert msg is not None
        assert msg.usage is not None
        assert msg.usage["inputTokens"] == 200
        assert msg.usage["outputTokens"] == 100


@pytest.mark.asyncio
async def test_usage_fire_and_forget_does_not_block_on_error(db, test_user):
    """If the fire-and-forget DB update fails, it logs but does not raise."""
    with patch("app.services.agent_runner.get_db", side_effect=RuntimeError("DB down")):
        # Should not raise
        await _update_run_usage("run_x", {"inputTokens": 1})
        await _update_message_usage("msg_x", {"inputTokens": 1})


@pytest.mark.asyncio
async def test_publish_before_persist(db, test_user):
    """Verify publish is called before persist_event in consume_stream."""
    from app.schemas.events import RunEndEvent
    from app.services.agent_runner import consume_stream

    await _seed_conversation("conv_order", test_user["id"])
    await _seed_agent("ag_1", test_user["id"])

    call_order: list[str] = []

    original_persist = persist_event
    original_publish = publish

    async def _tracking_persist(*args, **kwargs):
        call_order.append("persist")
        await original_persist(*args, **kwargs)

    def _tracking_publish(*args, **kwargs):
        call_order.append("publish")
        original_publish(*args, **kwargs)

    events = [
        MessageStartEvent(
            conversation_id="conv_order",
            timestamp=now_ms(),
            message_id="msg_order",
            agent_id="ag_1",
            run_id="run_order",
        ),
        PartStartEvent(
            conversation_id="conv_order",
            timestamp=now_ms(),
            message_id="msg_order",
            part_index=0,
            part={"type": "text", "content": "hello"},
        ),
        MessageEndEvent(
            conversation_id="conv_order",
            timestamp=now_ms(),
            message_id="msg_order",
        ),
        RunEndEvent(
            conversation_id="conv_order",
            timestamp=now_ms(),
            run_id="run_order",
            agent_id="ag_1",
            status="complete",
        ),
    ]

    async def _fake_stream():
        for e in events:
            yield e

    with (
        patch("app.services.agent_runner.persist_event", _tracking_persist),
        patch("app.services.agent_runner.publish", _tracking_publish),
        patch("app.services.agent_runner._get_redis_client", return_value=None),
    ):
        await consume_stream(
            _fake_stream(),
            agent_id="ag_1",
            run_id="run_order",
            hidden=False,
            user_id=test_user["id"],
        )

    # For each event, publish should come before persist
    for i in range(0, len(call_order), 2):
        assert call_order[i] == "publish", f"Expected publish at index {i}, got {call_order[i]}"
        assert call_order[i + 1] == "persist", f"Expected persist at index {i+1}, got {call_order[i+1]}"


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
    """Integration: Redis available → all events go to Stream, consumer flushes
    to PG (INSERT for message.start, UPDATE for parts and message.end), SSE is real-time."""
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
            # 1. message.start — XADD to Stream, publish to SSE
            start_event = MessageStartEvent(
                conversation_id="conv_int",
                timestamp=now_ms(),
                message_id=msg_id,
                agent_id="ag_1",
                run_id=run_id,
            )
            publish(start_event, user_id=test_user["id"])
            await persist_event(start_event, parts_buffer, run_id, "ag_1", output_message_ids, [], False)

            # 2. part.start — XADD to Stream, publish to SSE
            part_event = PartStartEvent(
                conversation_id="conv_int",
                timestamp=now_ms(),
                message_id=msg_id,
                part_index=0,
                part={"type": "text", "content": "hello from stream"},
            )
            publish(part_event, user_id=test_user["id"])
            await persist_event(part_event, parts_buffer, run_id, "ag_1", output_message_ids, [], False)

            # SSE should be real-time: events received before DB flush
            await asyncio.sleep(0.05)
            sse_types = {getattr(e, "type", None) for e in collected}
            assert "message.start" in sse_types
            assert "part.start" in sse_types

            # Message row NOT in PG yet (async INSERT via Consumer)
            async with get_db() as session:
                result = await session.execute(select(Message).where(Message.id == msg_id))
                msg = result.scalar_one_or_none()
                assert msg is None

            # Stream has 2 events (message.start + part.start)
            key = stream_key(run_id)
            assert await redis.xlen(key) == 2

            # 3. Consumer flushes Stream → PG (INSERT message row + UPDATE parts)
            consumer = DBWriterConsumer(redis)
            await consumer._process_once()

            # PG now has message row with parts
            async with get_db() as session:
                result = await session.execute(select(Message).where(Message.id == msg_id))
                msg = result.scalar_one_or_none()
                assert msg is not None
                assert msg.status == "streaming"
                assert len(msg.parts_list) == 1
                assert msg.parts_list[0]["type"] == "text"
                assert msg.parts_list[0]["content"] == "hello from stream"
                assert "startedAt" in msg.parts_list[0]

            # 4. message.end — XADD to Stream, publish to SSE
            end_event = MessageEndEvent(
                conversation_id="conv_int",
                timestamp=now_ms(),
                message_id=msg_id,
            )
            publish(end_event, user_id=test_user["id"])
            await persist_event(end_event, parts_buffer, run_id, "ag_1", output_message_ids, [], False)

            # PG does NOT have status="complete" yet (async UPDATE via Consumer)
            async with get_db() as session:
                result = await session.execute(select(Message).where(Message.id == msg_id))
                msg = result.scalar_one_or_none()
                assert msg.status == "streaming"

            # 5. Consumer flushes message.end → PG (UPDATE status="complete")
            await consumer._process_once()

            async with get_db() as session:
                result = await session.execute(select(Message).where(Message.id == msg_id))
                msg = result.scalar_one_or_none()
                assert msg.status == "complete"
                assert len(msg.parts_list) == 1
                assert msg.parts_list[0]["type"] == "text"
                assert msg.parts_list[0]["content"] == "hello from stream"

        await asyncio.sleep(0.05)
        await drainer

    # SSE received all event types
    final_sse_types = {getattr(e, "type", None) for e in collected}
    assert "message.end" in final_sse_types

    unregister_parts_buffer(run_id)
