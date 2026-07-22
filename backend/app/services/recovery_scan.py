"""Crash recovery scan — finds and resolves messages stuck in "streaming" status.

On startup, scans for Message rows with status="streaming" and created_at < now - 5min.
For each, if the Redis Stream exists, attempts to replay remaining events and mark
as "complete". If the Stream doesn't exist, marks as "interrupted".

Additionally scans Redis Streams for message.start events whose message rows may not
exist in PG yet (async INSERT not flushed before crash).
"""

from __future__ import annotations

import contextlib
import json
import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.engine import get_db
from app.db.models import Message
from app.utils.clock import now_ms

logger = logging.getLogger(__name__)

STUCK_THRESHOLD_MS = 5 * 60 * 1000  # 5 minutes


async def scan_interrupted_messages() -> int:
    """Find and resolve messages stuck in "streaming" status.

    Also scans Redis Streams for orphaned message.start events (messages that
    were XADD'd but never INSERTed to PG due to a crash before Consumer flush).

    Returns the number of messages resolved.
    """
    threshold = now_ms() - STUCK_THRESHOLD_MS

    async with get_db() as db:
        result = await db.execute(
            select(Message).where(
                Message.status == "streaming",
                Message.created_at < threshold,
            )
        )
        stuck_messages = result.scalars().all()

    if not stuck_messages:
        logger.info("Recovery scan: no interrupted messages found in PG")
    else:
        logger.warning(
            "Recovery scan: found %d stuck messages (status=streaming, older than 5min)",
            len(stuck_messages),
        )

    resolved = 0
    for msg in stuck_messages:
        await _resolve_stuck_message(msg)
        resolved += 1

    # Also scan Redis Streams for orphaned messages (not yet in PG)
    orphaned = await _scan_orphaned_streams()
    resolved += orphaned

    logger.info("Recovery scan: resolved %d stuck messages total", resolved)
    return resolved


async def _scan_orphaned_streams() -> int:
    """Scan Redis Streams for message.start events whose message rows don't exist in PG.

    This handles the case where a crash occurred before the DBWriterConsumer
    flushed the async INSERT for a message.start event.
    """
    from app.infra.factory import get_infrastructure
    from app.services.async_db_writer import STREAM_KEY_PREFIX

    infra = get_infrastructure()
    redis_client = infra.redis_client if infra else None
    if redis_client is None:
        return 0

    # Scan for stream keys matching the prefix
    try:
        cursor: int | bytes | str = 0
        orphaned_run_ids: set[str] = set()
        while True:
            cursor, keys = await redis_client.scan(
                cursor, match=f"{STREAM_KEY_PREFIX}*", count=100
            )
            for key in keys:
                if isinstance(key, bytes):
                    key = key.decode()
                run_id = key[len(STREAM_KEY_PREFIX):]
                orphaned_run_ids.add(run_id)
            # Redis SCAN returns cursor 0 (or b"0") when iteration is complete
            if isinstance(cursor, bytes):
                if cursor == b"0":
                    break
            elif int(cursor) == 0:
                break

        if not orphaned_run_ids:
            return 0

        resolved = 0
        for run_id in orphaned_run_ids:
            resolved += await _check_orphaned_run(run_id, redis_client)
        return resolved
    except Exception as e:
        logger.warning("Recovery scan: error scanning orphaned streams: %s", e)
        return 0


async def _check_orphaned_run(run_id: str, redis_client) -> int:
    """Check a single Redis Stream for message.start events not in PG."""
    from app.services.async_db_writer import stream_key

    key = stream_key(run_id)
    try:
        entries = await redis_client.xrange(key)
    except Exception:
        return 0

    # Find message.start events and check if their message rows exist
    message_start_events: dict[str, dict] = {}
    for _entry_id, fields in entries:
        raw = fields.get("data")
        if not raw:
            continue
        try:
            event_data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if event_data.get("type") == "message.start":
            msg_id = event_data.get("messageId")
            if msg_id:
                message_start_events[msg_id] = event_data

    if not message_start_events:
        return 0

    # Check which message rows are missing from PG
    found_ids: set[str] = set()
    async with get_db() as db:
        result = await db.execute(
            select(Message.id).where(Message.id.in_(list(message_start_events.keys())))
        )
        found_ids = {row[0] for row in result.all()}

    missing_ids = set(message_start_events.keys()) - found_ids
    if not missing_ids:
        return 0

    # INSERT missing message rows, then replay
    resolved = 0
    for msg_id in missing_ids:
        event_data = message_start_events[msg_id]
        await _insert_orphaned_message(event_data)
        # Now replay the stream for this message
        await _replay_stream_for_message(run_id, msg_id, redis_client, event_data)
        resolved += 1

    return resolved


