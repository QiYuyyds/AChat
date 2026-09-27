"""Crash recovery scan — finalizes records orphaned by the previous process.

On startup, runs from the previous process cannot still be alive (the run loop
lives in this process's memory), so every AgentRun row left in a non-terminal
status is marked "interrupted" with an error note. The same holds for Message
rows left in "streaming" status: at startup there are no live messages, so no
age threshold applies.

SQLite WAL mode automatically replays committed transactions on restart, so
only rows that were mid-run / mid-stream (not yet committed to a terminal
status) need to be resolved.

Redis Stream write-behind has been removed in the dual-DB migration. This module
now only scans the database — no Stream replay.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.db.engine import get_local_db
from app.db.models import AgentRun, Message
from app.utils.clock import now_ms

logger = logging.getLogger(__name__)

# AgentRun statuses that can only exist while the process that created them is
# alive ('queued' rows are drained by the in-process FIFO queue). Everything
# else is terminal.
ORPHANABLE_RUN_STATUSES = ("queued", "running")

ORPHANED_RUN_ERROR = "interrupted by restart"


async def scan_interrupted_messages() -> int:
    """Find and resolve records orphaned by the previous process.

    Marks all non-terminal AgentRun rows and all streaming Message rows as
    "interrupted". Returns the number of records resolved.
    """
    resolved = 0

    async with get_local_db() as db:
        orphan_runs = (
            await db.execute(
                select(AgentRun).where(AgentRun.status.in_(ORPHANABLE_RUN_STATUSES))
            )
        ).scalars().all()
        for run in orphan_runs:
            run.status = "interrupted"
            run.error = ORPHANED_RUN_ERROR
            run.finished_at = run.finished_at or now_ms()
        if orphan_runs:
            logger.warning(
                "Recovery scan: finalized %d orphaned run row(s) as interrupted",
                len(orphan_runs),
            )
            resolved += len(orphan_runs)

        stuck_messages = (
            await db.execute(select(Message).where(Message.status == "streaming"))
        ).scalars().all()
        for msg in stuck_messages:
            msg.status = "interrupted"
        if stuck_messages:
            logger.warning(
                "Recovery scan: marked %d streaming message(s) as interrupted",
                len(stuck_messages),
            )
            resolved += len(stuck_messages)

    if not resolved:
        logger.info("Recovery scan: no orphaned records found")
    else:
        logger.info("Recovery scan: resolved %d orphaned record(s) total", resolved)
    return resolved
