"""auto_compact hook — checks if turn-level compaction is needed.

Registers a post_turn handler that reuses P0's token estimation logic to
check whether the conversation should be compacted after each turn.
"""

from __future__ import annotations

import logging

from app.services.hook_registry import HookContext, HookEvent, HookRegistry, HookResult

logger = logging.getLogger(__name__)


async def _post_turn(ctx: HookContext) -> HookResult | None:
    """Check if turn-level compaction should trigger after this turn."""
    try:
        from app.services.agent_runner import _maybe_auto_compact_hook

        # Delegate to the existing auto-compact logic; the hook wrapper
        # allows per-agent opt-in via hook_names configuration.
        await _maybe_auto_compact_hook(
            conversation_id=ctx.conversation_id,
            agent_id=ctx.agent_id,
        )
    except Exception as e:
        logger.warning("[auto_compact] post_turn error: %s", e)
    return None


def register(registry: HookRegistry) -> None:
    registry.register(HookEvent.POST_TURN, _post_turn, priority=15, name="auto_compact")
