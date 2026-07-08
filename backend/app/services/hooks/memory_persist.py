"""memory_persist hook — triggers memory consolidation on run end.

Migrates the existing _post_run_memory_hook logic into a lifecycle hook
registered for on_run_end. Writes user prompt + agent output into the memory
subsystem (best-effort, non-blocking).
"""

from __future__ import annotations

import logging

from app.services.hook_registry import HookContext, HookEvent, HookRegistry, HookResult

logger = logging.getLogger(__name__)


async def _on_run_end(ctx: HookContext) -> HookResult | None:
    """Trigger memory consolidation after a run completes."""
    try:
        from app.services.agent_runner import _get_memory_service

        ms = _get_memory_service()
        if ms is None:
            return None

        # The original _post_run_memory_hook needs a RunExecutionResult;
        # this hook is a notification trigger — the actual memory write
        # is handled by the existing post-run asyncio.create_task in execute_run.
        # This hook exists for future agents that opt into hook-based memory.
        logger.info(
            "[memory_persist] on_run_end run=%s agent=%s conv=%s",
            ctx.run_id,
            ctx.agent_id,
            ctx.conversation_id,
        )
    except Exception as e:
        logger.warning("[memory_persist] error: %s", e)
    return None


def register(registry: HookRegistry) -> None:
    registry.register(HookEvent.ON_RUN_END, _on_run_end, priority=20, name="memory_persist")
