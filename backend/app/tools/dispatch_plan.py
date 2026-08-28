"""dispatch_plan tool — declarative DAG dispatch for the orchestrator.

Complements ``task_dispatch`` (single immediate dispatch) with a structured
multi-task DAG: the orchestrator declares a list of tasks with ``dependsOn``
dependencies in one tool call, and the system schedules them in topological
waves — running independent tasks in parallel within each wave.

Only available in coordinated mode. The tool is injected into the orchestrator's
tool list by ``_run_coordinated_loop`` in ``agent_loop.py``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from sqlalchemy import select

from app.db.engine import get_local_db
from app.db.models import Agent, AgentRun, AppSettings, Conversation
from app.schemas.dispatch import DispatchPlanItem
from app.schemas.events import DispatchPlanEvent
from app.services.dag_executor import DagExecContext, execute_dag, validate_dag
from app.services.event_bus import event_bus
from app.services.pending_dispatch_plans import (
    PlanReviewOutcome,
    pending_dispatch_plans,
)
from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok
from app.utils.clock import now_ms

logger = logging.getLogger(__name__)

DISPATCH_PLAN_TOOL_NAME = "dispatch_plan"

_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["tasks"],
    "properties": {
        "mode": {
            "type": "string",
            "enum": ["full", "retry"],
            "default": "full",
            "description": (
                "full = execute a new DAG from scratch; retry = re-run only "
                "failed nodes, reusing the conversation history and "
                "system_prompt from the original DAG's successful nodes. "
                "When mode=retry, originalDagId must be provided."
            ),
        },
        "originalDagId": {
            "type": "string",
            "description": (
                "Required when mode=retry. The DAG ID from the previous "
                "dispatch_plan return value. Used to look up original "
                "sessions for history reuse."
            ),
        },
        "tasks": {
            "type": "array",
            "minItems": 1,
            "description": (
                "List of tasks to execute as a DAG. Each task has an id, "
                "agentId, task description, and optional dependsOn (ids of "
                "tasks that must complete before this one starts)."
            ),
            "items": {
                "type": "object",
                "required": ["id", "task"],
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Unique task identifier within this plan.",
                    },
                    "agentId": {
                        "type": "string",
                        "description": (
                            "Optional: the ID of a group member to dispatch "
                            "this task to. When omitted, the calling agent "
                            "clones itself for the subtask."
                        ),
                    },
                    "task": {
                        "type": "string",
                        "description": (
                            "A clear, self-contained description of the task. "
                            "The sub-agent will not see the group chat context."
                        ),
                    },
                    "dependsOn": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional list of task ids that must complete "
                            "successfully before this task starts. Omit for "
                            "tasks that can run immediately."
                        ),
                    },
                    "planStepId": {
                        "type": "string",
                        "description": (
                            "Optional: the ID of a plan step (from create_plan) "
                            "to link this dispatch task to. When the task "
                            "completes, the linked plan step will be "
                            "automatically updated."
                        ),
                    },
                },
            },
        },
    },
}


async def _is_plan_approval_enabled() -> bool:
    """Check if plan approval is enabled (default: False).

    Reads from ``AppSettings.settings`` JSONB column on the remote DB.
    Returns False on any DB error so dispatch_plan never crashes.
    """
    from app.db.engine import get_remote_db

    try:
        async with get_remote_db() as db:
            row = (
                await db.execute(
                    select(AppSettings).where(AppSettings.id == "singleton")
                )
            ).scalar_one_or_none()
            if row and row.settings:
                return bool(row.settings.get("plan_approval_enabled", False))
    except Exception as exc:  # noqa: BLE001 - degrade gracefully
        logger.warning("[dispatch_plan] _is_plan_approval_enabled failed: %s", exc)
    return False


def _parse_plan_items(tasks_raw: Any) -> list[DispatchPlanItem] | str:
    """Parse raw task dicts into DispatchPlanItem list.

    Returns the list on success, or an error string on failure.
    """
    if not isinstance(tasks_raw, list) or not tasks_raw:
        return "dispatch_plan requires 'tasks' (non-empty array)"

    items: list[DispatchPlanItem] = []
    for i, t in enumerate(tasks_raw):
        if not isinstance(t, dict):
            return f"Task at index {i} must be an object"
        try:
            items.append(DispatchPlanItem.model_validate(t))
        except Exception as exc:  # noqa: BLE001 - surface validation message
            return f"Task at index {i} is invalid: {exc}"
    return items


async def _verify_agents_in_conversation(
    items: list[DispatchPlanItem], conversation_id: str, caller_agent_id: str
) -> str | None:
    """Verify all specified agent_ids exist and belong to the conversation.

    Items with ``agent_id=None`` (clone-self) are skipped — the caller's own
    agent_id will be used at execution time.

    Returns an error string if any agent is invalid, None if all are valid.
    """
    # Collect only non-None, non-self agent_ids for verification
    agent_ids = {
        item.agent_id
        for item in items
        if item.agent_id is not None and item.agent_id != caller_agent_id
    }
    if not agent_ids:
        return None

    async with get_local_db() as db:
        conv = (
            await db.execute(
                select(Conversation).where(Conversation.id == conversation_id)
            )
        ).scalar_one_or_none()
        conv_agent_ids = set(conv.agent_ids_list) if conv else set()

        result = await db.execute(
            select(Agent).where(Agent.id.in_(agent_ids))
        )
        existing_ids = {a.id for a in result.scalars().all()}

    for aid in agent_ids:
        if aid not in existing_ids:
            return f"Agent '{aid}' not found"
        if aid not in conv_agent_ids:
            return (
                f"Agent '{aid}' is not in conversation '{conversation_id}'"
            )
    return None


def _revalidation_validator(
    plan: list[DispatchPlanItem],
) -> list[DispatchPlanItem]:
    """Re-validate an (possibly user-edited) plan at approval time."""
    errors = validate_dag(plan)
    if errors:
        raise ValueError("; ".join(errors))
    return plan


async def _await_plan_approval(
    items: list[DispatchPlanItem],
    ctx: ToolContext,
) -> PlanReviewOutcome | None:
    """Register a pending plan and await the user's decision.

    Returns the ``PlanReviewOutcome`` or ``None`` if the run was cancelled
    while waiting.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[PlanReviewOutcome] = loop.create_future()

    pending_plan = pending_dispatch_plans.register(
        conversation_id=ctx.conversation_id,
        agent_id=ctx.agent_id,
        run_id=ctx.run_id,
        plan=items,
        validator=_revalidation_validator,
        user_id=ctx.user_id,
    )
    pending_dispatch_plans.attach_resolver(
        pending_plan.id,
        lambda outcome: (
            future.set_result(outcome)
            if not future.done()
            else None
        ),
    )

    cancel_task = asyncio.ensure_future(ctx.cancel_event.wait())

    try:
        done, _pending = await asyncio.wait(
            {future, cancel_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
    finally:
        if not cancel_task.done():
            cancel_task.cancel()

    if ctx.cancel_event.is_set():
        pending_dispatch_plans.cancel(pending_plan.id)
        return None

    if future in done:
        return future.result()

    pending_dispatch_plans.cancel(pending_plan.id)
    return None


async def _handler(args: Any, ctx: ToolContext) -> ToolResult:
    raw_tasks = args.get("tasks") if isinstance(args, dict) else None
    mode = args.get("mode", "full") if isinstance(args, dict) else "full"
    original_dag_id = args.get("originalDagId") if isinstance(args, dict) else None

    if mode not in ("full", "retry"):
        return err(f"Invalid mode '{mode}'; must be 'full' or 'retry'")

    if mode == "retry" and not original_dag_id:
        return err("originalDagId is required when mode='retry'")

    parsed = _parse_plan_items(raw_tasks)
    if isinstance(parsed, str):
        return err(parsed)
    items: list[DispatchPlanItem] = parsed

    # Validate DAG structure
    errors = validate_dag(items)
    if errors:
        return err("Invalid DAG: " + "; ".join(errors))

    # Depth check: prevent excessive recursion
    from app.services.agent_loop import MAX_DISPATCH_DEPTH

    if ctx.dispatch_depth >= MAX_DISPATCH_DEPTH:
        return err(
            f"Max dispatch depth ({MAX_DISPATCH_DEPTH}) reached; "
            "cannot dispatch further subagents"
        )

    logger.info(
        "[dispatch_plan] handler invoked run=%s depth=%d mode=%s tasks=%d",
        ctx.run_id, ctx.dispatch_depth, ctx.dispatch_mode, len(items),
    )

    # Anti-loop: non-coordinated mode can only clone itself
    has_group_dispatch = any(
        item.agent_id is not None and item.agent_id != ctx.agent_id
        for item in items
    )
    if has_group_dispatch and ctx.dispatch_mode != "coordinated":
        return err(
            "Subagent can only clone itself; cannot dispatch to other agents"
        )

    # Resolve clone-self: items with agent_id=None use caller's agent_id
    for item in items:
        if item.agent_id is None:
            item.agent_id = ctx.agent_id

    # Verify specified agents exist and are in the conversation
    agent_err = await _verify_agents_in_conversation(
        items, ctx.conversation_id, ctx.agent_id
    )
    if agent_err:
        return err(agent_err)

    # Optional plan approval flow
    approval_enabled = await _is_plan_approval_enabled()
    if approval_enabled:
        logger.info("[dispatch_plan] plan approval enabled, awaiting user decision run=%s", ctx.run_id)
        outcome = await _await_plan_approval(items, ctx)
        if outcome is None:
            return ok({"status": "aborted"})
        if outcome.kind == "reject":
            return ok({"status": "rejected"})
        if outcome.kind == "revise":
            return ok(
                {"status": "revise_requested", "feedback": outcome.feedback}
            )
        # approve — use the (possibly re-validated) plan
        if outcome.plan is not None:
            items = outcome.plan
        logger.info("[dispatch_plan] plan approved, proceeding run=%s", ctx.run_id)

    # Get trigger_message_id from the parent run
    async with get_local_db() as db:
        parent_run = (
            await db.execute(
                select(AgentRun).where(AgentRun.id == ctx.run_id)
            )
        ).scalar_one_or_none()
        trigger_message_id = (
            parent_run.trigger_message_id
            if parent_run
            else ctx.conversation_id
        )

    # Emit dispatch.plan event with the validated plan
    event_bus.publish(
        DispatchPlanEvent(
            conversation_id=ctx.conversation_id,
            timestamp=now_ms(),
            run_id=ctx.run_id,
            plan=items,
        ),
        user_id=ctx.user_id,
    )

    # Register plan-step mappings for tasks with planStepId
    from app.services.plan_dispatch_mapping import plan_dispatch_mapping
    from app.services.plan_registry import plan_registry

    for item in items:
        if item.plan_step_id is None:
            continue
        plan = plan_registry.find_plan_by_step(item.plan_step_id)
        if plan is not None:
            plan_dispatch_mapping.register(
                plan_id=plan.plan_id,
                step_id=item.plan_step_id,
                dispatch_task_id=item.id,
            )

    # Determine visibility: clone-self if all tasks use caller's agent_id
    all_clone = all(item.agent_id == ctx.agent_id for item in items)
    visibility = "hidden" if all_clone else "visible"

    dag_id = uuid.uuid4().hex
    all_task_ids = [item.id for item in items]

    dag_ctx = DagExecContext(
        conversation_id=ctx.conversation_id,
        trigger_message_id=trigger_message_id,
        parent_run_id=ctx.run_id,
        cancel_event=ctx.cancel_event,
        dispatch_depth=ctx.dispatch_depth + 1,
        dispatch_visibility=visibility,
        user_id=ctx.user_id,
        workspace_path=ctx.workspace_path,
        dag_id=dag_id,
        all_task_ids=all_task_ids,
        original_dag_id=original_dag_id if mode == "retry" else None,
        retry_mode=(mode == "retry"),
    )

    logger.info(
        "[dispatch_plan] run=%s executing DAG with %d tasks "
        "depth=%d visibility=%s",
        ctx.run_id,
        len(items),
        ctx.dispatch_depth,
        visibility,
    )

    results = await execute_dag(items, dag_ctx)

    logger.info(
        "[dispatch_plan] DAG execution complete run=%s results=%d",
        ctx.run_id,
        len(results),
    )

    # Drain mailbox: collect async messages left by sub-agents via ask_peer
    from app.services.agent_session_registry import agent_session_registry

    mailbox_msgs = agent_session_registry.drain_mailbox(ctx.run_id)

    result_data: dict[str, Any] = {
        "dagId": dag_id,
        "tasks": {
            nr.task_id: {
                "status": nr.status,
                "summary": nr.summary,
                "workspaceChanges": nr.workspace_changes,
                "artifactIds": nr.artifact_ids,
                "keyDecisions": nr.key_decisions,
                **(
                    {"errorDetail": nr.error_detail}
                    if nr.status in ("failed", "aborted") and nr.error_detail
                    else {}
                ),
            }
            for nr in results.values()
        },
    }
    if mailbox_msgs:
        result_data["mailbox"] = mailbox_msgs

    return ok(result_data)


dispatch_plan_tool = ToolDef(
    name=DISPATCH_PLAN_TOOL_NAME,
    description=(
        "Dispatch a structured DAG of tasks with dependency-aware parallel "
        "execution. Declare all tasks with their dependsOn relationships in "
        "one call; the system schedules them in topological waves — "
        "independent tasks run in parallel, dependent tasks wait for their "
        "upstream tasks. Use this for multi-task work with known dependencies "
        "(e.g. PRD → design → frontend+backend → integration test). For a "
        "single immediate dispatch, use task_dispatch instead.\n\n"
        "Parameters:\n"
        "- mode: 'full' (default, new DAG) or 'retry' (re-run failed nodes "
        "with reused history from the original DAG)\n"
        "- originalDagId: required when mode='retry'; the dagId from the "
        "previous dispatch_plan return value"
    ),
    parameters=_PARAMETERS,
    handler=_handler,
)