async def _insert_orphaned_message(event_data: dict) -> None:
    """INSERT a message row from a message.start event found in Redis Stream."""
    msg_id = event_data.get("messageId", "")
    try:
        async with get_db() as db:
            await db.execute(
                pg_insert(Message).values(
                    id=msg_id,
                    conversation_id=event_data.get("conversationId", ""),
                    role="agent",
                    agent_id=event_data.get("agentId"),
                    status="streaming",
                    run_id=event_data.get("runId"),
                    created_at=event_data.get("timestamp", now_ms()),
                    hidden=event_data.get("hidden", False),
                    parts=[],
                    mentioned_agent_ids=[],
                ).on_conflict_do_nothing(index_elements=["id"])
            )
    except Exception as e:
        logger.warning(
            "Recovery scan: failed to INSERT orphaned message %s: %s",
            msg_id, e,
        )


async def _replay_stream_for_message(
    run_id: str, msg_id: str, redis_client, start_event_data: dict
) -> None:
    """Replay events from Redis Stream for a specific message, then mark complete."""
    from app.services.async_db_writer import stream_key

    key = stream_key(run_id)
    try:
        entries = await redis_client.xrange(key)
    except Exception as e:
        logger.warning(
            "Recovery scan: XRANGE failed for orphaned stream %s: %s",
            key, e,
        )
        return

    parts: list[dict] = []

    for _entry_id, fields in entries:
        raw = fields.get("data")
        if not raw:
            continue
        try:
            event_data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue

        if event_data.get("messageId") != msg_id:
            continue

        etype = event_data.get("type")

        if etype == "part.start":
            part_index = event_data.get("partIndex", 0)
            while len(parts) <= part_index:
                parts.append({})
            if part_index < len(parts):
                parts[part_index] = event_data.get("part", {})

        elif etype == "part.delta":
            part_index = event_data.get("partIndex", 0)
            if part_index < len(parts):
                part = parts[part_index]
                delta = event_data.get("delta", {})
                dtype = delta.get("type")
                text = delta.get("text", "")
                appendable = {"text.append": "text", "thinking.append": "thinking", "code.append": "code"}
                if appendable.get(dtype) == part.get("type"):
                    part["content"] = part.get("content", "") + text

        elif etype == "tool.call":
            parts.append({
                "type": "tool_use",
                "callId": event_data.get("callId"),
                "toolName": event_data.get("toolName"),
                "args": event_data.get("args"),
            })

        elif etype == "tool.result":
            parts.append({
                "type": "tool_result",
                "callId": event_data.get("callId"),
                "result": event_data.get("result"),
                "isError": event_data.get("isError"),
            })

    has_message_end = any(
        json.loads(f.get("data", "{}")).get("type") == "message.end"
        and json.loads(f.get("data", "{}")).get("messageId") == msg_id
        for _, f in entries
    )

    final_status = "complete" if has_message_end else "interrupted"

    async with get_db() as db:
        result = await db.execute(select(Message).where(Message.id == msg_id))
        db_msg = result.scalar_one_or_none()
        if db_msg is not None:
            db_msg.status = final_status
            db_msg.parts_list = parts

    logger.info(
        "Recovery scan: replayed + inserted orphaned msg %s from stream %s, marked %s",
        msg_id, key, final_status,
    )


async def _resolve_stuck_message(msg: Message) -> None:
    """Resolve a single stuck message by replaying from Stream or marking interrupted."""
    from app.infra.factory import get_infrastructure
    from app.services.async_db_writer import stream_key

    infra = get_infrastructure()
    redis_client = infra.redis_client if infra else None

    if redis_client is not None:
        try:
            stream_exists = await redis_client.exists(stream_key(msg.run_id))
            if stream_exists:
                await _replay_stream_and_complete(msg, redis_client)
                return
        except Exception as e:
            logger.warning(
                "Recovery scan: error checking stream for msg %s (run %s): %s",
                msg.id, msg.run_id, e,
            )

    # Stream doesn't exist or Redis unavailable → mark as interrupted
    await _mark_interrupted(msg.id)


