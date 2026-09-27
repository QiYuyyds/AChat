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
  - :meth:`EventBus.subscribe` is an async context manager yielding the
    subscriber handle; the SSE layer drains its queue and serializes events to
    the wire.

Every published event is assigned a globally monotonic ``seq`` and kept in a
bounded replay buffer (:meth:`EventBus.replay_since`) so the SSE layer can
replay what a client missed across a reconnect (SSE ``Last-Event-ID``). When a
replay cannot cover the gap — or a slow subscriber's queue overflowed and shed
events — the SSE layer sends a synthetic ``stream.resync`` and the client
reconciles via REST (see specs/02-stream-events.md §SSE 编码).

Single-user mode: all events are delivered to all subscribers.
The ``user_id`` parameter on ``publish`` and ``subscribe`` is retained for
API compatibility but ignored — local data is single-user.

Single-process and local-first, so at most a handful of concurrent subscribers
(a desktop tab plus an occasional phone). Queues are bounded; on overflow we
drop the oldest event for that one slow subscriber, mark it ``needs_resync``,
and let the REST reconciliation compensate for what was shed rather than
wedging the whole bus.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from app.config import get_settings
from app.schemas.events import StreamEvent

logger = logging.getLogger(__name__)

# Generous per-subscriber buffer; a healthy SSE client drains far faster than
# events are produced. If one stalls past this, we shed its oldest events.
_QUEUE_MAXSIZE = 1000


@dataclass(eq=False)
class _Subscriber:
    """Internal subscriber handle — binds a user_id to its event queue.

    Queue entries are ``(seq, event)`` pairs; ``seq`` is the transport-layer
    sequence number assigned by :meth:`EventBus.publish` (written as the SSE
    ``id:`` field, never part of the event payload).
    """

    user_id: str | None
    queue: asyncio.Queue[tuple[int, StreamEvent]] = field(
        default_factory=lambda: asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
    )
    # Set when overflow shed events; the SSE layer injects a stream.resync
    # before the next regular event so the client reconciles via REST.
    needs_resync: bool = False

    def __hash__(self) -> int:
        return id(self)


class EventBus:
    """Fan-out hub between event producers and SSE subscribers."""

    def __init__(self, replay_buffer_size: int | None = None) -> None:
        self._subscribers: set[_Subscriber] = set()
        if replay_buffer_size is None:
            replay_buffer_size = get_settings().sse_replay_buffer
        self._replay: deque[tuple[int, StreamEvent]] = deque(maxlen=max(0, replay_buffer_size))
        self._seq = 0

    @property
    def last_seq(self) -> int:
        """Seq of the most recently published event (0 = nothing published yet)."""
        return self._seq

    def publish(self, event: StreamEvent, user_id: str | None = None) -> None:
        """Assign the next seq, buffer the event, and broadcast it (non-blocking).

        In single-user mode, all events are delivered to all subscribers.
        The ``user_id`` parameter is retained for API compatibility but ignored.
        """
        self._seq += 1
        entry = (self._seq, event)
        # deque with maxlen=0 discards immediately — SSE_REPLAY_BUFFER=0
        # disables buffering (every reconnect then resyncs).
        self._replay.append(entry)
        for sub in list(self._subscribers):
            _offer(sub, entry)

    def replay_since(self, last_seq: int) -> list[tuple[int, StreamEvent]] | None:
        """Buffered events with ``seq > last_seq``, or None when unreplayable.

        None means the server cannot resume a client from ``last_seq`` and must
        send a synthetic ``stream.resync`` instead: unknown or ahead-of-current
        id (e.g. server restart reset the counter), a gap beyond the replay
        window, or an empty buffer. A fully caught-up client (``last_seq``
        equals the newest published seq) gets an empty replay and simply
        continues live.
        """
        if not self._replay:
            return None
        oldest = self._replay[0][0]
        newest = self._seq
        if last_seq < oldest or last_seq > newest:
            return None
        return [entry for entry in self._replay if entry[0] > last_seq]

    @asynccontextmanager
    async def subscribe(
        self, user_id: str | None = None
    ) -> AsyncIterator[_Subscriber]:
        """Register a subscriber for the duration of the context.

        ``user_id`` is retained for API compatibility but ignored in
        single-user mode — all events are delivered to all subscribers.

        Usage (SSE layer)::

            async with event_bus.subscribe(user_id=current_user_id) as sub:
                while True:
                    seq, event = await sub.queue.get()
                    if sub.needs_resync:
                        ...  # yield a synthetic stream.resync first
                    yield {"id": str(seq), "data": serialize(event)}
        """
        sub = _Subscriber(user_id=user_id)
        self._subscribers.add(sub)
        try:
            yield sub
        finally:
            self._subscribers.discard(sub)

    @property
    def subscriber_count(self) -> int:
        """Number of currently-attached subscribers (diagnostics / tests)."""
        return len(self._subscribers)


def _offer(sub: _Subscriber, entry: tuple[int, StreamEvent]) -> None:
    """put_nowait with oldest-drop overflow handling."""
    try:
        sub.queue.put_nowait(entry)
    except asyncio.QueueFull:
        # Slow subscriber: shed its oldest queued event to make room and mark
        # it for resync — the dropped events are compensated by the client's
        # REST reconciliation (the promise this module's docstring has made
        # since the TS days).
        sub.needs_resync = True
        with contextlib.suppress(asyncio.QueueEmpty):
            sub.queue.get_nowait()
        try:
            sub.queue.put_nowait(entry)
        except asyncio.QueueFull:
            logger.warning(
                "event bus subscriber queue full; dropping event type=%s",
                getattr(entry[1], "type", "?"),
            )


# Module-level singleton (mirrors the TS globalThis singleton).
event_bus = EventBus()
