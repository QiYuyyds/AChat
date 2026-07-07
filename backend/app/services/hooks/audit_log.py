"""audit_log hook — records tool call audit logs.

Registers pre_tool_use and post_tool_use handlers that log tool invocations
with tool_name, args, result, is_error, run_id, and timestamp.
"""

from __future__ import annotations

import logging

from app.services.hook_registry import HookContext, HookEvent, HookRegistry, HookResult

logger = logging.getLogger(__name__)


async def _pre_tool_use(ctx: HookContext) -> HookResult | None:
    logger.info(
        "[audit] pre_tool_use run=%s tool=%s call_id=%s args=%s",
        ctx.run_id,
        ctx.tool_name,
        ctx.call_id,
        str(ctx.args)[:200],
    )
    return None


async def _post_tool_use(ctx: HookContext) -> HookResult | None:
    logger.info(
        "[audit] post_tool_use run=%s tool=%s call_id=%s is_error=%s result=%s",
        ctx.run_id,
        ctx.tool_name,
        ctx.call_id,
        ctx.is_error,
        str(ctx.result)[:200],
    )
    return None


def register(registry: HookRegistry) -> None:
    registry.register(HookEvent.PRE_TOOL_USE, _pre_tool_use, priority=0, name="audit_pre_tool")
    registry.register(HookEvent.POST_TOOL_USE, _post_tool_use, priority=0, name="audit_post_tool")
