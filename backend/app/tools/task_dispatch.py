"""TaskDispatch tool — any agent uses it to clone itself or dispatch to group members.

When called without ``agentId`` (or with the caller's own ``agent_id``), the tool
clones the calling agent — a full copy with the same model, tools, and system
prompt, running with an isolated task prompt. Clone-subagent messages are
persisted with ``hidden=True`` to prevent context pollution.

When called with a different ``agentId`` in coordinated mode, it dispatches to
a group member (existing behavior). Group-member dispatch messages are visible.

The tool enforces:
- ``MAX_DISPATCH_DEPTH`` limit (clone-self can recurse up to 3 levels)
- Anti-loop: subagent runs can only clone themselves, not dispatch to other agents
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from app.db.engine import get_db
from app.db.models import Agent, AgentRun, Conversation
from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok

logger = logging.getLogger(__name__)

TASK_DISPATCH_TOOL_NAME = "task_dispatch"

_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["taskDescription"],
    "properties": {
        "agentId": {
            "type": "string",
            "description": (
                "Optional: the ID of a group member to dispatch to (coordinated mode only). "
                "When omitted, the calling agent clones itself for the subtask. "
                "Clone-self is the default and works in all modes."
            ),
        },
        "taskDescription": {
            "type": "string",
            "description": (
                "A clear, self-contained description of the task. The sub-agent "
                "will not see the conversation context, so include all necessary "
                "information."
            ),
        },
        "dependsOn": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Optional list of prior task_dispatch call IDs that must complete "
                "before this one. Advisory only — the orchestrator controls "
                "ordering by sequencing tool calls."
            ),
        },
    },
}


async def _handler(args: Any, ctx: ToolContext) -> ToolResult:
    agent_id_arg = args.get("agentId") if isinstance(args, dict) else None
    task_description = (
        args.get("taskDescription") if isinstance(args, dict) else None
    )

    if not task_description or not isinstance(task_description, str):
        return err("task_dispatch requires 'taskDescription' (string)")

    # Lazy import to avoid circular dependency at module load
    from app.services.agent_loop import MAX_DISPATCH_DEPTH, spawn_subagent_loop

    # Depth check: prevent excessive recursion
    if ctx.dispatch_depth >= MAX_DISPATCH_DEPTH:
        return err(
            f"Max dispatch depth ({MAX_DISPATCH_DEPTH}) reached; "
            "cannot dispatch further subagents"
        )

    # Determine dispatch mode: clone-self vs group-member
    is_clone = (
        agent_id_arg is None
        or not isinstance(agent_id_arg, str)
        or agent_id_arg == ctx.agent_id
    )

    # Anti-loop: non-coordinated mode can only clone itself
    if not is_clone and ctx.dispatch_mode != "coordinated":
        return err(
            "Subagent can only clone itself; cannot dispatch to other agents"
        )

    if is_clone:
        target_agent_id = ctx.agent_id
        visibility = "hidden"
    else:
        target_agent_id = agent_id_arg
        visibility = "visible"

        # Verify the target agent exists and is in the conversation
        async with get_db() as db:
            agent = (
                await db.execute(select(Agent).where(Agent.id == target_agent_id))
            ).scalar_one_or_none()
            if agent is None:
                return err(f"Agent '{target_agent_id}' not found")

            conv = (
                await db.execute(
                    select(Conversation).where(
                        Conversation.id == ctx.conversation_id
                    )
                )
            ).scalar_one_or_none()
            if conv is not None and target_agent_id not in conv.agent_ids_list:
                return err(
                    f"Agent '{target_agent_id}' is not in conversation "
                    f"'{ctx.conversation_id}'"
                )

    # Get trigger_message_id from the parent run
    async with get_db() as db:
        parent_run = (
            await db.execute(
                select(AgentRun).where(AgentRun.id == ctx.run_id)
            )
        ).scalar_one_or_none()
        trigger_message_id = (
            parent_run.trigger_message_id if parent_run else ctx.conversation_id
        )

    logger.info(
        "[task_dispatch] run=%s agent=%s dispatching to agent=%s "
        "mode=%s depth=%d visibility=%s",
        ctx.run_id,
        ctx.agent_id,
        target_agent_id,
        ctx.dispatch_mode,
        ctx.dispatch_depth,
        visibility,
    )

    result = await spawn_subagent_loop(
        agent_id=target_agent_id,
        task_description=task_description,
        conversation_id=ctx.conversation_id,
        trigger_message_id=trigger_message_id,
        parent_run_id=ctx.run_id,
        parent_cancel_event=ctx.cancel_event,
        dispatch_depth=ctx.dispatch_depth + 1,
        dispatch_visibility=visibility,
    )

    if result.status == "aborted":
        return err(f"Sub-agent run was aborted: {result.text}")

    return ok(
        {
            "status": result.status,
            "summary": result.text,
        }
    )


task_dispatch_tool = ToolDef(
    name=TASK_DISPATCH_TOOL_NAME,
    description=(
        "Dispatch a task to a sub-agent. When called without agentId, clones "
        "yourself for the subtask (messages hidden from conversation). When "
        "called with a group member's agentId (coordinated mode only), "
        "dispatches to that agent (messages visible). Multiple task_dispatch "
        "calls in a single response run in parallel. Use this to split complex "
        "tasks into independent subtasks."
    ),
    parameters=_PARAMETERS,
    handler=_handler,
)
