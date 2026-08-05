"""tool_approval hook — intercepts tools requiring user approval.

Registers a pre_tool_use handler that checks:
1. Bash blacklist (rejects destructive commands)
2. Bash approval gate (requires user approval for package installs, etc.)
3. fs_write review mode (requires user approval for file writes)

When a tool is denied, returns HookResult(action="deny") with the reason.
When a tool requires approval and the user rejects, returns deny.
Otherwise returns None (allow).
"""

from __future__ import annotations

import logging

from app.services.hook_registry import HookContext, HookEvent, HookRegistry, HookResult
from app.utils.platform import IS_WINDOWS

logger = logging.getLogger(__name__)

_PLATFORM = "windows" if IS_WINDOWS else "posix"


async def _pre_tool_use(ctx: HookContext) -> HookResult | None:
    """Check if a tool call should be denied or requires approval."""
    if ctx.tool_name is None:
        return None

    # ── Bash blacklist ──
    if ctx.tool_name == "bash":
        from app.utils.security import find_banned_pattern

        command = ""
        if isinstance(ctx.args, dict):
            command = str(ctx.args.get("command", ""))
        banned = find_banned_pattern(command, _PLATFORM)
        if banned:
            return HookResult(
                action="deny",
                data=f"Command rejected by safety policy: {banned}",
            )

        # ── Bash approval gate ──
        from app.services.bash_command_approval import classify_bash_approval

        approval = classify_bash_approval(command, _PLATFORM)
        if approval.required:
            from app.infra.cache_helpers import get_workspace_cached
            from app.services.bash_command_approval import wait_for_bash_approval
            from app.utils.workspace_utils import get_effective_cwd

            workspace = await get_workspace_cached(ctx.conversation_id)
            if workspace is None:
                return HookResult(action="deny", data="Workspace not found")

            # Sandbox workspaces are isolated (directory + quota limits), so
            # skip the interactive approval gate and let the agent run unattended.
            if workspace.mode == "sandbox":
                return None

            cwd = get_effective_cwd(workspace)
            if isinstance(ctx.args, dict) and ctx.args.get("cwd"):
                from app.utils.workspace_utils import assert_path_within_workspace

                try:
                    cwd = assert_path_within_workspace(workspace, ctx.args["cwd"])
                except ValueError as e:
                    return HookResult(action="deny", data=str(e))

            import asyncio

            cancel_event = asyncio.Event()
            approved = await wait_for_bash_approval(
                conversation_id=ctx.conversation_id,
                agent_id=ctx.agent_id,
                run_id=ctx.run_id,
                command=command,
                cwd=cwd,
                reason=approval.reason,
                cancel_event=cancel_event,
                user_id=ctx.user_id,
            )
            if not approved:
                return HookResult(
                    action="deny",
                    data=f"User rejected command execution: {approval.reason}",
                )

    # ── fs_write review mode ──
    elif ctx.tool_name == "fs_write":
        from sqlalchemy import select

        from app.db.engine import get_local_db
        from app.db.models import Conversation

        async with get_local_db() as db:
            result = await db.execute(
                select(Conversation).where(Conversation.id == ctx.conversation_id)
            )
            conv = result.scalar_one_or_none()
        mode = conv.fs_write_approval_mode if conv else "review"

        if mode == "review":
            # In review mode, the fs_write tool handler itself manages the
            # approval flow (pending_writes + await_pending_decision).
            # The hook just signals that review is active — the tool handler
            # will block until the user approves or rejects.
            # We return None (allow) so the tool handler runs and manages approval.
            return None

    return None


def register(registry: HookRegistry) -> None:
    registry.register(HookEvent.PRE_TOOL_USE, _pre_tool_use, priority=5, name="tool_approval")
