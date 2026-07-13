"""Tests for the async DB writer consumer.

Covers: batch flush, consumer error handling, graceful shutdown.
Uses a FakeRedis to avoid requiring a real Redis instance.
"""

import json

import pytest

from app.services.async_db_writer import (
    DBWriterConsumer,
    register_parts_buffer,
    stream_key,
    unregister_parts_buffer,
    xadd_event,
)


class FakeRedisForStreams:
    """Minimal async Redis mock with Stream operations."""

    def __init__(self):
        self._streams: dict[str, list[tuple[str, dict]]] = {}
        self._groups: dict[str, set[str]] = {}
        self._counter = 0
        self._xreadgroup_should_fail = False

    async def xadd(self, key, fields, maxlen=None, approximate=False):
        self._counter += 1
        entry_id = f"{self._counter}-0"
        if key not in self._streams:
            self._streams[key] = []
        self._streams[key].append((entry_id, fields))
        return entry_id

    async def xgroup_create(self, key, group, id="0", mkstream=False):
        if key not in self._groups:
            self._groups[key] = set()
        if group in self._groups[key]:
            raise Exception("BUSYGROUP Consumer Group name already exists")
        self._groups[key].add(group)

    async def xreadgroup(self, group, consumer, streams, count=10, block=0):
        if self._xreadgroup_should_fail:
            raise ConnectionError("redis broken")
        results = []
        for key, _id in streams.items():
            entries = self._streams.get(key, [])
            if entries:
                batch = entries[:count]
                results.append((key, batch))
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

    async def keys(self, pattern):
        import fnmatch
        return [k for k in self._streams if fnmatch.fnmatch(k, pattern)]

    async def aclose(self):
        pass


@pytest.mark.asyncio
async def test_xadd_event():
    """xadd_event stores event JSON in the stream."""
    redis = FakeRedisForStreams()
    await xadd_event(redis, "run_1", json.dumps({"type": "part.delta", "messageId": "msg_1"}))
    key = stream_key("run_1")
    assert redis.xlen and await redis.xlen(key) == 1


@pytest.mark.asyncio
async def test_batch_flush_updates_pg(db, test_user):
    """Consumer reads events from Stream and flushes parts_buffer to PG."""
    from app.db.engine import get_db
    from app.db.models import Conversation, Message
    from app.utils.clock import now_ms

    # Seed a conversation first (FK constraint)
    async with get_db() as session:
        conv = Conversation(
            id="conv_test_flush",
            user_id=test_user["id"],
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

    # Seed a message in PG
    msg_id = "msg_test_flush"
    async with get_db() as session:
        msg = Message(
            id=msg_id,
            conversation_id="conv_test_flush",
            role="agent",
            agent_id=None,
            status="streaming",
            run_id="run_test_flush",
            created_at=now_ms(),
        )
        msg.parts_list = []
        session.add(msg)

    redis = FakeRedisForStreams()
    parts_buffer: dict[str, list[dict]] = {msg_id: [{"type": "text", "content": "hello"}]}
    register_parts_buffer("run_test_flush", parts_buffer)

    # Add an event to the stream
    await xadd_event(redis, "run_test_flush", json.dumps({"type": "part.delta", "messageId": msg_id}))

    consumer = DBWriterConsumer(redis)
    await consumer._ensure_group(stream_key("run_test_flush"))
    events = await redis.xreadgroup(
        "db_writer", "db_writer-1",
        {stream_key("run_test_flush"): ">"},
        count=50, block=1000,
    )
    await consumer._flush_batch(stream_key("run_test_flush"), events, parts_buffer)

    # Verify PG was updated
    async with get_db() as session:
        from sqlalchemy import select
        result = await session.execute(select(Message).where(Message.id == msg_id))
        msg = result.scalar_one_or_none()
        assert msg is not None
        assert msg.parts_list == [{"type": "text", "content": "hello"}]

    unregister_parts_buffer("run_test_flush")


@pytest.mark.asyncio
async def test_consumer_error_handling():
    """Consumer logs errors and continues (doesn't crash)."""
    redis = FakeRedisForStreams()
    redis._xreadgroup_should_fail = True

    # Register a dummy buffer so _process_once doesn't skip
    register_parts_buffer("run_err", {})
    consumer = DBWriterConsumer(redis)

    # Run one iteration — should not raise
    await consumer._process_once()

    unregister_parts_buffer("run_err")


@pytest.mark.asyncio
async def test_graceful_shutdown():
    """stop() cancels the background task cleanly."""
    redis = FakeRedisForStreams()
    consumer = DBWriterConsumer(redis)
    await consumer.start()
    await consumer.stop()
    assert consumer._task is None
