"""Async DB writer — Redis Stream-backed write-behind buffer for event persistence.

Deferrable stream events (part.start/delta/end, tool.call/result, usage) are
XADD'd to a per-run Redis Stream. A background consumer reads batches via
XREADGROUP, groups by message_id, and flushes the latest parts_buffer state
to PostgreSQL in a single UPDATE per message.

When Redis is unavailable, persist_event falls back to synchronous DB writes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

from sqlalchemy import update

from app.db.engine import get_db
from app.db.models import Message

logger = logging.getLogger(__name__)

STREAM_KEY_PREFIX = "achat:run:"
CONSUMER_GROUP = "db_writer"
CONSUMER_NAME = "db_writer-1"
BATCH_COUNT = 50
BLOCK_MS = 1000
MAXLEN = 10000

# Registry of active parts_buffers, keyed by run_id.
# consume_stream registers its parts_buffer here so the consumer can read
# the latest parts state without replaying every Stream event.
_parts_buffers: dict[str, dict[str, list[dict]]] = {}


def register_parts_buffer(run_id: str, buf: dict[str, list[dict]]) -> None:
    """Register a parts_buffer for a run so the consumer can read it."""
    _parts_buffers[run_id] = buf


def unregister_parts_buffer(run_id: str) -> None:
    """Remove a parts_buffer registration (called on run finalization)."""
    _parts_buffers.pop(run_id, None)


def stream_key(run_id: str) -> str:
    return f"{STREAM_KEY_PREFIX}{run_id}"


async def xadd_event(redis_client: Any, run_id: str, event_json: str) -> None:
    """XADD an event to the per-run Redis Stream with MAXLEN trimming."""
    key = stream_key(run_id)
    await redis_client.xadd(key, {"data": event_json}, maxlen=MAXLEN, approximate=True)


class DBWriterConsumer:
    """Background consumer that reads events from Redis Streams and flushes to PG.

    Uses a consumer group so multiple backend instances can share the load.
    On error, logs and continues (unacked events remain for retry).
    """

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client
        self._task: asyncio.Task | None = None
        self._stopped = False

    async def start(self) -> None:
        """Start the background consumer task."""
        if self._task is not None:
            return
        self._stopped = False
        self._task = asyncio.create_task(self._run_loop())
        logger.info("DBWriterConsumer started (consumer=%s)", CONSUMER_NAME)

    async def stop(self) -> None:
        """Cancel the background consumer task and wait for cleanup."""
        self._stopped = True
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("DBWriterConsumer stopped")

    async def _run_loop(self) -> None:
        """Main loop: read batches from all known streams and flush to PG."""
        while not self._stopped:
            try:
                await self._process_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("[db_writer] error in consumer loop: %s", e)
                await asyncio.sleep(1)

    async def _process_once(self) -> None:
        """Process one batch of events from all active run streams in a single XREADGROUP."""
        if not _parts_buffers:
            await asyncio.sleep(0.1)
            return

        # Ensure consumer groups exist for all active streams, then build the read dict
        run_id_by_key: dict[str, str] = {}
        streams: dict[str, str] = {}
        for run_id in list(_parts_buffers):
            key = stream_key(run_id)
            try:
                await self._ensure_group(key)
                streams[key] = ">"
                run_id_by_key[key] = run_id
            except Exception as e:
                err_str = str(e)
                if "NOGROUP" in err_str:
                    logger.debug("[db_writer] stream %s not ready yet", key)
                else:
                    logger.warning("[db_writer] ensure_group(%s) failed: %s", key, e)

        if not streams:
            return

        try:
            results = await self._redis.xreadgroup(
                CONSUMER_GROUP,
                CONSUMER_NAME,
                streams,
                count=BATCH_COUNT,
                block=BLOCK_MS,
            )
        except Exception as e:
            err_str = str(e)
            if "NOGROUP" in err_str:
                logger.debug("[db_writer] one or more streams not ready yet")
            else:
                logger.warning("[db_writer] xreadgroup failed: %s", e)
            return

        if not results:
            return

        # Route each stream's events to its parts_buffer
        for key, entries in results:
            run_id = run_id_by_key.get(key)
            buf = _parts_buffers.get(run_id) if run_id else None
            if buf is None:
                entry_ids = [eid for eid, _ in entries]
                if entry_ids:
                    await self._redis.xack(key, CONSUMER_GROUP, *entry_ids)
                continue
            await self._flush_batch(key, [(key, entries)], buf)

    async def _ensure_group(self, key: str) -> None:
        """Create the consumer group if it doesn't exist (idempotent)."""
        try:
            await self._redis.xgroup_create(key, CONSUMER_GROUP, id="0", mkstream=True)
        except Exception as e:
            err_str = str(e)
            if "BUSYGROUP" not in err_str:
                raise

    async def _flush_batch(
        self,
        key: str,
        events: list,
        parts_buffer: dict[str, list[dict]],
    ) -> None:
        """Flush a batch of events to PG, grouping by message_id.

        For each unique message_id in the batch, read the latest parts from
        parts_buffer and do a single UPDATE.
        """
        # events is [(key, [(id, {data: json_str}), ...]), ...]
        message_ids: set[str] = set()
        entry_ids: list[str] = []

        for _stream_key, entries in events:
            for entry_id, fields in entries:
                entry_ids.append(entry_id)
                try:
                    data = json.loads(fields.get("data", "{}"))
                    msg_id = data.get("messageId")
                    if msg_id:
                        message_ids.add(msg_id)
                except (json.JSONDecodeError, TypeError):
                    logger.warning("[db_writer] failed to parse event data for entry %s", entry_id)

        if not message_ids:
            if entry_ids:
                await self._redis.xack(key, CONSUMER_GROUP, *entry_ids)
            return

        # Flush each message's latest parts to PG via direct UPDATE (no SELECT)
        async with get_db() as db:
            for msg_id in message_ids:
                parts = parts_buffer.get(msg_id)
                if parts is None:
                    continue
                await db.execute(
                    update(Message)
                    .where(Message.id == msg_id)
                    .values(parts=parts)
                )

        # XACK all processed entries
        if entry_ids:
            await self._redis.xack(key, CONSUMER_GROUP, *entry_ids)


_writer_instance: DBWriterConsumer | None = None


def get_db_writer() -> DBWriterConsumer | None:
    return _writer_instance


async def start_db_writer(redis_client: Any) -> None:
    """Create and start the global DBWriterConsumer."""
    global _writer_instance
    if _writer_instance is not None:
        return
    _writer_instance = DBWriterConsumer(redis_client)
    await _writer_instance.start()


async def stop_db_writer() -> None:
    """Stop and clear the global DBWriterConsumer."""
    global _writer_instance
    if _writer_instance is not None:
        await _writer_instance.stop()
        _writer_instance = None
