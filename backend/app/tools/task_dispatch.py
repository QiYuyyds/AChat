"""TaskDispatch tool — orchestrator uses it to launch sub-agents within its loop.

Analogous to Claude Code's Agent tool. When the orchestrator calls task_dispatch,
the system synchronously runs a sub-agent loop with the given agent and task
description, then returns the sub-agent's final text output to the orchestrator's
loop context.

Only available in coordinated mode (dispatch_mode='orchestrated'). The tool name
is included in the orchestrator's tool list by _run_coordinated_loop in agent_loop.py.
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
    "required": ["agentId", "taskDescription"],
    "properties": {
        "agentId": {
            "type": "string",
            "description": (
                "The ID of the agent to dispatch the task to. Must be an agent "
                "in the current conversation."
            ),
        },
        "taskDescription": {
            "type": "string",
            "description": (
                "A clear, self-contained description of the task. The sub-agent "
                "will not see the group chat context, so include all necessary "
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
    agent_id = args.get("agentId") if isinstance(args, dict) else None
    task_description = (
        args.get("taskDescription") if isinstance(args, dict) else None
    )

    if not agent_id or not isinstance(agent_id, str):
        return err("task_dispatch requires 'agentId' (string)")
    if not task_description or not isinstance(task_description, str):
        return err("task_dispatch requires 'taskDescription' (string)")

    # Verify the target agent exists and is in the conversation
    async with get_db() as db:
        agent = (
            await db.execute(select(Agent).where(Agent.id == agent_id))
        ).scalar_one_or_none()
        if agent is None:
            return err(f"Agent '{agent_id}' not found")

        conv = (
            await db.execute(
                select(Conversation).where(Conversation.id == ctx.conversation_id)
            )
        ).scalar_one_or_none()
        if conv is not None and agent_id not in conv.agent_ids_list:
            return err(
                f"Agent '{agent_id}' is not in conversation '{ctx.conversation_id}'"
            )

        # Get trigger_message_id from the parent run
        parent_run = (
            await db.execute(
                select(AgentRun).where(AgentRun.id == ctx.run_id)
            )
        ).scalar_one_or_none()
        trigger_message_id = (
            parent_run.trigger_message_id if parent_run else ctx.conversation_id
        )

    # Lazy import to avoid circular dependency at module load
    from app.services.agent_loop import spawn_subagent_loop

    logger.info(
        "[task_dispatch] orchestrator run=%s dispatching to agent=%s",
        ctx.run_id,
        agent_id,
    )

    result = await spawn_subagent_loop(
        agent_id=agent_id,
        task_description=task_description,
        conversation_id=ctx.conversation_id,
        trigger_message_id=trigger_message_id,
        parent_run_id=ctx.run_id,
        parent_cancel_event=ctx.cancel_event,
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
        "Dispatch a task to another agent in the conversation. The sub-agent "
        "will run independently and return its result. Multiple task_dispatch "
        "calls in a single response will be executed in parallel. Use this "
        "to leverage other agents' specialized capabilities, especially when "
        "multiple independent tasks can run concurrently."
    ),
    parameters=_PARAMETERS,
    handler=_handler,
)
