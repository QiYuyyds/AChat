"""Port of src/server/agent-runner.ts (orchestrator path).

The isOrchestrator branch: three stages, with dynamic re-planning.
  Stage 1 PLAN      — LLM emits plan_tasks; the plan is parked for user review.
  Stage 2 EXECUTE   — approved plan runs as a DAG; child runs are nested runs.
  Stage 3 AGGREGATE — a final consolidation pass over all child results.

Failed / conflicting rounds feed a replan context back into the planner, up to
MAX_DISPATCH_ROUNDS. See specs/06-orchestrator-flow.md.

Port mappings: TS AbortSignal -> the per-run asyncio.Event carried by the
runner; Promise.all over a wave -> asyncio.gather; AgentRunner.run(child) ->
agent_runner.run_with_args (returns the child task to await); the TS Semaphore ->
the shared agent_runner.sub_agent_run_semaphore. ``execute_orchestrator_run`` is
imported lazily by agent_runner.execute_run, so importing this module pulls in
agent_runner without a cycle at load time.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field

from sqlalchemy import select

from app.adapters.base import AdapterAttachment
from app.adapters.registry import agent_registry
from app.db.engine import get_db
from app.db.models import Agent, Artifact, Conversation, Workspace
from app.schemas.artifacts import ArtifactRecord
from app.schemas.dispatch import DispatchExpectedOutput, DispatchPlanItem
from app.schemas.events import (
    ArtifactCreateEvent,
    DispatchEndEvent,
    DispatchPlanEvent,
    DispatchStartEvent,
)

# the seam from Core-A — import the runner machinery used to spawn nested runs.
from app.services.agent_runner import (  # noqa: E402 - intentional: keep below local imports
    ASK_USER_TOOL_NAME,
    DEFAULT_PREPARE_TIMEOUT_MS,
    DEFAULT_VERIFICATION_TIMEOUT_MS,
    MAX_CHILD_TASK_ATTEMPTS,
    MAX_DISPATCH_ROUNDS,
    ORCHESTRATOR_PLAN_ALLOWED_TOOLS,
    RunArgs,
    RunExecutionResult,
    _empty_run_execution_result,
    build_adapter_input,
    consume_stream,
    publish,
    run_with_args,
    sub_agent_run_semaphore,
)
from app.services.dispatch_plan import (
    ReplanConflictView,
    ReplanTaskView,
    build_replan_context,
    build_revise_context,
    compile_and_validate_dispatch_plan,
    extract_plan_tasks_tool_args,
    get_required_expected_outputs,
    parse_dispatch_plan_tool_args,
    should_replan,
)
from app.services.orchestrator_prompts import (
    ResolvedTaskInput,
    build_aggregate_prompt,
    build_orchestrator_aggregate_prompt,
    build_orchestrator_plan_prompt,
    build_sub_agent_prompt,
    ensure_includes,
    escape_xml,
)
from app.services.pending_dispatch_plans import PlanReviewOutcome, pending_dispatch_plans
from app.services.project_artifact import build_project_files  # noqa: E402
from app.services.task_result_report import evaluate_task_result_report
from app.tools.base import ToolContext
from app.tools.bash import BashExecutionArgs, execute_bash_command
from app.utils.clock import now_ms
from app.utils.dispatch_file_writes import (
    FileWriteConflict,
    RunFileWrites,
    clear_file_writes,
    detect_wave_conflicts,
    get_file_writes,
)
from app.utils.dispatch_run_evidence import (
    RunCommandEvidence,
    RunToolEvidence,
    clear_run_tool_evidence,
    get_run_tool_evidence,
    record_run_command,
)
from app.utils.ids import new_artifact_id  # noqa: E402
from app.utils.workspace_utils import assert_path_within_workspace, get_effective_cwd


# ─── dispatch result + evaluation views (mirror the TS interfaces) ────────────
@dataclass
class DispatchTaskResult:
    run_id: str | None
    status: str  # DispatchTaskEndStatus: complete | failed | aborted | skipped
    artifact_ids: list[str] = field(default_factory=list)
    output_message_ids: list[str] = field(default_factory=list)
    output_artifacts: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    run_ids: list[str] | None = None
    task_report: dict | None = None


@dataclass
class _VerificationCommandResult:
    command: str
    exit_code: int | None
    timed_out: bool
    ok: bool
    cwd: str | None = None
    output: str | None = None
    error: str | None = None
    prepare: bool = False


@dataclass
class _ChildAttemptEvaluation:
    raw_result: DispatchTaskResult
    result: DispatchTaskResult
    evidence: RunToolEvidence
    verification_results: list[_VerificationCommandResult]


@dataclass
class DagContext:
    parent_run_id: str
    conversation_id: str
    trigger_message_id: str
    workspace: Workspace
    cancel_event: asyncio.Event
    seed_results: dict[str, DispatchTaskResult] | None = None
    external_plan_items: list[DispatchPlanItem] | None = None


# ─── Stage entry: PLAN → EXECUTE (with replan) → AGGREGATE ────────────────────
async def execute_orchestrator_run(
    run_id: str,
    cancel_event: asyncio.Event,
    args: RunArgs,
    user_prompt: str,
    attachments: list[AdapterAttachment],
) -> RunExecutionResult:
    async with get_db() as db:
        agent = (
            await db.execute(select(Agent).where(Agent.id == args.agent_id))
        ).scalar_one_or_none()
        if agent is None:
            raise RuntimeError(f"Agent not found: {args.agent_id}")
        conv = (
            await db.execute(select(Conversation).where(Conversation.id == args.conversation_id))
        ).scalar_one_or_none()
        if conv is None:
            raise RuntimeError(f"Conversation not found: {args.conversation_id}")
        workspace = (
            await db.execute(
                select(Workspace).where(Workspace.conversation_id == args.conversation_id)
            )
        ).scalar_one_or_none()
        if workspace is None:
            raise RuntimeError(f"Workspace not found for conversation: {args.conversation_id}")

        other_agent_ids = [aid for aid in conv.agent_ids_list if aid != agent.id]
        other_agents = (
            list(
                (
                    await db.execute(select(Agent).where(Agent.id.in_(other_agent_ids)))
                ).scalars().all()
            )
            if other_agent_ids
            else []
        )
        db.expunge(agent)
        db.expunge(workspace)
        for a in other_agents:
            db.expunge(a)

    all_artifact_ids: list[str] = []
    all_output_message_ids: list[str] = []
    all_output_artifacts: dict[str, str] = {}

    merged_results: dict[str, DispatchTaskResult] = {}
    plan_items_by_id: dict[str, DispatchPlanItem] = {}
    last_conflicts: list[FileWriteConflict] = []

    # ─── Stage 1+2: PLAN → EXECUTE, replanning on failure/conflict ────────────
    for round_no in range(1, MAX_DISPATCH_ROUNDS + 1):
        if cancel_event.is_set():
            raise RuntimeError("Orchestrator run aborted")

        replan_context = (
            None
            if round_no == 1
            else build_replan_context(
                _to_replan_views(plan_items_by_id, merged_results),
                _to_replan_conflicts(last_conflicts),
            )
        )

        initial_plan, plan_run = await _run_plan_stage(
            args,
            agent,
            run_id,
            workspace,
            user_prompt,
            other_agents,
            attachments if round_no == 1 else [],
            cancel_event,
            replan_context,
            list(plan_items_by_id.values()),
        )
        all_artifact_ids.extend(plan_run.artifact_ids)
        all_output_message_ids.extend(plan_run.output_message_ids)
        all_output_artifacts.update(plan_run.output_artifacts)

        if not initial_plan:
            # round 1 with no plan: the orchestrator just answered the user directly.
            if round_no == 1:
                return RunExecutionResult(
                    artifact_ids=all_artifact_ids,
                    output_message_ids=all_output_message_ids,
                    output_artifacts=all_output_artifacts,
                )
            break

        # ─── REVIEW (gate): approve / reject / revise ───
        plan = initial_plan
        approved_plan: list[DispatchPlanItem] | None = None
        reviewing = True
        while reviewing:
            outcome = await _wait_for_dispatch_plan_review(
                conversation_id=args.conversation_id,
                agent_id=agent.id,
                run_id=run_id,
                plan=plan,
                available_agents=other_agents,
                orchestrator_agent_id=agent.id,
                resolved_external_tasks=list(plan_items_by_id.values()),
                cancel_event=cancel_event,
            )
            if outcome.kind == "approve":
                approved_plan = outcome.plan
                reviewing = False
            elif outcome.kind == "reject":
                reviewing = False
            else:
                # revise: feed the user's feedback into a re-plan; review the result again.
                revised_plan, revised_run = await _run_plan_stage(
                    args,
                    agent,
                    run_id,
                    workspace,
                    user_prompt,
                    other_agents,
                    [],
                    cancel_event,
                    build_revise_context(plan, outcome.feedback or ""),
                    list(plan_items_by_id.values()),
                )
                all_artifact_ids.extend(revised_run.artifact_ids)
                all_output_message_ids.extend(revised_run.output_message_ids)
                all_output_artifacts.update(revised_run.output_artifacts)
                if revised_plan:
                    plan = revised_plan

        if not approved_plan:
            if cancel_event.is_set():
                raise RuntimeError("Orchestrator run aborted")
            if round_no == 1:
                return RunExecutionResult(
                    artifact_ids=all_artifact_ids,
                    output_message_ids=all_output_message_ids,
                    output_artifacts=all_output_artifacts,
                )
            break

        publish(
            DispatchPlanEvent(
                conversation_id=args.conversation_id,
                timestamp=now_ms(),
                run_id=run_id,
                plan=approved_plan,
            )
        )
        for item in approved_plan:
            plan_items_by_id[item.id] = item

        # ─── EXECUTE (DAG) ───
        results, conflicts = await _execute_dag(
            approved_plan,
            DagContext(
                parent_run_id=run_id,
                conversation_id=args.conversation_id,
                trigger_message_id=args.trigger_message_id,
                workspace=workspace,
                cancel_event=cancel_event,
                seed_results=merged_results,
                external_plan_items=list(plan_items_by_id.values()),
            ),
        )
        for task_id, r in results.items():
            merged_results[task_id] = r
        last_conflicts = conflicts

        round_views = [
            ReplanTaskView(
                task_id=t.id,
                agent_id=t.agent_id,
                status=(results.get(t.id).status if results.get(t.id) else "skipped"),
                error=(results.get(t.id).error if results.get(t.id) else None),
            )
            for t in approved_plan
        ]
        if not should_replan(round_views, _to_replan_conflicts(conflicts)):
            break

    # collect artifacts/messages from the merged final results (deduped by task)
    for r in merged_results.values():
        all_artifact_ids.extend(r.artifact_ids)
        all_output_message_ids.extend(r.output_message_ids)
        all_output_artifacts.update(r.output_artifacts)

    # ─── Stage 3: AGGREGATE ───
    aggregate_system_prompt = build_orchestrator_aggregate_prompt(agent.system_prompt)
    aggregate_user_prompt = await build_aggregate_prompt(
        user_prompt,
        list(plan_items_by_id.values()),
        merged_results,
        last_conflicts,
        workspace,
    )
    # aggregate stage drops plan_tasks / ask_user (no re-dispatch / interruption)
    aggregate_tool_names = [
        n for n in agent.tool_names_list if n != "plan_tasks" and n != ASK_USER_TOOL_NAME
    ]

    adapter = agent_registry.get_adapter(agent)
    agg_stream = adapter.stream(
        await build_adapter_input(
            args,
            agent,
            run_id,
            aggregate_user_prompt,
            workspace,
            aggregate_tool_names,
            aggregate_system_prompt,
            [],
        ),
        cancel_event,
    )
    agg_run = await consume_stream(agg_stream, agent.id, run_id)
    all_artifact_ids.extend(agg_run.artifact_ids)
    all_output_message_ids.extend(agg_run.output_message_ids)
    all_output_artifacts.update(agg_run.output_artifacts)

    return RunExecutionResult(
        artifact_ids=all_artifact_ids,
        output_message_ids=all_output_message_ids,
        output_artifacts=all_output_artifacts,
    )


# ─── PLAN stage (shared by first + remediation rounds) ────────────────────────
async def _run_plan_stage(
    args: RunArgs,
    agent: Agent,
    run_id: str,
    workspace: Workspace,
    user_prompt: str,
    other_agents: list[Agent],
    attachments: list[AdapterAttachment],
    cancel_event: asyncio.Event,
    replan_context: str | None,
    resolved_external_tasks: list[DispatchPlanItem],
) -> tuple[list[DispatchPlanItem] | None, RunExecutionResult]:
    plan_system_prompt = build_orchestrator_plan_prompt(
        agent.system_prompt, other_agents, workspace
    )
    plan_tool_names = ensure_includes(
        ensure_includes(
            [name for name in agent.tool_names_list if name in ORCHESTRATOR_PLAN_ALLOWED_TOOLS],
            "plan_tasks",
        ),
        ASK_USER_TOOL_NAME,
    )
    effective_prompt = (
        f"{replan_context}\n\n<original_request>\n{user_prompt}\n</original_request>"
        if replan_context
        else user_prompt
    )

    plan_ref: dict[str, list[DispatchPlanItem] | None] = {"value": None}

    def on_tool_call(event) -> dict | None:
        if event.type != "tool.call":
            return None
        plan_args = extract_plan_tasks_tool_args(event.tool_name, event.args)
        if plan_args is None:
            return None
        plan = parse_dispatch_plan_tool_args(plan_args)
        plan_ref["value"] = plan
        return {
            "stop": True,
            "result": {"acknowledged": True, "taskCount": len(plan)},
        }

    adapter = agent_registry.get_adapter(agent)
    plan_stream = adapter.stream(
        await build_adapter_input(
            args,
            agent,
            run_id,
            effective_prompt,
            workspace,
            plan_tool_names,
            plan_system_prompt,
            attachments,
        ),
        cancel_event,
    )
    plan_run = await consume_stream(plan_stream, agent.id, run_id, on_tool_call)

    raw = plan_ref["value"]
    plan = (
        compile_and_validate_dispatch_plan(
            raw, other_agents, agent.id, resolved_external_tasks
        ).plan
        if raw
        else None
    )
    return plan, plan_run


def _to_replan_views(
    plan_by_id: dict[str, DispatchPlanItem],
    results: dict[str, DispatchTaskResult],
) -> list[ReplanTaskView]:
    views: list[ReplanTaskView] = []
    for task_id, item in plan_by_id.items():
        r = results.get(task_id)
        views.append(
            ReplanTaskView(
                task_id=task_id,
                agent_id=item.agent_id,
                status=r.status if r else "skipped",
                error=r.error if r else None,
            )
        )
    return views


def _to_replan_conflicts(conflicts: list[FileWriteConflict]) -> list[ReplanConflictView]:
    return [
        ReplanConflictView(path=c.path, task_ids=[w["taskId"] for w in c.contributors])
        for c in conflicts
    ]


def _merge_external_plan_items(
    external_items: list[DispatchPlanItem],
    current_plan: list[DispatchPlanItem],
) -> list[DispatchPlanItem]:
    by_id: dict[str, DispatchPlanItem] = {}
    for item in external_items:
        by_id[item.id] = item
    for item in current_plan:
        by_id[item.id] = item
    return list(by_id.values())


# ─── plan review gate (parks the plan; awaits the user's decision) ────────────
async def _wait_for_dispatch_plan_review(
    *,
    conversation_id: str,
    agent_id: str,
    run_id: str,
    plan: list[DispatchPlanItem],
    available_agents: list[Agent],
    orchestrator_agent_id: str,
    resolved_external_tasks: list[DispatchPlanItem],
    cancel_event: asyncio.Event,
) -> PlanReviewOutcome:
    def validator(p: list[DispatchPlanItem]) -> list[DispatchPlanItem]:
        return compile_and_validate_dispatch_plan(
            p, available_agents, orchestrator_agent_id, resolved_external_tasks
        ).plan

    pending = pending_dispatch_plans.register(
        conversation_id=conversation_id,
        agent_id=agent_id,
        run_id=run_id,
        plan=plan,
        validator=validator,
    )

    loop = asyncio.get_running_loop()
    future: asyncio.Future[PlanReviewOutcome] = loop.create_future()

    def finish(outcome: PlanReviewOutcome) -> None:
        if not future.done():
            future.set_result(outcome)

    pending_dispatch_plans.attach_resolver(pending.id, finish)
    if cancel_event.is_set():
        pending_dispatch_plans.cancel(pending.id)
        return await future

    # cascade an abort during the wait into a cancel of the parked plan
    abort_watcher = asyncio.ensure_future(cancel_event.wait())

    def _on_abort(_t: asyncio.Future) -> None:
        if not future.done():
            pending_dispatch_plans.cancel(pending.id)

    abort_watcher.add_done_callback(_on_abort)
    try:
        return await future
    finally:
        abort_watcher.cancel()


# ─── DAG execution (topological waves; per-wave conflict detection) ───────────
async def _execute_dag(
    plan: list[DispatchPlanItem],
    ctx: DagContext,
) -> tuple[dict[str, DispatchTaskResult], list[FileWriteConflict]]:
    current_task_ids = {t.id for t in plan}
    results: dict[str, DispatchTaskResult] = {
        task_id: r
        for task_id, r in (ctx.seed_results or {}).items()
        if task_id not in current_task_ids
    }
    remaining = {t.id for t in plan}
    conflicts: list[FileWriteConflict] = []
    plan_context = _merge_external_plan_items(ctx.external_plan_items or [], plan)

    while remaining:
        if ctx.cancel_event.is_set():
            _mark_remaining_tasks_aborted(plan, remaining, results, ctx)
            raise RuntimeError("Orchestrator run aborted")

        for task in plan:
            if task.id not in remaining:
                continue
            blockers = [
                {"taskId": dep, "result": results[dep]}
                for dep in (task.depends_on or [])
                if dep in results and results[dep].status != "complete"
            ]
            if not blockers:
                continue
            result = _skipped_task_result(task, blockers)
            results[task.id] = result
            remaining.discard(task.id)
            _publish_dispatch_end(ctx, task.id, result)

        if not remaining:
            break

        ready = [
            t
            for t in plan
            if t.id in remaining
            and all(
                results.get(d) is not None and results[d].status == "complete"
                for d in (t.depends_on or [])
            )
        ]
        if not ready:
            raise RuntimeError("Circular dependency or unresolved task in plan")

        wave = await asyncio.gather(
            *(_run_child_task(t, results, plan_context, ctx) for t in ready)
        )
        for i, t in enumerate(ready):
            results[t.id] = wave[i]
            remaining.discard(t.id)

        # same-wave code conflict: ≥2 child runs wrote the same file differently.
        if len(ready) > 1:
            run_writes: list[RunFileWrites] = []
            for i, t in enumerate(ready):
                child_run_ids = _get_dispatch_result_run_ids(wave[i])
                if not child_run_ids:
                    continue
                run_writes.append(
                    RunFileWrites(
                        task_id=t.id,
                        agent_id=t.agent_id,
                        run_id=child_run_ids[-1],
                        writes=_merge_file_writes(child_run_ids),
                    )
                )
            conflicts.extend(detect_wave_conflicts(run_writes))

    # release this dispatch's child-run write/evidence records (in-memory)
    for task_id, r in results.items():
        if task_id not in current_task_ids:
            continue
        for child_run_id in _get_dispatch_result_run_ids(r):
            clear_file_writes(child_run_id)
            clear_run_tool_evidence(child_run_id)

    return (
        {task_id: r for task_id, r in results.items() if task_id in current_task_ids},
        conflicts,
    )


def _get_dispatch_result_run_ids(result: DispatchTaskResult) -> list[str]:
    if result.run_ids:
        return result.run_ids
    return [result.run_id] if result.run_id else []


def _merge_file_writes(run_ids: list[str]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for run_id in run_ids:
        for path, hash_ in get_file_writes(run_id).items():
            merged[path] = hash_
    return merged


# ─── one child task: attempts loop, evaluation, project artifact binding ──────
async def _run_child_task(
    task: DispatchPlanItem,
    upstream: dict[str, DispatchTaskResult],
    plan: list[DispatchPlanItem],
    ctx: DagContext,
) -> DispatchTaskResult:
    resolved_inputs = _resolve_task_inputs(task, upstream, plan)
    missing_required_inputs = [
        e for e in resolved_inputs if e.missing and e.input.required is not False
    ]
    if missing_required_inputs:
        result = _skipped_missing_inputs_task_result(task, missing_required_inputs)
        _publish_dispatch_end(ctx, task.id, result)
        return result

    try:
        release = await sub_agent_run_semaphore.acquire(ctx.cancel_event)
    except Exception:  # noqa: BLE001 - faithful: aborted while waiting for a slot
        result = _aborted_before_start_task_result(
            task, "Aborted while waiting for sub-agent concurrency slot"
        )
        _publish_dispatch_end(ctx, task.id, result)
        return result

    try:
        base_prompt = await build_sub_agent_prompt(
            task, upstream, ctx.conversation_id, plan, resolved_inputs, ctx.workspace
        )

        continuation_context: str | None = None
        last_evaluation: _ChildAttemptEvaluation | None = None
        aggregate = _empty_run_execution_result()
        aggregate_evidence = RunToolEvidence()
        attempt_run_ids: list[str] = []

        for attempt in range(1, MAX_CHILD_TASK_ATTEMPTS + 1):
            if ctx.cancel_event.is_set():
                result = _aborted_before_start_task_result(
                    task, "Aborted before sub-agent run started"
                )
                _publish_dispatch_end(ctx, task.id, result)
                return _merge_attempt_aggregate(result, aggregate)

            prompt = (
                _build_continuation_prompt(base_prompt, task, attempt, continuation_context)
                if continuation_context
                else base_prompt
            )
            attempt_evaluation = await _run_child_task_attempt(task, prompt, ctx)
            if attempt_evaluation.raw_result.run_id:
                attempt_run_ids.append(attempt_evaluation.raw_result.run_id)
            _merge_run_execution_result(aggregate, attempt_evaluation.raw_result)
            _merge_run_tool_evidence(aggregate_evidence, attempt_evaluation.evidence)
            evaluated_result = _evaluate_child_task_result(
                task, attempt_evaluation.raw_result, aggregate_evidence
            )
            evaluated_result.run_ids = list(attempt_run_ids)
            current_evaluation = _ChildAttemptEvaluation(
                raw_result=attempt_evaluation.raw_result,
                result=evaluated_result,
                evidence=_clone_run_tool_evidence(aggregate_evidence),
                verification_results=attempt_evaluation.verification_results,
            )

            if current_evaluation.result.status == "complete":
                project_artifact_id = await _maybe_create_project_artifact(
                    evidence=aggregate_evidence,
                    conversation_id=ctx.conversation_id,
                    agent_id=task.agent_id,
                    task_id=task.id,
                    result=current_evaluation.result,
                )
                result_with_project = _bind_project_expected_output(
                    task, current_evaluation.result, project_artifact_id
                )
                output_evaluation = _evaluate_required_project_outputs(task, result_with_project)
                if output_evaluation[0]:
                    current_evaluation.result = result_with_project
                else:
                    result_with_project.status = "failed"
                    result_with_project.error = output_evaluation[1]
                    current_evaluation.result = result_with_project

            last_evaluation = current_evaluation

            if current_evaluation.result.status == "complete":
                _publish_dispatch_end(ctx, task.id, current_evaluation.result)
                return _merge_attempt_aggregate(current_evaluation.result, aggregate)

            report = current_evaluation.result.task_report
            if current_evaluation.result.status == "aborted" or (
                report is not None and report.get("status") == "blocked"
            ):
                _publish_dispatch_end(ctx, task.id, current_evaluation.result)
                return _merge_attempt_aggregate(current_evaluation.result, aggregate)

            continuation_context = _build_task_continuation_context(
                task, current_evaluation, attempt, MAX_CHILD_TASK_ATTEMPTS
            )

        result = (
            last_evaluation.result
            if last_evaluation
            else _aborted_before_start_task_result(task, "No child task attempt was executed")
        )
        exhausted = DispatchTaskResult(
            run_id=result.run_id,
            status="complete" if result.status == "complete" else "failed",
            artifact_ids=result.artifact_ids,
            output_message_ids=result.output_message_ids,
            output_artifacts=result.output_artifacts,
            error=(
                result.error
                if result.status == "complete"
                else (
                    f'Task "{task.id}" did not satisfy completion gates after '
                    f"{MAX_CHILD_TASK_ATTEMPTS} attempt(s). Last error: "
                    f"{result.error or 'unknown error'}"
                )
            ),
            run_ids=list(attempt_run_ids),
            task_report=result.task_report,
        )
        _publish_dispatch_end(ctx, task.id, exhausted)
        return _merge_attempt_aggregate(exhausted, aggregate)
    finally:
        release()


# port of maybeCreateProjectArtifact — takes the aggregated evidence object
# directly (child attempts accumulate writes across retries, so a single run-id
# lookup would miss earlier attempts' files).
async def _maybe_create_project_artifact(
    *,
    evidence: RunToolEvidence,
    conversation_id: str,
    agent_id: str,
    task_id: str | None,
    result: DispatchTaskResult,
) -> str | None:
    if len(evidence.file_writes) == 0:
        return None

    async with get_db() as db:
        workspace = (
            await db.execute(
                select(Workspace).where(Workspace.conversation_id == conversation_id)
            )
        ).scalar_one_or_none()
        if workspace is None:
            return None
        effective_cwd = get_effective_cwd(workspace)

    files = build_project_files(evidence.file_writes, effective_cwd)
    if len(files) == 0:
        return None

    async with get_db() as db:
        agent = (
            await db.execute(select(Agent).where(Agent.id == agent_id))
        ).scalar_one_or_none()
        agent_name = agent.name if agent else agent_id
    title = f"{agent_name} · 项目产物"

    content: dict = {
        "type": "project",
        "files": [f.model_dump(by_alias=True) for f in files],
        "agentId": agent_id,
    }
    if task_id:
        content["taskId"] = task_id

    artifact_id = new_artifact_id()
    created_at = now_ms()
    async with get_db() as db:
        artifact = Artifact(
            id=artifact_id,
            conversation_id=conversation_id,
            type="project",
            title=title,
            version=1,
            parent_artifact_id=None,
            created_by_agent_id=agent_id,
            created_at=created_at,
        )
        artifact.content_dict = content
        db.add(artifact)

    result.artifact_ids.append(artifact_id)
    publish(
        ArtifactCreateEvent(
            conversation_id=conversation_id,
            timestamp=now_ms(),
            artifact=ArtifactRecord(
                id=artifact_id,
                conversation_id=conversation_id,
                type="project",
                title=title,
                content=content,
                version=1,
                parent_artifact_id=None,
                created_by_agent_id=agent_id,
                created_at=created_at,
            ),
        )
    )
    return artifact_id


async def _run_child_task_attempt(
    task: DispatchPlanItem,
    prompt: str,
    ctx: DagContext,
) -> _ChildAttemptEvaluation:
    child_run_id, child_task, _child_cancel = run_with_args(
        RunArgs(
            agent_id=task.agent_id,
            conversation_id=ctx.conversation_id,
            trigger_message_id=ctx.trigger_message_id,
            parent_run_id=ctx.parent_run_id,
            override_prompt=prompt,
            require_task_report=True,
            parent_cancel_event=ctx.cancel_event,
        )
    )

    publish(
        DispatchStartEvent(
            conversation_id=ctx.conversation_id,
            timestamp=now_ms(),
            parent_run_id=ctx.parent_run_id,
            child_run_id=child_run_id,
            task_id=task.id,
            agent_id=task.agent_id,
        )
    )

    run_result = await child_task
    raw = DispatchTaskResult(
        run_id=child_run_id,
        status=run_result.status,
        artifact_ids=run_result.artifact_ids,
        output_message_ids=run_result.output_message_ids,
        output_artifacts=run_result.output_artifacts,
        error=run_result.error,
        task_report=run_result.task_report,
    )
    verification_results = (
        []
        if run_result.status == "aborted"
        else await _run_required_commands(task, child_run_id, ctx)
    )
    evidence = get_run_tool_evidence(child_run_id)
    result = _evaluate_child_task_result(task, raw, evidence)
    return _ChildAttemptEvaluation(
        raw_result=raw,
        result=result,
        evidence=evidence,
        verification_results=verification_results,
    )


def _evaluate_child_task_result(
    task: DispatchPlanItem,
    result: DispatchTaskResult,
    evidence: RunToolEvidence | None = None,
) -> DispatchTaskResult:
    if evidence is None:
        evidence = RunToolEvidence()
    if result.status != "complete":
        return result

    output_artifacts = _bind_implicit_single_output(task, result)
    report_evaluation = evaluate_task_result_report(task, result.task_report, evidence)
    if not report_evaluation.ok:
        return DispatchTaskResult(
            run_id=result.run_id,
            status="failed",
            artifact_ids=result.artifact_ids,
            output_message_ids=result.output_message_ids,
            output_artifacts=output_artifacts,
            error=report_evaluation.error,
            run_ids=result.run_ids,
            task_report=result.task_report,
        )

    return DispatchTaskResult(
        run_id=result.run_id,
        status=result.status,
        artifact_ids=result.artifact_ids,
        output_message_ids=result.output_message_ids,
        output_artifacts=output_artifacts,
        error=result.error,
        run_ids=result.run_ids,
        task_report=result.task_report,
    )


# ─── merge helpers ────────────────────────────────────────────────────────────
def _merge_run_execution_result(target: RunExecutionResult, source: DispatchTaskResult) -> None:
    target.artifact_ids.extend(source.artifact_ids)
    target.output_message_ids.extend(source.output_message_ids)
    target.output_artifacts.update(source.output_artifacts)
    if source.task_report:
        target.task_report = source.task_report


def _merge_run_tool_evidence(target: RunToolEvidence, source: RunToolEvidence) -> None:
    target.file_writes.extend(source.file_writes)
    target.commands.extend(source.commands)


def _clone_run_tool_evidence(source: RunToolEvidence) -> RunToolEvidence:
    return RunToolEvidence(
        file_writes=list(source.file_writes), commands=list(source.commands)
    )


def _merge_attempt_aggregate(
    result: DispatchTaskResult, aggregate: RunExecutionResult
) -> DispatchTaskResult:
    artifact_ids = list(dict.fromkeys([*aggregate.artifact_ids, *result.artifact_ids]))
    return DispatchTaskResult(
        run_id=result.run_id,
        status=result.status,
        artifact_ids=artifact_ids,
        output_message_ids=list(aggregate.output_message_ids),
        output_artifacts={**aggregate.output_artifacts, **result.output_artifacts},
        error=result.error,
        run_ids=result.run_ids,
        task_report=result.task_report,
    )


# ─── required-command verification (completion gate) ──────────────────────────
async def _run_required_commands(
    task: DispatchPlanItem,
    run_id: str,
    ctx: DagContext,
) -> list[_VerificationCommandResult]:
    if not task.required_commands:
        return []

    async with get_db() as db:
        workspace = (
            await db.execute(
                select(Workspace).where(Workspace.conversation_id == ctx.conversation_id)
            )
        ).scalar_one_or_none()
        if workspace is not None:
            db.expunge(workspace)
    if workspace is None:
        return [
            _VerificationCommandResult(
                command="(required commands)",
                exit_code=None,
                timed_out=False,
                ok=False,
                error="Workspace not found",
            )
        ]

    results: list[_VerificationCommandResult] = []
    for required in task.required_commands:
        expanded = _expand_required_command(required)
        command_results: list[_VerificationCommandResult] = []

        try:
            import re

            prepare = (
                None
                if any(
                    re.search(r"\b(?:pnpm|npm|yarn)\s+install\b", command, re.IGNORECASE)
                    for command in expanded["commands"]
                )
                else _build_prepare_command(workspace, expanded["cwd"])
            )
        except Exception as err:  # noqa: BLE001 - faithful: surface prepare-build failure
            results.append(
                _VerificationCommandResult(
                    command="prepare workspace",
                    cwd=expanded["cwd"],
                    exit_code=None,
                    timed_out=False,
                    ok=False,
                    error=str(err),
                    prepare=True,
                )
            )
            continue

        if prepare:
            prepare_result = await _run_supervisor_command(
                {
                    "command": prepare["command"],
                    "cwd": prepare.get("cwd"),
                    "timeoutMs": DEFAULT_PREPARE_TIMEOUT_MS,
                },
                task,
                run_id,
                ctx,
                True,
            )
            results.append(prepare_result)
            command_results.append(prepare_result)
            if not prepare_result.ok:
                continue

        for command in expanded["commands"]:
            command_result = await _run_supervisor_command(
                {
                    "command": command,
                    "cwd": expanded["cwd"],
                    "timeoutMs": required.timeout_ms or DEFAULT_VERIFICATION_TIMEOUT_MS,
                },
                task,
                run_id,
                ctx,
                False,
            )
            results.append(command_result)
            command_results.append(command_result)
            if not command_result.ok:
                break

        failed = next((r for r in command_results if not r.ok), None)
        cwd = (command_results[0].cwd if command_results else None) or required.cwd
        record_run_command(
            run_id,
            RunCommandEvidence(
                command=required.command,
                cwd=cwd or get_effective_cwd(workspace),
                exit_code=(failed.exit_code if failed else 0),
                timed_out=failed.timed_out if failed else False,
                is_error=bool(failed),
                error=failed.error if failed and failed.error else None,
            ),
        )

    return results


async def _run_supervisor_command(
    command: dict,
    task: DispatchPlanItem,
    run_id: str,
    ctx: DagContext,
    prepare: bool,
) -> _VerificationCommandResult:
    result = await execute_bash_command(
        BashExecutionArgs(
            command=command["command"],
            cwd=command.get("cwd"),
            timeout_ms=command.get("timeoutMs"),
            evidence_kind="prepare" if prepare else "verification",
        ),
        ToolContext(
            conversation_id=ctx.conversation_id,
            workspace_path="",
            agent_id=task.agent_id,
            run_id=run_id,
            cancel_event=ctx.cancel_event,
        ),
    )

    if not result.ok:
        return _VerificationCommandResult(
            command=command["command"],
            cwd=command.get("cwd"),
            exit_code=None,
            timed_out=False,
            ok=False,
            error=result.error,
            prepare=prepare,
        )

    value = result.value if isinstance(result.value, dict) else {}
    exit_code = value.get("exitCode") if isinstance(value.get("exitCode"), int) else None
    timed_out = value.get("timedOut") is True
    return _VerificationCommandResult(
        command=value["command"] if isinstance(value.get("command"), str) else command["command"],
        cwd=value["cwd"] if isinstance(value.get("cwd"), str) else command.get("cwd"),
        exit_code=exit_code,
        timed_out=timed_out,
        ok=exit_code == 0 and not timed_out,
        output=value["output"] if isinstance(value.get("output"), str) else None,
        prepare=prepare,
    )


def _expand_required_command(required) -> dict:
    import re

    cwd = required.cwd
    command = required.command.strip()
    cd_match = re.match(r'^cd\s+("?[^"&;]+"?)\s*&&\s*(.+)$', command, re.IGNORECASE)
    if cd_match and not cwd:
        cwd = cd_match.group(1).strip('"')
        command = cd_match.group(2).strip()
    return {
        "cwd": cwd,
        "commands": [part.strip() for part in re.split(r"\s+&&\s+", command) if part.strip()],
    }


def _build_prepare_command(workspace: Workspace, cwd: str | None) -> dict | None:
    cwd_abs = (
        assert_path_within_workspace(workspace, cwd) if cwd else get_effective_cwd(workspace)
    )
    if not os.path.exists(os.path.join(cwd_abs, "package.json")):
        return None
    if os.path.exists(os.path.join(cwd_abs, "node_modules")):
        return None
    return {"command": "pnpm install", **({"cwd": cwd} if cwd else {})}


# ─── continuation prompts ─────────────────────────────────────────────────────
def _build_continuation_prompt(
    base_prompt: str,
    task: DispatchPlanItem,
    attempt: int,
    continuation_context: str,
) -> str:
    return "\n".join(
        [
            base_prompt,
            "",
            "<continuation>",
            f'You are continuing the same dispatched task "{task.id}". This is attempt '
            f"{attempt}/{MAX_CHILD_TASK_ATTEMPTS}.",
            "Do not restart from scratch if useful files already exist. Inspect the workspace, "
            "fix the missing or failing parts, run the relevant verification, and then call "
            "report_task_result.",
            continuation_context,
            "</continuation>",
        ]
    )


def _build_task_continuation_context(
    task: DispatchPlanItem,
    evaluation: _ChildAttemptEvaluation,
    attempt: int,
    max_attempts: int,
) -> str:
    lines = [
        "<previous_attempt>",
        f"  <attempt>{attempt}/{max_attempts}</attempt>",
        f"  <status>{evaluation.result.status}</status>",
    ]
    if evaluation.result.error:
        lines.append(f"  <error>{escape_xml(evaluation.result.error)}</error>")
    if not evaluation.result.task_report:
        lines.append("  <missing_report>true</missing_report>")
    if task.target_paths:
        lines.append("  <target_paths>")
        for target_path in task.target_paths:
            lines.append(f"    <path>{escape_xml(target_path)}</path>")
        lines.append("  </target_paths>")
    if evaluation.verification_results:
        lines.append("  <verification_results>")
        for result in evaluation.verification_results:
            import json

            prepare_attr = ' prepare="true"' if result.prepare else ""
            lines.append(
                f"    <command text={json.dumps(result.command, ensure_ascii=False)} "
                f'ok="{str(result.ok).lower()}" exitCode="{result.exit_code if result.exit_code is not None else ""}" '
                f'timedOut="{str(result.timed_out).lower()}"{prepare_attr}>'
            )
            if result.cwd:
                lines.append(f"      <cwd>{escape_xml(result.cwd)}</cwd>")
            if result.error:
                lines.append(f"      <error>{escape_xml(result.error)}</error>")
            if result.output:
                lines.append(f"      <output>{escape_xml(result.output[-4000:])}</output>")
            lines.append("    </command>")
        lines.append("  </verification_results>")
    lines.append("</previous_attempt>")
    return "\n".join(lines)


# ─── input / output binding ───────────────────────────────────────────────────
def _bind_implicit_single_output(
    task: DispatchPlanItem,
    result: DispatchTaskResult,
) -> dict[str, str]:
    output_artifacts = dict(result.output_artifacts)
    required_outputs = get_required_expected_outputs(task)
    if len(required_outputs) != 1 or len(result.artifact_ids) != 1:
        return output_artifacts
    output_id = required_outputs[0].id
    if output_artifacts.get(output_id):
        return output_artifacts
    if result.artifact_ids[0] in output_artifacts.values():
        return output_artifacts
    output_artifacts[output_id] = result.artifact_ids[0]
    return output_artifacts


def _bind_project_expected_output(
    task: DispatchPlanItem,
    result: DispatchTaskResult,
    project_artifact_id: str | None,
) -> DispatchTaskResult:
    if not project_artifact_id:
        return result
    project_outputs = [o for o in get_required_expected_outputs(task) if o.type == "project"]
    if not project_outputs:
        return result
    output_artifacts = dict(result.output_artifacts)
    for output in project_outputs:
        output_artifacts.setdefault(output.id, project_artifact_id)
    result.output_artifacts = output_artifacts
    return result


def _evaluate_required_project_outputs(
    task: DispatchPlanItem,
    result: DispatchTaskResult,
) -> tuple[bool, str | None]:
    missing = [
        o
        for o in get_required_expected_outputs(task)
        if o.type == "project" and not result.output_artifacts.get(o.id)
    ]
    if not missing:
        return True, None
    return (
        False,
        f'Task "{task.id}" is missing required project output: '
        + ", ".join(o.id for o in missing),
    )


def _resolve_task_inputs(
    task: DispatchPlanItem,
    upstream: dict[str, DispatchTaskResult],
    plan: list[DispatchPlanItem],
) -> list[ResolvedTaskInput]:
    task_by_id = {item.id: item for item in plan}
    resolved: list[ResolvedTaskInput] = []
    for inp in task.inputs or []:
        upstream_task = task_by_id.get(inp.from_task_id)
        expected_output: DispatchExpectedOutput | None = None
        if upstream_task:
            expected_output = next(
                (o for o in (upstream_task.expected_outputs or []) if o.id == inp.output_id),
                None,
            )
        upstream_result = upstream.get(inp.from_task_id)
        artifact_id = (
            upstream_result.output_artifacts.get(inp.output_id) if upstream_result else None
        )
        resolved.append(
            ResolvedTaskInput(
                input=inp,
                type=expected_output.type if expected_output else None,
                artifact_id=artifact_id,
                missing=not artifact_id,
            )
        )
    return resolved


# ─── terminal task-result constructors ────────────────────────────────────────
def _skipped_missing_inputs_task_result(
    task: DispatchPlanItem,
    missing_inputs: list[ResolvedTaskInput],
) -> DispatchTaskResult:
    missing_text = ", ".join(f"{e.input.from_task_id}.{e.input.output_id}" for e in missing_inputs)
    return DispatchTaskResult(
        run_id=None,
        status="skipped",
        error=(
            f'Skipped because required input artifact(s) were missing for task '
            f'"{task.id}": {missing_text}'
        ),
    )


def _skipped_task_result(
    task: DispatchPlanItem,
    blockers: list[dict],
) -> DispatchTaskResult:
    blocker_text = ", ".join(f"{b['taskId']}:{b['result'].status}" for b in blockers)
    return DispatchTaskResult(
        run_id=None,
        status="skipped",
        error=(
            f'Skipped because upstream task(s) did not complete for task "{task.id}": '
            f"{blocker_text}"
        ),
    )


def _mark_remaining_tasks_aborted(
    plan: list[DispatchPlanItem],
    remaining: set[str],
    results: dict[str, DispatchTaskResult],
    ctx: DagContext,
) -> None:
    for task in plan:
        if task.id not in remaining:
            continue
        result = DispatchTaskResult(
            run_id=None,
            status="aborted",
            error=f'Aborted before task "{task.id}" started',
        )
        results[task.id] = result
        remaining.discard(task.id)
        _publish_dispatch_end(ctx, task.id, result)


def _aborted_before_start_task_result(task: DispatchPlanItem, error: str) -> DispatchTaskResult:
    return DispatchTaskResult(
        run_id=None,
        status="aborted",
        error=f'{error} for task "{task.id}"',
    )


def _publish_dispatch_end(ctx: DagContext, task_id: str, result: DispatchTaskResult) -> None:
    publish(
        DispatchEndEvent(
            conversation_id=ctx.conversation_id,
            timestamp=now_ms(),
            parent_run_id=ctx.parent_run_id,
            child_run_id=result.run_id,
            task_id=task_id,
            status=result.status,
            error=result.error,
        )
    )
