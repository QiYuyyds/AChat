"""Checkpoint service — turn-level save/load/list/clean for SDK agent runs.

Stores the full ``messages`` list at each turn so a failed or cancelled run
can resume from its latest checkpoint. Checkpoints live in the
``agent_run_checkpoints`` table; retention is bounded (max 3 per run).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import delete, select

from app.db.engine import get_db
from app.db.models import AgentRunCheckpoint
from app.utils.clock import now_ms
from app.utils.ids import new_checkpoint_id

logger = logging.getLogger(__name__)

MAX_CHECKPOINTS_PER_RUN = 3


async def save_checkpoint(
    run_id: str,
    turn_number: int,
    messages: list[dict[str, Any]],
) -> AgentRunCheckpoint | None:
    """Serialize messages and persist a checkpoint, then enforce retention."""
    checkpoint_id = new_checkpoint_id()
    checkpoint = AgentRunCheckpoint(
        id=checkpoint_id,
        run_id=run_id,
        turn_number=turn_number,
        messages_json=messages,
        created_at=now_ms(),
    )
    async with get_db() as db:
        db.add(checkpoint)

    await clean_old_checkpoints(run_id, keep=MAX_CHECKPOINTS_PER_RUN)
    logger.info(
        "[checkpoint] saved run=%s turn=%d (kept ≤%d)",
        run_id, turn_number, MAX_CHECKPOINTS_PER_RUN,
    )
    return checkpoint


async def load_latest_checkpoint(run_id: str) -> AgentRunCheckpoint | None:
    """Return the checkpoint with the highest turn_number for this run."""
    async with get_db() as db:
        result = await db.execute(
            select(AgentRunCheckpoint)
            .where(AgentRunCheckpoint.run_id == run_id)
            .order_by(AgentRunCheckpoint.turn_number.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


async def list_checkpoints(run_id: str) -> list[AgentRunCheckpoint]:
    """Return all checkpoints for a run, ordered by turn_number descending."""
    async with get_db() as db:
        result = await db.execute(
            select(AgentRunCheckpoint)
            .where(AgentRunCheckpoint.run_id == run_id)
            .order_by(AgentRunCheckpoint.turn_number.desc())
        )
        return list(result.scalars().all())


async def clean_old_checkpoints(run_id: str, keep: int = MAX_CHECKPOINTS_PER_RUN) -> int:
    """Delete checkpoints beyond the latest *keep*, returning the count deleted."""
    checkpoints = await list_checkpoints(run_id)
    if len(checkpoints) <= keep:
        return 0
    to_delete = checkpoints[keep:]
    ids_to_delete = [c.id for c in to_delete]
    async with get_db() as db:
        await db.execute(
            delete(AgentRunCheckpoint).where(AgentRunCheckpoint.id.in_(ids_to_delete))
        )
    logger.info(
        "[checkpoint] cleaned %d old checkpoint(s) for run=%s (kept %d)",
        len(ids_to_delete), run_id, keep,
    )
    return len(ids_to_delete)


async def clean_run_checkpoints(run_id: str, keep_latest: bool = True) -> int:
    """After run completion, retain only the latest checkpoint (or delete all)."""
    if not keep_latest:
        async with get_db() as db:
            await db.execute(
                delete(AgentRunCheckpoint).where(AgentRunCheckpoint.run_id == run_id)
            )
        return 0

    checkpoints = await list_checkpoints(run_id)
    if len(checkpoints) <= 1:
        return 0
    to_delete = checkpoints[1:]
    ids_to_delete = [c.id for c in to_delete]
    async with get_db() as db:
        await db.execute(
            delete(AgentRunCheckpoint).where(AgentRunCheckpoint.id.in_(ids_to_delete))
        )
    logger.info(
        "[checkpoint] post-run cleanup: deleted %d for run=%s (kept latest)",
        len(ids_to_delete), run_id,
    )
    return len(ids_to_delete)
