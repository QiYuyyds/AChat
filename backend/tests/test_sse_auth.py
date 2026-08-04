"""Tests for SSE stream authentication (app/api/stream.py).

Verifies that SSE connections without a token are rejected (401) and that
authenticated connections receive events (single-user mode — no user_id filtering).
"""

from __future__ import annotations

import asyncio
import json

from app.schemas.events import RunStartEvent
from app.services.event_bus import event_bus


async def test_sse_without_token_returns_401(raw_client):
    """SSE connection without any token gets 401."""
    resp = await raw_client.get("/api/stream")
    assert resp.status_code == 401


async def test_sse_with_invalid_token_returns_401(raw_client):
    """SSE connection with an invalid token gets 401."""
    resp = await raw_client.get("/api/stream?token=invalid.token.here")
    assert resp.status_code == 401


async def test_sse_with_valid_token_resolves_user(db, test_user):
    """_resolve_sse_user returns user_id for a valid token."""
    from app.api.stream import _resolve_sse_user

    user_id = await _resolve_sse_user(None, test_user["token"])
    assert user_id == test_user["id"]


async def test_event_bus_delivers_to_all_subscribers():
    """EventBus delivers events to all subscribers (single-user mode — no user_id filtering)."""
    from app.api.stream import _event_stream

    # User A subscribes
    gen_a = _event_stream(user_id="user_a")
    # User B subscribes
    gen_b = _event_stream(user_id="user_b")

    try:
        # Consume the initial "connected" frames
        await gen_a.__anext__()
        await gen_b.__anext__()

        # Publish an event for user A
        event_a = RunStartEvent(
            conversation_id="conv_a",
            timestamp=123,
            run_id="run_a",
            agent_id="agent_a",
            trigger_message_id="msg_a",
        )
        event_bus.publish(event_a, user_id="user_a")

        # Both users receive it (single-user mode — no filtering)
        frame_a = await asyncio.wait_for(gen_a.__anext__(), timeout=2.0)
        payload_a = json.loads(frame_a["data"])
        assert payload_a["type"] == "run.start"
        assert payload_a["runId"] == "run_a"

        frame_b = await asyncio.wait_for(gen_b.__anext__(), timeout=2.0)
        payload_b = json.loads(frame_b["data"])
        assert payload_b["type"] == "run.start"
        assert payload_b["runId"] == "run_a"

    finally:
        await gen_a.aclose()
        await gen_b.aclose()


async def test_event_bus_broadcasts_no_user_events():
    """Events without user_id are broadcast to all subscribers."""
    from app.api.stream import _event_stream

    gen_a = _event_stream(user_id="user_a")
    gen_b = _event_stream(user_id="user_b")

    try:
        await gen_a.__anext__()
        await gen_b.__anext__()

        # Publish a broadcast event (user_id=None)
        event = RunStartEvent(
            conversation_id="conv_bc",
            timestamp=456,
            run_id="run_bc",
            agent_id="agent_bc",
            trigger_message_id="msg_bc",
        )
        event_bus.publish(event, user_id=None)

        # Both users receive it
        frame_a = await asyncio.wait_for(gen_a.__anext__(), timeout=2.0)
        payload_a = json.loads(frame_a["data"])
        assert payload_a["runId"] == "run_bc"

        frame_b = await asyncio.wait_for(gen_b.__anext__(), timeout=2.0)
        payload_b = json.loads(frame_b["data"])
        assert payload_b["runId"] == "run_bc"

    finally:
        await gen_a.aclose()
        await gen_b.aclose()