async def _replay_stream_and_complete(msg: Message, redis_client) -> None:
    """Read events from Redis Stream, replay to reconstruct parts, mark complete.

    If the message row doesn't exist in PG (async INSERT not flushed), it is
    INSERTed from the message.start event before replaying.
    """
    from app.services.async_db_writer import stream_key

    key = stream_key(msg.run_id)
    try:
        entries = await redis_client.xrange(key)
    except Exception as e:
        logger.warning(
            "Recovery scan: XRANGE failed for stream %s: %s, marking interrupted",
            key, e,
        )
        await _mark_interrupted(msg.id)
        return

    # Check if message row exists in PG (might have been deleted or never INSERTed)
    msg_exists = False
    message_start_data: dict | None = None
    async with get_db() as db:
        result = await db.execute(select(Message).where(Message.id == msg.id))
        db_msg = result.scalar_one_or_none()
        msg_exists = db_msg is not None

    # Find message.start event in Stream for INSERT if needed
    if not msg_exists:
        for _entry_id, fields in entries:
            raw = fields.get("data")
            if not raw:
                continue
            try:
                event_data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if event_data.get("type") == "message.start" and event_data.get("messageId") == msg.id:
                message_start_data = event_data
                break

        if message_start_data is not None:
            await _insert_orphaned_message(message_start_data)
            logger.info(
                "Recovery scan: INSERTed missing msg %s from message.start event",
                msg.id,
            )
        else:
            logger.warning(
                "Recovery scan: msg %s not in PG and no message.start event in stream %s",
                msg.id, key,
            )
            await _mark_interrupted(msg.id)
            return

    # Replay events to reconstruct parts
    parts: list[dict] = list(msg.parts_list) if msg.parts_list else []

    for _entry_id, fields in entries:
        raw = fields.get("data")
        if not raw:
            continue
        try:
            event_data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue

        etype = event_data.get("type")
        msg_id = event_data.get("messageId")

        if msg_id != msg.id:
            continue

        if etype == "part.start":
            part_index = event_data.get("partIndex", 0)
            while len(parts) <= part_index:
                parts.append({})
            if part_index < len(parts):
                parts[part_index] = event_data.get("part", {})

        elif etype == "part.delta":
            part_index = event_data.get("partIndex", 0)
            if part_index < len(parts):
                part = parts[part_index]
                delta = event_data.get("delta", {})
                dtype = delta.get("type")
                text = delta.get("text", "")
                appendable = {"text.append": "text", "thinking.append": "thinking", "code.append": "code"}
                if appendable.get(dtype) == part.get("type"):
                    part["content"] = part.get("content", "") + text

        elif etype == "tool.call":
            parts.append({
                "type": "tool_use",
                "callId": event_data.get("callId"),
                "toolName": event_data.get("toolName"),
                "args": event_data.get("args"),
            })

        elif etype == "tool.result":
            parts.append({
                "type": "tool_result",
                "callId": event_data.get("callId"),
                "result": event_data.get("result"),
                "isError": event_data.get("isError"),
            })

    # Write final parts and mark as complete
    async with get_db() as db:
        result = await db.execute(select(Message).where(Message.id == msg.id))
        db_msg = result.scalar_one_or_none()
        if db_msg is not None:
            db_msg.status = "complete"
            db_msg.parts_list = parts

    # Clean up the Stream
    with contextlib.suppress(Exception):
        await redis_client.delete(key)

    if not msg_exists:
        logger.info(
            "Recovery scan: replayed + inserted %d events for msg %s, marked complete",
            len(entries), msg.id,
        )
    else:
        logger.info(
            "Recovery scan: replayed + updated %d events for msg %s, marked complete",
            len(entries), msg.id,
        )


async def _mark_interrupted(message_id: str) -> None:
    """Mark a message as interrupted (crash recovery without Stream data)."""
    async with get_db() as db:
        result = await db.execute(select(Message).where(Message.id == message_id))
        msg = result.scalar_one_or_none()
        if msg is not None:
            msg.status = "interrupted"

    logger.info("Recovery scan: marked msg %s as interrupted", message_id)
