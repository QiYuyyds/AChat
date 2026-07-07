"""summary_generate hook — triggers conversation summary generation on run end.

Registers an on_run_end handler that notifies the summary generation system.
The actual summary write is handled by the existing post-run
asyncio.create_task in execute_run; this hook exists for future agents that
opt into hook-based summary generation via hook_names configuration.
"""

from __future__ import annotations

import logging

from app.services.hook_registry import HookContext, HookEvent, HookRegistry, HookResult

logger = logging.getLogger(__name__)


async def _on_run_end(ctx: HookContext) -> HookResult | None:
    """Notify summary generation system that a run has completed."""
    try:
        logger.info(
            "[summary_generate] on_run_end run=%s agent=%s conv=%s",
            ctx.run_id,
            ctx.agent_id,
            ctx.conversation_id,
        )
    except Exception as e:
        logger.warning("[summary_generate] error: %s", e)
    return None


def register(registry: HookRegistry) -> None:
    registry.register(HookEvent.ON_RUN_END, _on_run_end, priority=25, name="summary_generate")
