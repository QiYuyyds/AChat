"""Global SSE stream. Port of src/app/api/stream/route.ts — one connection for
all conversations; each event carries conversationId and the frontend buckets by
id. See specs/02-stream-events.md."""

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Query, Request
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from app.auth.dependencies import COOKIE_NAME
from app.auth.jwt_handler import verify_token
from app.db.models import User
from app.services.event_bus import event_bus
from app.utils.clock import now_ms

router = APIRouter()

# idle gap after which we emit a JSON heartbeat (TS uses a 15s setInterval)
_HEARTBEAT_SECONDS = 15.0


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


async def _event_stream(user_id: str | None) -> AsyncIterator[dict]:
    # data-only frames (no SSE `event:` field) so the frontend reads them via
    # EventSource.onmessage; the event type lives inside the JSON payload.
    async with event_bus.subscribe(user_id=user_id) as queue:
        # tell the client the connection is live immediately (mirrors TS hello)
        yield {"data": json.dumps({"type": "connected", "timestamp": now_ms()})}
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=_HEARTBEAT_SECONDS)
            except TimeoutError:
                yield {"data": json.dumps({"type": "heartbeat", "timestamp": now_ms()})}
                continue
            # StreamEvent is Pydantic with camelCase aliases — serialize by alias
            yield {"data": event.model_dump_json(by_alias=True)}


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
        _event_stream(user_id),
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
