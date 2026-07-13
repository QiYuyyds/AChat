"""Crash recovery scan — finds and resolves messages stuck in "streaming" status.

On startup, scans for Message rows with status="streaming" and created_at < now - 5min.
For each, if the Redis Stream exists, attempts to replay remaining events and mark
as "complete". If the Stream doesn't exist, marks as "interrupted".
"""

from __future__ import annotations

import contextlib
import json
import logging

from sqlalchemy import select

from app.db.engine import get_db
from app.db.models import Message
from app.utils.clock import now_ms

logger = logging.getLogger(__name__)

STUCK_THRESHOLD_MS = 5 * 60 * 1000  # 5 minutes


async def scan_interrupted_messages() -> int:
    """Find and resolve messages stuck in "streaming" status.

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
        logger.info("Recovery scan: no interrupted messages found")
        return 0

    logger.warning(
        "Recovery scan: found %d stuck messages (status=streaming, older than 5min)",
        len(stuck_messages),
    )

    resolved = 0
    for msg in stuck_messages:
        await _resolve_stuck_message(msg)
        resolved += 1

    logger.info("Recovery scan: resolved %d stuck messages", resolved)
    return resolved


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
    """Read events from Redis Stream, replay to reconstruct parts, mark complete."""
    from app.services.async_db_writer import stream_key

    key = stream_key(msg.run_id)
    try:
        # Read all events from the Stream
        entries = await redis_client.xrange(key)
    except Exception as e:
        logger.warning(
            "Recovery scan: XRANGE failed for stream %s: %s, marking interrupted",
            key, e,
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

    logger.info(
        "Recovery scan: replayed %d events for msg %s, marked complete",
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
