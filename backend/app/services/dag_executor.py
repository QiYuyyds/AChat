"""DAG Executor — topological wave scheduling for the dispatch_plan tool.

Validates a task DAG (no cycles, no missing refs, no self-deps), groups tasks
into dependency waves, and executes each wave in parallel via
``spawn_subagent_loop``. Failed upstream tasks cause downstream tasks to be
marked ``skipped``.

This module is intentionally independent of the tool layer so it can be unit
tested with a mocked ``spawn_subagent_loop``.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Literal

from app.schemas.dispatch import DispatchPlanItem
from app.schemas.events import DispatchEndEvent, DispatchStartEvent
from app.services.event_bus import event_bus
from app.utils.clock import now_ms

logger = logging.getLogger(__name__)

NodeStatus = Literal["complete", "failed", "aborted", "skipped"]


@dataclass
class NodeResult:
    """Outcome of a single DAG node execution."""

    task_id: str
    status: NodeStatus
    summary: str
    child_run_id: str | None = None


@dataclass
class DagExecContext:
    """Context passed to ``execute_dag`` — mirrors what spawn_subagent_loop needs."""

    conversation_id: str
    trigger_message_id: str
    parent_run_id: str
    cancel_event: asyncio.Event
    dispatch_depth: int = 0
    dispatch_visibility: str = "visible"
    user_id: str | None = None


# ─── Validation ───────────────────────────────────────────────────────────────


def validate_dag(tasks: list[DispatchPlanItem]) -> list[str]:
    """Validate a DAG: duplicate ids, self-deps, missing refs, cycles.

    Returns a list of error strings (empty if the plan is valid).
    """
    errors: list[str] = []

    seen_ids: set[str] = set()
    for t in tasks:
        if t.id in seen_ids:
            errors.append(f"Duplicate task id: '{t.id}'")
        seen_ids.add(t.id)

    for t in tasks:
        deps = t.depends_on or []
        if t.id in deps:
            errors.append(f"Task '{t.id}' depends on itself")

    for t in tasks:
        deps = t.depends_on or []
        for dep in deps:
            if dep not in seen_ids:
                errors.append(
                    f"Task '{t.id}' depends on unknown task '{dep}'"
                )

    if not errors:
        try:
            topological_waves(tasks)
        except ValueError as exc:
            errors.append(str(exc))

    return errors


def topological_waves(
    tasks: list[DispatchPlanItem],
) -> list[list[DispatchPlanItem]]:
    """Group tasks into dependency waves.

    Each wave contains tasks whose all dependencies are satisfied by prior
    waves. Raises ``ValueError`` if a cycle is detected.
    """
    completed: set[str] = set()
    waves: list[list[DispatchPlanItem]] = []
    remaining = list(tasks)

    while remaining:
        ready = [
            t
            for t in remaining
            if all(d in completed for d in (t.depends_on or []))
        ]

        if not ready:
            remaining_ids = ", ".join(t.id for t in remaining)
            raise ValueError(
                f"Cycle detected in task dependencies (involves: {remaining_ids})"
            )

        for t in ready:
            completed.add(t.id)
            remaining.remove(t)

        waves.append(ready)

    return waves


# ─── Execution ────────────────────────────────────────────────────────────────


async def execute_dag(
    tasks: list[DispatchPlanItem],
    ctx: DagExecContext,
) -> dict[str, NodeResult]:
    """Execute a DAG of tasks via wave-based topological scheduling.

    Each wave runs independent tasks in parallel via ``spawn_subagent_loop``.
    Tasks whose any upstream dependency did not complete successfully are
    marked ``skipped`` and not executed.

    Returns a flat ``dict[task_id, NodeResult]``.
    """
    from app.services.agent_loop import spawn_subagent_loop

    waves = topological_waves(tasks)
    results: dict[str, NodeResult] = {}
    failed_ids: set[str] = set()

    for wave in waves:
        ready: list[DispatchPlanItem] = []
        skipped: list[DispatchPlanItem] = []

        for t in wave:
            deps = t.depends_on or []
            upstream_failed = [d for d in deps if d in failed_ids]
            if upstream_failed:
                skipped.append(t)
            else:
                ready.append(t)

        # Mark skipped tasks (no dispatch.start, only dispatch.end)
        for t in skipped:
            failed_upstream = [
                d for d in (t.depends_on or []) if d in failed_ids
            ]
            reason = (
                f"Upstream task(s) did not complete: {', '.join(failed_upstream)}"
            )
            results[t.id] = NodeResult(
                task_id=t.id,
                status="skipped",
                summary=reason,
                child_run_id=None,
            )
            failed_ids.add(t.id)
        event_bus.publish(
            DispatchEndEvent(
                conversation_id=ctx.conversation_id,
                timestamp=now_ms(),
                parent_run_id=ctx.parent_run_id,
                child_run_id=None,
                task_id=t.id,
                status="skipped",
                error=reason,
            ),
            user_id=ctx.user_id,
        )

        # Execute ready tasks in parallel
        if ready:
            coros = [
                _execute_node(t, ctx, spawn_subagent_loop) for t in ready
            ]
            node_results = await asyncio.gather(*coros)
            for t, nr in zip(ready, node_results, strict=True):
                results[t.id] = nr
                if nr.status in ("failed", "aborted", "skipped"):
                    failed_ids.add(t.id)

    return results


async def _execute_node(
    task: DispatchPlanItem,
    ctx: DagExecContext,
    spawn_fn,  # callable matching spawn_subagent_loop signature
) -> NodeResult:
    """Execute a single DAG node via the provided spawn function.

    Emits ``dispatch.start`` when the child run begins and ``dispatch.end``
    when it finishes.
    """
    child_run_id_holder: list[str] = []

    def on_start(run_id: str) -> None:
        child_run_id_holder.append(run_id)
    event_bus.publish(
        DispatchStartEvent(
            conversation_id=ctx.conversation_id,
            timestamp=now_ms(),
            parent_run_id=ctx.parent_run_id,
            child_run_id=run_id,
            task_id=task.id,
            agent_id=task.agent_id,
        ),
        user_id=ctx.user_id,
    )

    logger.info(
        "[dag_executor] executing node=%s agent=%s parent_run=%s",
        task.id,
        task.agent_id,
        ctx.parent_run_id,
    )

    result = await spawn_fn(
        agent_id=task.agent_id,
        task_description=task.task,
        conversation_id=ctx.conversation_id,
        trigger_message_id=ctx.trigger_message_id,
        parent_run_id=ctx.parent_run_id,
        parent_cancel_event=ctx.cancel_event,
        on_start=on_start,
        dispatch_depth=ctx.dispatch_depth,
        dispatch_visibility=ctx.dispatch_visibility,
        user_id=ctx.user_id,
    )

    child_run_id = child_run_id_holder[0] if child_run_id_holder else None

    event_bus.publish(
        DispatchEndEvent(
            conversation_id=ctx.conversation_id,
            timestamp=now_ms(),
            parent_run_id=ctx.parent_run_id,
            child_run_id=child_run_id,
            task_id=task.id,
            status=result.status,
        ),
        user_id=ctx.user_id,
    )

    return NodeResult(
        task_id=task.id,
        status=result.status,
        summary=result.text,
        child_run_id=child_run_id,
    )
