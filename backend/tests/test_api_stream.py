"""Tests for the global SSE stream generator (app/api/stream.py).

Drives the async generator directly against the real event_bus rather than over
an httpx stream — that keeps the wire-contract assertions deterministic.
"""

import asyncio
import json

from app.api.stream import _event_stream
from app.schemas.events import RunStartEvent
from app.services.event_bus import event_bus


async def test_first_frame_is_connected():
    gen = _event_stream(user_id=None)
    try:
        frame = await gen.__anext__()
        assert set(frame.keys()) == {"data"}  # data-only, no SSE `event:` field
        payload = json.loads(frame["data"])
        assert payload["type"] == "connected"
        assert isinstance(payload["timestamp"], int)
    finally:
        await gen.aclose()


async def test_published_event_is_forwarded_camelcase():
    gen = _event_stream(user_id=None)
    try:
        await gen.__anext__()  # consume connected; queue is now subscribed
        event_bus.publish(
            RunStartEvent(
                conversation_id="conv_1",
                timestamp=123,
                run_id="run_1",
                agent_id="agent_1",
                trigger_message_id="msg_1",
            )
        )
        frame = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        payload = json.loads(frame["data"])
        assert payload["type"] == "run.start"
        # snake_case Python fields must serialize with camelCase aliases on the wire
        assert payload["conversationId"] == "conv_1"
        assert payload["runId"] == "run_1"
        assert payload["agentId"] == "agent_1"
        assert payload["triggerMessageId"] == "msg_1"
    finally:
        await gen.aclose()


async def test_idle_emits_heartbeat(monkeypatch):
    import app.api.stream as stream_mod

    monkeypatch.setattr(stream_mod, "_HEARTBEAT_SECONDS", 0.05)
    gen = stream_mod._event_stream(user_id=None)
    try:
        await gen.__anext__()  # connected
        frame = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        payload = json.loads(frame["data"])
        assert payload["type"] == "heartbeat"
    finally:
        await gen.aclose()


async def test_unsubscribes_on_close():
    before = event_bus.subscriber_count
    gen = _event_stream(user_id=None)
    await gen.__anext__()  # subscribed
    assert event_bus.subscriber_count == before + 1
    await gen.aclose()
    assert event_bus.subscriber_count == before


async def test_user_isolation_filters_events():
    """Events tagged with user_id=A must not reach subscriber user_id=B."""
    gen_a = _event_stream(user_id="user_a")
    gen_b = _event_stream(user_id="user_b")
    try:
        await gen_a.__anext__()  # connected
        await gen_b.__anext__()  # connected

        # Publish an event for user_a only
        event_bus.publish(
            RunStartEvent(
                conversation_id="conv_1",
                timestamp=123,
                run_id="run_1",
                agent_id="agent_1",
                trigger_message_id="msg_1",
            ),
            user_id="user_a",
        )

        # user_a receives it
        frame_a = await asyncio.wait_for(gen_a.__anext__(), timeout=2.0)
        payload_a = json.loads(frame_a["data"])
        assert payload_a["type"] == "run.start"

        # user_b does NOT receive it (should get heartbeat on timeout instead)
        try:
            frame_b = await asyncio.wait_for(gen_b.__anext__(), timeout=0.3)
            payload_b = json.loads(frame_b["data"])
            # If we get something, it must be a heartbeat, not the event
            assert payload_b["type"] != "run.start"
        except TimeoutError:
            pass  # acceptable — no event and no heartbeat yet
    finally:
        await gen_a.aclose()
        await gen_b.aclose()


async def test_broadcast_events_reach_all_users():
    """Events published with user_id=None (broadcast) reach all subscribers."""
    gen_a = _event_stream(user_id="user_a")
    gen_b = _event_stream(user_id="user_b")
    try:
        await gen_a.__anext__()  # connected
        await gen_b.__anext__()  # connected

        event_bus.publish(
            RunStartEvent(
                conversation_id="conv_1",
                timestamp=123,
                run_id="run_1",
                agent_id="agent_1",
                trigger_message_id="msg_1",
            ),
            user_id=None,  # broadcast
        )

        frame_a = await asyncio.wait_for(gen_a.__anext__(), timeout=2.0)
        payload_a = json.loads(frame_a["data"])
        assert payload_a["type"] == "run.start"

        frame_b = await asyncio.wait_for(gen_b.__anext__(), timeout=2.0)
        payload_b = json.loads(frame_b["data"])
        assert payload_b["type"] == "run.start"
    finally:
        await gen_a.aclose()
        await gen_b.aclose()
