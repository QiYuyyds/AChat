"""Crash recovery scan — finds and resolves messages stuck in "streaming" status.

On startup, scans for Message rows with status="streaming" and created_at < now - 5min.
SQLite WAL mode automatically replays committed transactions on restart, so only
messages that were mid-stream (not yet committed as "complete") need to be marked
as "interrupted".

Redis Stream write-behind has been removed in the dual-DB migration. This module
now only scans the database for stuck streaming messages — no Stream replay.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.db.engine import get_local_db
from app.db.models import Message
from app.utils.clock import now_ms

logger = logging.getLogger(__name__)

STUCK_THRESHOLD_MS = 5 * 60 * 1000  # 5 minutes


async def scan_interrupted_messages() -> int:
    """Find and resolve messages stuck in "streaming" status.

    Returns the number of messages resolved.
    """
    threshold = now_ms() - STUCK_THRESHOLD_MS

    async with get_local_db() as db:
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
        await _mark_interrupted(msg.id)
        resolved += 1

    logger.info("Recovery scan: resolved %d stuck messages total", resolved)
    return resolved


async def _mark_interrupted(message_id: str) -> None:
    """Mark a message as interrupted (crash recovery)."""
    async with get_local_db() as db:
        result = await db.execute(select(Message).where(Message.id == message_id))
        msg = result.scalar_one_or_none()
        if msg is not None:
            msg.status = "interrupted"

    logger.info("Recovery scan: marked msg %s as interrupted", message_id)
