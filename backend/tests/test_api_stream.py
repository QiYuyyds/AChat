"""Tests for the global SSE stream generator (app/api/stream.py).

Drives the async generator directly against the real event_bus rather than over
an httpx stream — that keeps the wire-contract assertions deterministic. One
test exercises the full HTTP layer (Last-Event-ID header → replay) over httpx
ASGI since the header plumbing only exists at the request boundary.
"""

import asyncio
import json
import re

import pytest

import app.api.stream as stream_mod
from app.api.stream import _event_stream
from app.schemas.events import RunStartEvent
from app.services.event_bus import EventBus, event_bus


def _run_start(run_id: str, ts: int) -> RunStartEvent:
    return RunStartEvent(
        conversation_id="conv_1",
        timestamp=ts,
        run_id=run_id,
        agent_id="agent_1",
        trigger_message_id="msg_1",
    )


async def _next_frame(gen, timeout: float = 2.0) -> dict:
    return await asyncio.wait_for(gen.__anext__(), timeout=timeout)


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


async def test_publish_target_ignored_all_subscribers_receive():
    """user_id publish targets are retained for API compat but ignored.

    The event bus broadcasts every event to every subscriber in single-user
    mode (local user filtering removed with the dual-DB routing change), so
    both subscribers receive the event regardless of the publish target.
    """
    gen_a = _event_stream(user_id="user_a")
    gen_b = _event_stream(user_id="user_b")
    try:
        await gen_a.__anext__()  # connected
        await gen_b.__anext__()  # connected

        # Publish an event targeted at user_a
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

        # user_b receives it too — broadcast semantics
        frame_b = await asyncio.wait_for(gen_b.__anext__(), timeout=2.0)
        payload_b = json.loads(frame_b["data"])
        assert payload_b["type"] == "run.start"
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


# ─── Reconnect replay & resync (specs: add-sse-reconnect-reconciliation) ───


async def test_every_event_frame_carries_seq_id(monkeypatch):
    bus = EventBus(replay_buffer_size=100)
    monkeypatch.setattr(stream_mod, "event_bus", bus)

    gen = _event_stream(user_id=None)
    try:
        await gen.__anext__()  # connected
        bus.publish(_run_start("run_1", 1))
        bus.publish(_run_start("run_2", 2))
        frame = await _next_frame(gen)
        assert frame["id"] == "1"
        assert json.loads(frame["data"])["runId"] == "run_1"
        frame = await _next_frame(gen)
        assert frame["id"] == "2"
    finally:
        await gen.aclose()


async def test_reconnect_within_buffer_replays_missed_events(monkeypatch):
    """Disconnect window: events 1..3 in the buffer, reconnect at id 1."""
    bus = EventBus(replay_buffer_size=100)
    monkeypatch.setattr(stream_mod, "event_bus", bus)
    bus.publish(_run_start("run_1", 1))  # delivered before the disconnect
    # Missed during the disconnect — published before the generator's first
    # __anext__, i.e. before it subscribes and snapshots the buffer.
    bus.publish(_run_start("run_2", 2))
    bus.publish(_run_start("run_3", 3))

    gen = _event_stream(user_id=None, last_event_id="1")
    try:
        await gen.__anext__()  # connected

        # Replay of seq 2 then 3 — in order, each with its seq id.
        frame = await _next_frame(gen)
        assert frame["id"] == "2"
        assert json.loads(frame["data"])["runId"] == "run_2"
        frame = await _next_frame(gen)
        assert frame["id"] == "3"
        assert json.loads(frame["data"])["runId"] == "run_3"

        # Live tail: an event published after the reconnect follows seamlessly.
        bus.publish(_run_start("run_4", 4))
        frame = await _next_frame(gen)
        assert frame["id"] == "4"
        assert json.loads(frame["data"])["runId"] == "run_4"
    finally:
        await gen.aclose()


async def test_caught_up_client_reconnects_without_resync(monkeypatch):
    """last_seq == newest published seq → empty replay, live loop, no resync."""
    bus = EventBus(replay_buffer_size=100)
    monkeypatch.setattr(stream_mod, "event_bus", bus)
    bus.publish(_run_start("run_1", 1))

    gen = _event_stream(user_id=None, last_event_id="1")
    try:
        await gen.__anext__()  # connected
        bus.publish(_run_start("run_2", 2))
        frame = await _next_frame(gen)
        # First frame after connected is the live event, not a resync.
        assert json.loads(frame["data"])["type"] == "run.start"
        assert frame["id"] == "2"
    finally:
        await gen.aclose()


