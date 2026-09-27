"""Global SSE stream. Port of src/app/api/stream/route.ts — one connection for
all conversations; each event carries conversationId and the frontend buckets by
id. See specs/02-stream-events.md.

Reconnect replay: every event frame carries ``id: <seq>`` (monotonic seq from
the event bus). On reconnect EventSource sends ``Last-Event-ID``; events still
in the bus's replay buffer are re-sent before the live loop. When the gap
cannot be replayed (buffer overflow, unknown/stale seq after a server
restart), a synthetic ``stream.resync`` frame tells the client to reconcile
the active conversation via REST instead.
"""

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Query, Request
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from app.auth.dependencies import COOKIE_NAME
from app.auth.jwt_handler import verify_token
from app.db.models import User
from app.schemas.events import StreamResyncEvent
from app.services.event_bus import event_bus
from app.utils.clock import now_ms

router = APIRouter()

# idle gap after which we emit a JSON heartbeat (TS uses a 15s setInterval)
_HEARTBEAT_SECONDS = 15.0


def _resync_frame() -> dict:
    """Synthetic ``stream.resync`` frame (no SSE ``id:`` — the client must
    reconcile via REST rather than resume from a sequence id)."""
    event = StreamResyncEvent(timestamp=now_ms())
    return {"data": event.model_dump_json(by_alias=True)}


def _parse_last_event_id(raw: str | None) -> int | None:
    """``Last-Event-ID`` header value → seq, or None when absent/unparseable."""
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


async def _resolve_sse_user(request: Request, token: str | None) -> str | None:
    """Resolve the user_id for an SSE connection.

    Token may come from the cookie (same-origin / production) or from the
    ``?token=`` query param (cross-origin dev — EventSource cannot set
    Authorization headers). Returns the user_id or None if unauthenticated.

    桌面模式例外：无条件解析为固定本地用户（platform-security delta）。
    """
    from app.auth.desktop import LOCAL_USER_ID, get_or_seed_local_user, is_desktop_mode

    if is_desktop_mode():
        from app.db.engine import get_remote_db
        async with get_remote_db() as db:
            user = await get_or_seed_local_user(db)
        return user.id if user else LOCAL_USER_ID

    if not token:
        return None
    try:
        payload = verify_token(token, expected_type="access")
    except Exception:
        return None
    user_id = payload.get("sub")
    token_ver = payload.get("ver", 0)
    if not user_id:
        return None

    # Verify the user still exists and token_version matches
    from app.db.engine import get_remote_db
    async with get_remote_db() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user is None or user.token_version != token_ver:
            return None
    return user_id


async def _event_stream(
    user_id: str | None, last_event_id: str | None = None
) -> AsyncIterator[dict]:
    # data-only frames (no SSE `event:` field) so the frontend reads them via
    # EventSource.onmessage; the event type lives inside the JSON payload and
    # the replay seq goes into the SSE `id:` field.
    async with event_bus.subscribe(user_id=user_id) as sub:
        # Reconnect replay. Snapshot the buffer synchronously BEFORE the first
        # yield: publish() never awaits, so everything published up to this
        # point is in the buffer and everything after lands in the queue —
        # an exact replay/live cut with no overlap (specs/02-stream-events.md).
        last_seq = _parse_last_event_id(last_event_id)
        replayed = event_bus.replay_since(last_seq) if last_seq is not None else None
        # A present-but-unusable Last-Event-ID (unparseable, unknown, gap
        # beyond the buffer, or post-restart seq) must resync, never replay
        # partially — a partial replay would silently lose events.
        must_resync = bool(last_event_id) and replayed is None

        # tell the client the connection is live immediately (mirrors TS hello)
        yield {"data": json.dumps({"type": "connected", "timestamp": now_ms()})}

        if must_resync:
            yield _resync_frame()
        elif replayed:
            for seq, event in replayed:
                yield {"id": str(seq), "data": event.model_dump_json(by_alias=True)}

        while True:
            # Overflow compensation: a resync must precede the next regular
            # event after this subscriber shed queued events.
            if sub.needs_resync:
                sub.needs_resync = False
                yield _resync_frame()
            try:
                seq, event = await asyncio.wait_for(sub.queue.get(), timeout=_HEARTBEAT_SECONDS)
            except TimeoutError:
                yield {"data": json.dumps({"type": "heartbeat", "timestamp": now_ms()})}
                continue
            # Overflow may also have happened while older events were still
            # queued ahead of this one — keep the resync-before-event order.
            if sub.needs_resync:
                sub.needs_resync = False
                yield _resync_frame()
            # StreamEvent is Pydantic with camelCase aliases — serialize by alias
            yield {"id": str(seq), "data": event.model_dump_json(by_alias=True)}


@router.get("/stream")
async def stream_events(
    request: Request,
    token: str | None = Query(default=None),
) -> EventSourceResponse:
    # Try cookie first, then ?token= query param (for cross-origin dev)
    resolved_token = token or request.cookies.get(COOKIE_NAME)
    user_id = await _resolve_sse_user(request, resolved_token)
    if user_id is None:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=401,
            content={"detail": "Not authenticated"},
        )
    return EventSourceResponse(
        _event_stream(user_id, request.headers.get("last-event-id")),
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
