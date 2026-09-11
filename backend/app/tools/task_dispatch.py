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
- Handoff (catch point ②): when the dispatched run ends via the ``handoff``
  terminal tool, the handler re-dispatches the target agent in-place on the
  same worktree and visibility — merge-back/cleanup happen once, after the
  final executor finishes (chain cap: MAX_HANDOFF_CHAIN, cycle-free).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from app.db.engine import get_local_db
from app.db.models import Agent, AgentRun, Conversation
from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok
from app.tools.handoff import MAX_HANDOFF_CHAIN, HandoffPayload

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
        async with get_local_db() as db:
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
    async with get_local_db() as db:
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

    # Isolated worktree (degrades to shared workspace if None). Merge-back /
    # cleanup run once after the final executor (or after the chain ended),
    # regardless of how the loop below exits.
    from app.services.worktree_service import isolated_workspace
    from app.utils.ids import new_tool_call_id

    async with isolated_workspace(
        main_workspace=ctx.workspace_path,
        task_id=new_tool_call_id(),
        agent_id=target_agent_id,
        conversation_id=ctx.conversation_id,
        user_id=ctx.user_id,
    ) as wt:
        workspace_path_arg = wt.path if wt else None

        # ── Executor-swap loop (catch point ②) ────────────────────────────
        # Each iteration spawns one executor on the SAME worktree / visibility /
        # dispatch depth; a run that ends via handoff is replaced in-place by its
        # target.
        result = await spawn_subagent_loop(
            agent_id=target_agent_id,
            task_description=task_description,
            conversation_id=ctx.conversation_id,
            trigger_message_id=trigger_message_id,
            parent_run_id=ctx.run_id,
            parent_cancel_event=ctx.cancel_event,
            dispatch_depth=ctx.dispatch_depth + 1,
            dispatch_visibility=visibility,
            user_id=ctx.user_id,
            workspace_path=workspace_path_arg,
            allow_handoff=True,
            handoff_chain=[target_agent_id],
        )

        chain = [target_agent_id]
        rejection: str | None = None
        while (
            result.handoff is not None
            and len(chain) < MAX_HANDOFF_CHAIN
            and not ctx.cancel_event.is_set()
        ):
            handoff = result.handoff
            rejection = await _validate_redispatch_target(ctx, handoff.agent_id, chain)
            if rejection is not None:
                break
            next_agent_id = handoff.agent_id
            chain.append(next_agent_id)
            logger.info(
                "[task_dispatch] handoff re-dispatch run=%s %s -> %s (chain=%s)",
                ctx.run_id,
                chain[-2],
                next_agent_id,
                chain,
            )
            result = await spawn_subagent_loop(
                agent_id=next_agent_id,
                task_description=task_description
                + _format_handoff_note(chain[-2], handoff),
                conversation_id=ctx.conversation_id,
                trigger_message_id=trigger_message_id,
                parent_run_id=ctx.run_id,
                parent_cancel_event=ctx.cancel_event,
                dispatch_depth=ctx.dispatch_depth + 1,  # unchanged: swap, not nest
                dispatch_visibility=visibility,
                user_id=ctx.user_id,
                workspace_path=workspace_path_arg,
                allow_handoff=True,
                handoff_chain=chain,
            )

    if result.status == "aborted":
        return err(f"Sub-agent run was aborted: {result.text}")

    payload: dict[str, Any] = {
        "status": result.status,
        "summary": result.text,
    }
    if result.stop_reason:
        payload["stopReason"] = result.stop_reason

    # A pending handoff at loop exit means the last executor tried to hand off
    # but the chain ended here: cap reached, cancel, or re-validation failed.
    # Fall back to a normal tool result with an explanation — never interrupt
    # the dispatch itself.
    handoff_blocked: str | None = None
    if result.handoff is not None:
        if ctx.cancel_event.is_set():
            handoff_blocked = "用户停止了本次运行"
        elif len(chain) >= MAX_HANDOFF_CHAIN:
            handoff_blocked = f"移交链已达上限（{MAX_HANDOFF_CHAIN}）"
        else:
            handoff_blocked = rejection or "移交目标校验未通过"
        payload["status"] = "failed"
        payload["summary"] = (
            f"{chain[-1]} 尝试将任务移交给 {result.handoff.agent_id}，"
            f"但{handoff_blocked}，移交未生效。前任移交说明：{result.text}"
        )

    if len(chain) > 1 or handoff_blocked is not None:
        payload["executedBy"] = chain[-1]
        payload["handoffChain"] = chain
        await _record_dispatch_metadata(result.run_id, chain)

    return ok(payload)


async def _validate_redispatch_target(
    ctx: ToolContext, target: str, chain: list[str]
) -> str | None:
    """Second-chance validation before re-dispatch (window-race backstop).

    Returns None when the target may take over, otherwise a rejection reason.
    """
    if target in chain:
        return f"目标 {target} 已在移交链中，禁止成环"
    async with get_local_db() as db:
        conv = (
            await db.execute(
                select(Conversation).where(Conversation.id == ctx.conversation_id)
            )
        ).scalar_one_or_none()
        if conv is None or target not in conv.agent_ids_list:
            return f"目标 {target} 不在会话成员中"
        busy = (
            await db.execute(
                select(AgentRun.id).where(
                    AgentRun.conversation_id == ctx.conversation_id,
                    AgentRun.agent_id == target,
                    AgentRun.status.in_(["running", "queued"]),
                )
            )
        ).first()
    if busy is not None:
        return f"目标 {target} 正忙（已有进行中的运行）"
    return None


def _format_handoff_note(previous_agent_id: str, handoff: HandoffPayload) -> str:
    """Structured handoff summary appended to the task description for the
    successor (same format family as the DAG upstream-output block)."""
    lines = [
        "\n\n---\n"
        f"## 前任执行者移交说明（{previous_agent_id} → {handoff.agent_id}）",
        f"移交理由：{handoff.reason}",
        f"移交说明：\n{handoff.summary}",
    ]
    if handoff.files_changed:
        lines.append(f"已变更文件：{'、'.join(handoff.files_changed)}")
    if handoff.key_decisions:
        lines.append(f"关键决策：{'、'.join(handoff.key_decisions)}")
    lines.append(
        "请从剩余工作继续。你的工作目录即前任的工作目录，"
        "已产生的半成品文件可直接使用。"
    )
    return "\n".join(lines)


async def _record_dispatch_metadata(run_id: str | None, chain: list[str]) -> None:
    """Persist executedBy / handoffChain onto the final executor's run row."""
    if run_id is None:
        return
    try:
        async with get_local_db() as db:
            run = await db.get(AgentRun, run_id)
            if run is not None:
                run.dispatch_results = {
                    "executedBy": chain[-1],
                    "handoffChain": chain,
                }
    except Exception as exc:  # noqa: BLE001 - metadata is best-effort
        logger.warning("[task_dispatch] dispatch_results write failed: %s", exc)


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
