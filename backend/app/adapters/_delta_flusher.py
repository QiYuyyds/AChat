"""Delta coalescer for CustomAdapter streaming.

Buffers same-key ``part.delta`` events within a time window and flushes them
as a single merged event, reducing SSE event volume without changing
``part.delta`` semantics (the merged ``text`` is still an append).
The first delta of each key passes through immediately (first-token latency).

See ``openspec/changes/fix-streaming-render-freeze/`` for the coalescing
design rationale and ``openspec/changes/speed-up-first-token-latency/`` for
the first-delta passthrough.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.schemas.events import PartDeltaEvent
from app.utils.clock import now_ms

DEFAULT_WINDOW_MS = 50


@dataclass
class _Buffer:
    """Accumulates text for a single ``(part_index, delta_type)`` coalescing key."""

    conversation_id: str
    message_id: str
    part_index: int
    delta_type: str
    text: str = ""
    start_ts: int = 0


class DeltaFlusher:
    """Time-windowed coalescer for ``part.delta`` events.

    The FIRST delta of each coalescing key ``(part_index, delta_type)`` is
    returned immediately (first-token passthrough — no window wait); the
    coalescing window applies only to subsequent deltas of that key, exactly
    as before. Feed deltas via :meth:`feed`; the flusher returns a merged
    :class:`PartDeltaEvent` when the time window elapses, or ``None`` if the
    delta is still within the window. Call :meth:`flush` to drain all pending
    buffers (before non-delta events / turn end). Call :meth:`flush_for` to
    drain a specific ``(part_index, delta_type)`` key (before ``part.end``).

    See ``openspec/changes/speed-up-first-token-latency/`` for the passthrough
    rationale (event volume stays bounded: exactly +1 event per key).
    """

    def __init__(self, window_ms: int = DEFAULT_WINDOW_MS) -> None:
        self._window_ms = window_ms
        self._buffers: dict[tuple[int, str], _Buffer] = {}
        # Keys whose first delta has already passed through. Kept separate from
        # _buffers because a window merge clears the buffer but the key must
        # not pass through again (event volume stays "+1 per key").
        self._passthrough_done: set[tuple[int, str]] = set()

    def feed(
        self,
        message_id: str,
        part_index: int,
        delta_type: str,
        text: str,
        conversation_id: str,
        timestamp: int | None = None,
    ) -> PartDeltaEvent | None:
        """Buffer a delta.

        Returns a merged :class:`PartDeltaEvent` if the window has elapsed
        since the first buffered delta for this key (the merged event includes
        the current delta), else ``None``. The first delta of a key is
        returned immediately as a passthrough event.
        """
        ts = timestamp if timestamp is not None else now_ms()
        key = (part_index, delta_type)
        buf = self._buffers.get(key)
        if buf is None:
            if key not in self._passthrough_done:
                # First delta ever for this key — emit immediately so the
                # first token is not held back by the coalescing window.
                self._passthrough_done.add(key)
                return PartDeltaEvent(
                    conversation_id=conversation_id,
                    timestamp=ts,
                    message_id=message_id,
                    part_index=part_index,
                    delta={"type": delta_type, "text": text},
                )
            # First delta in a new window — buffer and wait
            self._buffers[key] = _Buffer(
                conversation_id=conversation_id,
                message_id=message_id,
                part_index=part_index,
                delta_type=delta_type,
                text=text,
                start_ts=ts,
            )
            return None
        # Add current delta to the buffer first
        buf.text += text
        if ts - buf.start_ts >= self._window_ms:
            # Window elapsed: emit merged event (includes current delta), clear buffer
            merged = PartDeltaEvent(
                conversation_id=buf.conversation_id,
                timestamp=ts,
                message_id=buf.message_id,
                part_index=buf.part_index,
                delta={"type": buf.delta_type, "text": buf.text},
            )
            del self._buffers[key]
            return merged
        return None

    def flush(self) -> list[PartDeltaEvent]:
        """Flush all pending buffers, returning merged events in insertion order."""
        if not self._buffers:
            return []
        ts = now_ms()
        events: list[PartDeltaEvent] = []
        for key in list(self._buffers.keys()):
            buf = self._buffers.pop(key)
            if buf.text:
                events.append(
                    PartDeltaEvent(
                        conversation_id=buf.conversation_id,
                        timestamp=ts,
                        message_id=buf.message_id,
                        part_index=buf.part_index,
                        delta={"type": buf.delta_type, "text": buf.text},
                    )
                )
        return events

    def flush_for(self, part_index: int, delta_type: str) -> PartDeltaEvent | None:
        """Flush only the buffer for a specific ``(part_index, delta_type)`` key."""
        key = (part_index, delta_type)
        buf = self._buffers.pop(key, None)
        if buf is None or not buf.text:
            return None
        return PartDeltaEvent(
            conversation_id=buf.conversation_id,
            timestamp=now_ms(),
            message_id=buf.message_id,
            part_index=buf.part_index,
            delta={"type": buf.delta_type, "text": buf.text},
        )
