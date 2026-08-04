"""In-process event bus.

Port of src/server/event-bus.ts. Every :class:`StreamEvent` produced by an
adapter or service is published here and fanned out to the SSE subscribers
(``GET /api/stream``).

TypeScript used Node's ``EventEmitter`` with a single ``'event'`` channel where
each subscriber filtered for itself. The Python port keeps the same "publish to
everyone, subscriber filters" contract but uses one :class:`asyncio.Queue` per
subscriber:

  - :meth:`EventBus.publish` is **synchronous** so it can be called from inside
    async service code (and, later, from adapter tasks) without ``await``. It
    never blocks: each subscriber queue gets a non-blocking ``put_nowait``.
  - :meth:`EventBus.subscribe` is an async context manager yielding the queue;
    the SSE layer drains it and serializes events to the wire.

Single-user mode: all events are delivered to all subscribers.
The ``user_id`` parameter on ``publish`` and ``subscribe`` is retained for
API compatibility but ignored — local data is single-user.

Single-process and local-first, so at most a handful of concurrent subscribers
(a desktop tab plus an occasional phone). Queues are bounded; on overflow we
drop the oldest event for that one slow subscriber, which reconciles via REST on
its next reconnect rather than wedging the whole bus.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from app.schemas.events import StreamEvent

logger = logging.getLogger(__name__)

# Generous per-subscriber buffer; a healthy SSE client drains far faster than
# events are produced. If one stalls past this, we shed its oldest events.
_QUEUE_MAXSIZE = 1000


@dataclass(eq=False)
class _Subscriber:
    """Internal subscriber handle — binds a user_id to its event queue."""

    user_id: str | None
    queue: asyncio.Queue[StreamEvent] = field(default_factory=lambda: asyncio.Queue(maxsize=_QUEUE_MAXSIZE))

    def __hash__(self) -> int:
        return id(self)


class EventBus:
    """Fan-out hub between event producers and SSE subscribers."""

    def __init__(self) -> None:
        self._subscribers: set[_Subscriber] = set()

    def publish(self, event: StreamEvent, user_id: str | None = None) -> None:
        """Broadcast an event to all subscribers (non-blocking).

        In single-user mode, all events are delivered to all subscribers.
        The ``user_id`` parameter is retained for API compatibility but ignored.
        """
        for sub in list(self._subscribers):
            _offer(sub.queue, event)

    @asynccontextmanager
    async def subscribe(
        self, user_id: str | None = None
    ) -> AsyncIterator[asyncio.Queue[StreamEvent]]:
        """Register a subscriber queue for the duration of the context.

        ``user_id`` is retained for API compatibility but ignored in
        single-user mode — all events are delivered to all subscribers.

        Usage (SSE layer)::

            async with event_bus.subscribe(user_id=current_user_id) as queue:
                while True:
                    event = await queue.get()
                    yield serialize(event)
        """
        sub = _Subscriber(user_id=user_id)
        self._subscribers.add(sub)
        try:
            yield sub.queue
        finally:
            self._subscribers.discard(sub)

    @property
    def subscriber_count(self) -> int:
        """Number of currently-attached subscribers (diagnostics / tests)."""
        return len(self._subscribers)


def _offer(queue: asyncio.Queue[StreamEvent], event: StreamEvent) -> None:
    """put_nowait with oldest-drop overflow handling."""
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        # Slow subscriber: drop its oldest queued event to make room.
        with contextlib.suppress(asyncio.QueueEmpty):
            queue.get_nowait()
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning(
                "event bus subscriber queue full; dropping event type=%s",
                getattr(event, "type", "?"),
            )


# Module-level singleton (mirrors the TS globalThis singleton).
event_bus = EventBus()