async def test_gap_beyond_buffer_triggers_resync(monkeypatch):
    """Reconnect id older than the buffer window → resync instead of partial replay."""
    bus = EventBus(replay_buffer_size=2)
    monkeypatch.setattr(stream_mod, "event_bus", bus)
    for i in range(1, 5):  # seqs 1..4, buffer now holds only 3..4
        bus.publish(_run_start(f"run_{i}", i))

    gen = _event_stream(user_id=None, last_event_id="1")
    try:
        await gen.__anext__()  # connected
        frame = await _next_frame(gen)
        assert "id" not in frame  # resync carries no seq id
        payload = json.loads(frame["data"])
        assert payload["type"] == "stream.resync"
        assert isinstance(payload["timestamp"], int)

        # Live events still flow after the resync.
        bus.publish(_run_start("run_5", 5))
        frame = await _next_frame(gen)
        assert frame["id"] == "5"
    finally:
        await gen.aclose()


async def test_server_restart_stale_id_triggers_resync(monkeypatch):
    """Pre-restart seq id is ahead of the reset counter → resync, no replay."""
    bus = EventBus(replay_buffer_size=100)
    monkeypatch.setattr(stream_mod, "event_bus", bus)
    bus.publish(_run_start("run_new_1", 1))  # post-restart counter: _seq == 1

    gen = _event_stream(user_id=None, last_event_id="5000")
    try:
        await gen.__anext__()  # connected
        frame = await _next_frame(gen)
        assert json.loads(frame["data"])["type"] == "stream.resync"
    finally:
        await gen.aclose()


async def test_unparseable_last_event_id_triggers_resync(monkeypatch):
    bus = EventBus(replay_buffer_size=100)
    monkeypatch.setattr(stream_mod, "event_bus", bus)
    bus.publish(_run_start("run_1", 1))

    gen = _event_stream(user_id=None, last_event_id="garbage")
    try:
        await gen.__anext__()  # connected
        frame = await _next_frame(gen)
        assert json.loads(frame["data"])["type"] == "stream.resync"
    finally:
        await gen.aclose()


async def test_first_connect_without_last_event_id_skips_replay(monkeypatch):
    bus = EventBus(replay_buffer_size=100)
    monkeypatch.setattr(stream_mod, "event_bus", bus)
    bus.publish(_run_start("run_1", 1))  # already in the buffer

    gen = _event_stream(user_id=None)  # no header — fresh connection
    try:
        await gen.__anext__()  # connected
        bus.publish(_run_start("run_2", 2))
        frame = await _next_frame(gen)
        # Live event only — no replay of seq 1, no resync.
        assert frame["id"] == "2"
    finally:
        await gen.aclose()


async def test_queue_overflow_injects_resync_before_next_event(monkeypatch):
    """Slow subscriber sheds events → resync precedes the next regular event."""
    bus = EventBus(replay_buffer_size=100)
    monkeypatch.setattr(stream_mod, "event_bus", bus)

    gen = _event_stream(user_id=None)
    try:
        await gen.__anext__()  # connected; generator now subscribed
        # Overflow the 1000-slot queue: event 1 is shed, needs_resync is set.
        for i in range(1, 1002):
            bus.publish(_run_start(f"run_{i}", i))

        frame = await _next_frame(gen)
        assert json.loads(frame["data"])["type"] == "stream.resync"

        # The first regular event after the resync (seq 2 — seq 1 was shed).
        frame = await _next_frame(gen)
        assert frame["id"] == "2"
    finally:
        await gen.aclose()


@pytest.mark.usefixtures("db", "test_user")
async def test_last_event_id_header_flows_through_http_layer(
    monkeypatch, api_client, test_user
):
    """httpx ASGI integration: the Last-Event-ID request header reaches the
    stream handler and produces a replay after the connected frame.

    httpx's ASGITransport buffers the whole response body, so the (normally
    infinite) stream is bounded to the frames under test: connected + the two
    replayed events, then the generator closes and the response completes.
    """
    bus = EventBus(replay_buffer_size=100)
    monkeypatch.setattr(stream_mod, "event_bus", bus)
    bus.publish(_run_start("run_1", 1))
    bus.publish(_run_start("run_2", 2))
    bus.publish(_run_start("run_3", 3))

    real_event_stream = stream_mod._event_stream

    async def _bounded(user_id, last_event_id=None):
        gen = real_event_stream(user_id, last_event_id)
        for _ in range(3):  # connected + replay of seq 2 + seq 3
            yield await gen.__anext__()
        await gen.aclose()

    monkeypatch.setattr(stream_mod, "_event_stream", _bounded)

    token = test_user["token"]
    async with asyncio.timeout(10.0):
        async with api_client.stream(
            "GET", f"/api/stream?token={token}", headers={"Last-Event-ID": "1"}
        ) as response:
            assert response.status_code == 200
            body = b""
            async for chunk in response.aiter_bytes():
                body += chunk

    frames = [b for b in re.split(rb"\r?\n\r?\n", body) if b.strip()]
    ids, types = [], []
    for frame in frames:
        for line in frame.splitlines():
            if line.startswith(b"id:"):
                ids.append(line[3:].strip().decode())
            elif line.startswith(b"data:"):
                types.append(json.loads(line[5:].strip()).get("type"))

    assert ids == ["2", "3"]  # replayed in order, no resync
    assert types == ["connected", "run.start", "run.start"]
    assert "stream.resync" not in types
