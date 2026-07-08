"""checkpoint hook — saves turn-level checkpoints after each turn.

Registers a post_turn handler that checks ``agent.checkpoint_enabled`` and
persists the current ``messages`` list via ``checkpoint_service.save_checkpoint``.
Best-effort: errors are logged but never block the run.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.db.engine import get_db
from app.db.models import Agent
from app.services.checkpoint_service import clean_old_checkpoints, save_checkpoint
from app.services.hook_registry import HookContext, HookEvent, HookRegistry, HookResult

logger = logging.getLogger(__name__)


async def _post_turn(ctx: HookContext) -> HookResult | None:
    """Save a checkpoint if the agent has checkpoint_enabled."""
    if ctx.messages is None:
        return None

    try:
        async with get_db() as db:
            agent = await db.get(Agent, ctx.agent_id)
            if agent is None or not agent.checkpoint_enabled:
                return None

        await save_checkpoint(
            run_id=ctx.run_id,
            turn_number=ctx.turn_number,
            messages=ctx.messages,
        )
        await clean_old_checkpoints(ctx.run_id, keep=3)
    except Exception as e:
        logger.warning("[checkpoint] post_turn error: %s", e)

    return None


def register(registry: HookRegistry) -> None:
    registry.register(HookEvent.POST_TURN, _post_turn, priority=20, name="checkpoint")
