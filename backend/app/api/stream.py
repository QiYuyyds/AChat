"""Global SSE stream. Port of src/app/api/stream/route.ts — one connection for
all conversations; each event carries conversationId and the frontend buckets by
id. See specs/02-stream-events.md."""

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from app.services.event_bus import event_bus
from app.utils.clock import now_ms

router = APIRouter()

# idle gap after which we emit a JSON heartbeat (TS uses a 15s setInterval)
_HEARTBEAT_SECONDS = 15.0


async def _event_stream() -> AsyncIterator[dict]:
    # data-only frames (no SSE `event:` field) so the frontend reads them via
    # EventSource.onmessage; the event type lives inside the JSON payload.
    async with event_bus.subscribe() as queue:
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
async def stream_events() -> EventSourceResponse:
    return EventSourceResponse(
        _event_stream(),
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )
