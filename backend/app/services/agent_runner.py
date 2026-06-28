"""Port of src/server/agent-runner.ts (simple path + shared machinery).

Executes one agent run. Two branches:
  - execute_simple_run: plain agent — consume the adapter event stream
  - execute_orchestrator_run: isOrchestrator agent (Core-B; lazy-imported)

This module ports the SIMPLE path plus the primitives shared with the
orchestrator (consume_stream / persist_event / finalize / build_adapter_input /
the Semaphore / execute_run). See specs/06-orchestrator-flow.md.

Port mappings: TS AbortSignal -> per-run asyncio.Event; Promise/AsyncIterable ->
async / async generators; the TS module-global ``db`` singleton -> per-call
``get_db()`` sessions; ``Date.now()`` -> now_ms().
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import and_, select

from app.adapters.base import AdapterAttachment, AdapterInput, CustomConfig
from app.adapters.registry import agent_registry
from app.db.engine import get_db
from app.db.models import Agent, AgentRun, Artifact, Conversation, Message, Workspace
from app.schemas.artifacts import ArtifactRecord
from app.schemas.events import (
    ArtifactCreateEvent,
    MessageEndEvent,
    MessageStartEvent,
    PartStartEvent,
    RunEndEvent,
    RunStartEvent,
    StreamEvent,
    ToolResultEvent,
)
from app.services import runner_registry
from app.services.attachment_service import get_attachment_absolute_path
from app.services.context_compaction_service import prefix_prompt_with_context_summary
from app.services.conversation_context import BuildHistoryOptions, build_history_for
from app.services.event_bus import event_bus
from app.services.project_artifact import build_project_files
from app.services.runner_registry import RunHandle
from app.services.settings_service import get_app_settings
from app.services.task_result_report import (
    REPORT_TASK_RESULT_TOOL_NAME,
    parse_and_normalize,
)
from app.tools.registry import (
    tool_registry,  # noqa: F401 - parity import (tool resolution lives in adapters)
)
from app.utils.clock import now_ms
from app.utils.dispatch_run_evidence import (
    clear_run_tool_evidence,
    get_run_tool_evidence,
)
from app.utils.ids import new_artifact_id, new_run_id
from app.utils.model_registry import estimate_tokens, get_model_limits
from app.utils.workspace_utils import get_effective_cwd

logger = logging.getLogger(__name__)


# ─── PromptAssembler integration (lazy, degrades gracefully) ─────────────────
def _get_prompt_assembler():
    """Retrieve the PromptAssembler from app.state, or None if unavailable."""
    try:
        from app.main import _app_ref
        if _app_ref is None:
            return None
        return getattr(_app_ref.state, "prompt_assembler", None)
    except Exception:
        return None


def _get_memory_service():
    """Retrieve the MemoryService singleton, or None if unavailable."""
    try:
        from app.main import _memory_service
        return _memory_service
    except Exception:
        return None


async def _post_run_memory_hook(
    prompt: str,
    result: RunExecutionResult,
    conversation_id: str,
) -> None:
    """Background hook: write user prompt + agent output into memory subsystem.

    Runs as an asyncio.create_task so it never blocks the main run path.
    """
    ms = _get_memory_service()
    if ms is None:
        return
    try:
        await ms.on_message_end("user", prompt)
        # Collect agent output text from output_message_ids
        if result.output_message_ids:
            async with get_db() as db:
                from app.db.models import Message
                for msg_id in result.output_message_ids:
                    msg = (
                        await db.execute(select(Message).where(Message.id == msg_id))
                    ).scalar_one_or_none()
                    if msg:
                        text_parts = [
                            p.get("content", "")
                            for p in msg.parts_list
                            if p.get("type") == "text"
                        ]
                        agent_text = "\n".join(text_parts)
                        if agent_text:
                            await ms.on_message_end("assistant", agent_text)
    except Exception as e:
        logger.warning("_post_run_memory_hook error: %s", e)


# ─── Args / results (mirror the TS interfaces) ───────────────────────────────
@dataclass
class RunArgs:
    agent_id: str
    conversation_id: str
    trigger_message_id: str
    parent_run_id: str | None = None
    # sub-agent dispatch: external prompt assembled by the Orchestrator
    override_prompt: str | None = None
    # orchestrator stages use different system prompts
    override_system_prompt: str | None = None
    # orchestrator aggregate stage drops plan_tasks
    override_tool_names: list[str] | None = None
    # sub-task runs must report a semantic result via report_task_result
    require_task_report: bool = False
    # parent run's cancel signal — cascade: parent abort -> child abort
    parent_cancel_event: asyncio.Event | None = None


@dataclass
class RunResult:
    run_id: str
    status: str  # 'complete' | 'failed' | 'aborted'
    error: str | None = None
    artifact_ids: list[str] = field(default_factory=list)
    output_message_ids: list[str] = field(default_factory=list)
    output_artifacts: dict[str, str] = field(default_factory=dict)
    task_report: dict[str, Any] | None = None


@dataclass
class RunExecutionResult:
    artifact_ids: list[str] = field(default_factory=list)
    output_message_ids: list[str] = field(default_factory=list)
    output_artifacts: dict[str, str] = field(default_factory=dict)
    task_report: dict[str, Any] | None = None


def _empty_run_execution_result() -> RunExecutionResult:
    return RunExecutionResult(artifact_ids=[], output_message_ids=[], output_artifacts={})


# ─── Constants (port of agent-runner.ts:212) ─────────────────────────────────
SUB_AGENT_CONTEXT_RECENT_LIMIT = 5
MAX_CONCURRENT_SUB_AGENT_RUNS = 4
ASK_USER_TOOL_NAME = "ask_user"
ORCHESTRATOR_PLAN_ALLOWED_TOOLS = {
    "plan_tasks",
    ASK_USER_TOOL_NAME,
    "fs_list",
    "fs_read",
    "read_artifact",
    "read_attachment",
}
MAX_DISPATCH_ROUNDS = 4
MAX_CHILD_TASK_ATTEMPTS = 4
DEFAULT_VERIFICATION_TIMEOUT_MS = 5 * 60_000
DEFAULT_PREPARE_TIMEOUT_MS = 10 * 60_000


# ─── Fair async semaphore (port of the TS Semaphore) ─────────────────────────
class _Semaphore:
    """Throttle concurrent sub-agent runs; FIFO, abort-aware.

    Mirrors the TS Semaphore: acquire returns a release callable, waiters queue
    in FIFO order, and an aborted cancel_event rejects/skips its waiter.
    """

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._active = 0
        self._queue: list[tuple[asyncio.Future[Callable[[], None]], asyncio.Event]] = []

    async def acquire(self, cancel_event: asyncio.Event) -> Callable[[], None]:
        if cancel_event.is_set():
            raise RuntimeError("Semaphore acquire aborted")
        if self._active < self._limit:
            self._active += 1
            return self._create_release()

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Callable[[], None]] = loop.create_future()
        waiter = (fut, cancel_event)
        self._queue.append(waiter)

        def _on_abort() -> None:
            if waiter in self._queue:
                self._queue.remove(waiter)
            if not fut.done():
                fut.set_exception(RuntimeError("Semaphore acquire aborted"))

        # asyncio.Event has no listener API; poll-free abort via a watcher task.
        watcher = asyncio.ensure_future(_wait_event(cancel_event))
        watcher.add_done_callback(lambda _t: _on_abort() if not fut.done() else None)
        try:
            return await fut
        finally:
            watcher.cancel()

    def _create_release(self) -> Callable[[], None]:
        released = False

        def release() -> None:
            nonlocal released
            if released:
                return
            released = True
            self._active -= 1
            self._drain()

        return release

    def _drain(self) -> None:
        while self._active < self._limit and self._queue:
            fut, cancel_event = self._queue.pop(0)
            if fut.done():
                continue
            if cancel_event.is_set():
                continue
            self._active += 1
            fut.set_result(self._create_release())


async def _wait_event(event: asyncio.Event) -> None:
    await event.wait()


# ─── Module state ────────────────────────────────────────────────────────────
# run_id -> (task, cancel_event)
_active_runs: dict[str, tuple[asyncio.Task[RunResult], asyncio.Event]] = {}
sub_agent_run_semaphore = _Semaphore(MAX_CONCURRENT_SUB_AGENT_RUNS)


# ─── Facade (port of AgentRunner.run/abort) ──────────────────────────────────
class AgentRunnerImpl:
    """Synchronous facade: spawn an asyncio task, return the handle immediately."""

    def run(
        self,
        *,
        agent_id: str,
        conversation_id: str,
        trigger_message_id: str,
        parent_run_id: str | None = None,
    ) -> RunHandle:
        run_id = new_run_id()
        cancel_event = asyncio.Event()

        # cascade: parent run abort -> this run abort
        parent_cancel_event: asyncio.Event | None = None
        if parent_run_id:
            parent_entry = _active_runs.get(parent_run_id)
            if parent_entry:
                parent_cancel_event = parent_entry[1]
                if parent_cancel_event.is_set():
                    cancel_event.set()

        args = RunArgs(
            agent_id=agent_id,
            conversation_id=conversation_id,
            trigger_message_id=trigger_message_id,
            parent_run_id=parent_run_id,
            parent_cancel_event=parent_cancel_event,
        )
        task = asyncio.create_task(execute_run(run_id, cancel_event, args))
        _active_runs[run_id] = (task, cancel_event)
        task.add_done_callback(lambda _t: _active_runs.pop(run_id, None))
        task.add_done_callback(_log_uncaught)
        return RunHandle(run_id=run_id)

    def abort(self, run_id: str) -> bool:
        entry = _active_runs.get(run_id)
        if not entry:
            return False
        task, cancel_event = entry
        # Idempotent: once a run is already cancelling, do NOT cancel the task again.
        # A second task.cancel() can interrupt finalize() before it publishes RunEndEvent,
        # which drops the run.end event and leaves the frontend retrying abort forever.
        if cancel_event.is_set():
            return True
        cancel_event.set()
        task.cancel()  # best-effort: stop pending awaits promptly
        return True


def _log_uncaught(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    err = task.exception()
    if err is not None:
        logger.error("[AgentRunner] uncaught error", exc_info=err)


# ─── Run-from-args (used by the orchestrator to spawn children) ───────────────
def run_with_args(args: RunArgs) -> tuple[str, asyncio.Task[RunResult], asyncio.Event]:
    """Spawn a run from a full RunArgs (override prompt / parent signal / etc.).

    The orchestrator needs the override fields and a handle on the spawned task
    (to await the child's RunResult), which the registry-facing ``run`` hides.
    """
    run_id = new_run_id()
    cancel_event = asyncio.Event()

    if args.parent_cancel_event is not None:
        if args.parent_cancel_event.is_set():
            cancel_event.set()
        else:
            # cascade parent abort onto this child
            watcher = asyncio.ensure_future(_wait_event(args.parent_cancel_event))
            watcher.add_done_callback(lambda _t: cancel_event.set())

    task = asyncio.create_task(execute_run(run_id, cancel_event, args))
    _active_runs[run_id] = (task, cancel_event)
    task.add_done_callback(lambda _t: _active_runs.pop(run_id, None))
    task.add_done_callback(_log_uncaught)
    return run_id, task, cancel_event


# ─── Main entry ──────────────────────────────────────────────────────────────
async def execute_run(
    run_id: str, cancel_event: asyncio.Event, args: RunArgs
) -> RunResult:
    """Load prerequisites, dispatch to simple/orchestrator, always finalize."""
    async with get_db() as db:
        agent = (
            await db.execute(select(Agent).where(Agent.id == args.agent_id))
        ).scalar_one_or_none()
        if not agent:
            return await finalize_failed(run_id, args, f"Agent not found: {args.agent_id}")

        workspace = (
            await db.execute(
                select(Workspace).where(Workspace.conversation_id == args.conversation_id)
            )
        ).scalar_one_or_none()
        if not workspace:
            return await finalize_failed(
                run_id, args, f"Workspace not found for conversation: {args.conversation_id}"
            )

        trigger_message = (
            await db.execute(
                select(Message).where(
                    and_(
                        Message.id == args.trigger_message_id,
                        Message.conversation_id == args.conversation_id,
                    )
                )
            )
        ).scalar_one_or_none()
        if not trigger_message:
            return await finalize_failed(
                run_id, args, f"Trigger message not found: {args.trigger_message_id}"
            )

        is_orchestrator = agent.is_orchestrator
        trigger_parts = trigger_message.parts_list

    prompt = args.override_prompt or _extract_text_from_parts(trigger_parts)

    # parse trigger-message attachments (skip for sub-runs / overridePrompt to
    # avoid the sub-agent re-processing the same files)
    attachments: list[AdapterAttachment] = []
    if not args.override_prompt:
        for p in trigger_parts:
            if p.get("type") in ("image_attachment", "file_attachment"):
                abs_path = await get_attachment_absolute_path(p["attachmentId"])
                if abs_path:
                    attachments.append(
                        AdapterAttachment(
                            id=p["attachmentId"],
                            file_name=p["fileName"],
                            mime_type=p["mimeType"],
                            kind="image" if p["type"] == "image_attachment" else "file",
                            abs_path=abs_path,
                        )
                    )

    await insert_run(run_id, args, args.agent_id)
    publish(
        RunStartEvent(
            conversation_id=args.conversation_id,
            timestamp=now_ms(),
            run_id=run_id,
            agent_id=args.agent_id,
            trigger_message_id=args.trigger_message_id,
            parent_run_id=args.parent_run_id,
        )
    )

    try:
        if is_orchestrator:
            # lazy import to break the agent_runner <-> orchestrator import cycle
            from app.services.orchestrator import execute_orchestrator_run

            result = await execute_orchestrator_run(
                run_id, cancel_event, args, prompt, attachments
            )
        else:
            result = await execute_simple_run(
                run_id, cancel_event, args, prompt, attachments
            )
        if cancel_event.is_set():
            return await finalize(run_id, args, "aborted", result)
        final_result = await finalize_ok(run_id, args, result)
        # ─── Post-run memory hook (Task 5.4) ───
        asyncio.create_task(
            _post_run_memory_hook(prompt, result, args.conversation_id)
        )
        return final_result
    except asyncio.CancelledError:
        return await finalize(run_id, args, "aborted", _empty_run_execution_result())
    except Exception as err:  # noqa: BLE001 - faithful catch-all; surfaced via finalize
        if cancel_event.is_set():
            return await finalize(run_id, args, "aborted", _empty_run_execution_result())
        return await finalize(
            run_id, args, "failed", _empty_run_execution_result(), str(err)
        )


# ─── Simple agent ────────────────────────────────────────────────────────────
async def execute_simple_run(
    run_id: str,
    cancel_event: asyncio.Event,
    args: RunArgs,
    prompt: str,
    attachments: list[AdapterAttachment],
) -> RunExecutionResult:
    async with get_db() as db:
        agent = (
            await db.execute(select(Agent).where(Agent.id == args.agent_id))
        ).scalar_one()
        workspace = (
            await db.execute(
                select(Workspace).where(Workspace.conversation_id == args.conversation_id)
            )
        ).scalar_one()
        db.expunge(agent)
        db.expunge(workspace)

    base_tool_names = args.override_tool_names or agent.tool_names_list

    # Task 1.1: Implicitly inject memory_recall for custom agents
    if agent.adapter_name == "custom":
        if "memory_recall" not in base_tool_names:
            base_tool_names = ["memory_recall"] + list(base_tool_names)
            logger.info(
                "[AgentRunner] Implicitly injected memory_recall tool for custom agent %s",
                args.agent_id,
            )

    # Inject load_skill when a custom agent has equipped (and still-present) skills.
    if agent.adapter_name == "custom" and agent.skill_names_list:
        from app.services.skill_service import list_skills
        available = {m.slug for m in list_skills()}
        if any(s in available for s in agent.skill_names_list) and "load_skill" not in base_tool_names:
            base_tool_names = list(base_tool_names) + ["load_skill"]
            logger.info(
                "[AgentRunner] Injected load_skill for custom agent %s (equipped skills)",
                args.agent_id,
            )

    # Task 4.1: Dynamically inject RAG tools if conversation has rag_enabled=true
    RAG_TOOLS = ["rag_search", "rag_ingest", "rag_list_documents", "rag_delete_document"]
    async with get_db() as db:
        from app.db.models import Conversation
        conv = (
            await db.execute(select(Conversation).where(Conversation.id == args.conversation_id))
        ).scalar_one_or_none()
        if conv and conv.rag_enabled:
            # Only inject for custom agents (exclude SDK agents)
            if agent.adapter_name == "custom":
                existing = set(base_tool_names)
                new_tools = [t for t in RAG_TOOLS if t not in existing]
                if new_tools:
                    base_tool_names = list(base_tool_names) + new_tools
                    logger.info(
                        "[AgentRunner] Injected RAG tools %s for conversation %s (rag_enabled=true)",
                        new_tools,
                        args.conversation_id,
                    )

    tool_names = (
        _ensure_includes(base_tool_names, REPORT_TASK_RESULT_TOOL_NAME)
        if args.require_task_report
        else base_tool_names
    )

    adapter = agent_registry.get_adapter(agent)
    adapter_input = await build_adapter_input(
        args, agent, run_id, prompt, workspace, tool_names, args.override_system_prompt, attachments
    )
    stream = adapter.stream(adapter_input, cancel_event)

    result = await consume_stream(stream, args.agent_id, run_id)
    if args.parent_run_id:
        return result

    try:
        await maybe_create_project_artifact(
            evidence_run_id=run_id,
            conversation_id=args.conversation_id,
            agent_id=args.agent_id,
            result=result,
        )
    finally:
        clear_run_tool_evidence(run_id)
    return result


# ─── project artifact (port of maybeCreateProjectArtifact) ───────────────────
async def maybe_create_project_artifact(
    *,
    evidence_run_id: str,
    conversation_id: str,
    agent_id: str,
    result: RunExecutionResult,
    task_id: str | None = None,
) -> str | None:
    """Auto-create a 'project' artifact from applied fs_write evidence."""
    evidence = get_run_tool_evidence(evidence_run_id)
    if len(evidence.file_writes) == 0:
        return None

    async with get_db() as db:
        workspace = (
            await db.execute(
                select(Workspace).where(Workspace.conversation_id == conversation_id)
            )
        ).scalar_one_or_none()
        if not workspace:
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

    # ArtifactContent stays camelCase on the wire / in the DB JSON column.
    content: dict[str, Any] = {
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


# ─── Stream consumption + persistence (port of consumeStream) ────────────────
# onToolCall control: return None, or {"stop": True, "result": ..., "isError": bool}
ToolCallControl = dict[str, Any] | None


async def consume_stream(
    stream: AsyncIterable[StreamEvent],
    agent_id: str,
    run_id: str,
    on_tool_call: Callable[[StreamEvent], ToolCallControl] | None = None,
) -> RunExecutionResult:
    parts_buffer: dict[str, list[dict]] = {}
    artifact_ids: list[str] = []
    output_message_ids: list[str] = []
    output_artifacts: dict[str, str] = {}
    output_key_by_artifact_id: dict[str, str] = {}
    tool_name_by_call_id: dict[str, str] = {}
    task_report: dict[str, Any] | None = None
    current_message_id: str | None = None

    async for event in stream:
        if event.type == "message.start":
            current_message_id = event.message_id
        if event.type == "tool.call":
            tool_name_by_call_id[event.call_id] = event.tool_name

        await persist_event(
            event, parts_buffer, run_id, agent_id, output_message_ids, artifact_ids
        )
        publish(event)

        if event.type == "artifact.create":
            output_key = output_key_by_artifact_id.get(event.artifact.id)
            if output_key:
                output_artifacts[output_key] = event.artifact.id

        # tool-produced artifact: append an artifact_ref part to the live message
        if event.type == "artifact.create" and current_message_id:
            parts = parts_buffer.get(current_message_id, [])
            part_index = len(parts)
            ref_part = {"type": "artifact_ref", "artifactId": event.artifact.id}
            parts.append(ref_part)
            parts_buffer[current_message_id] = parts
            await _update_message_parts(current_message_id, parts)
            publish(
                PartStartEvent(
                    conversation_id=event.conversation_id,
                    timestamp=now_ms(),
                    message_id=current_message_id,
                    part_index=part_index,
                    part=ref_part,
                )
            )

        if event.type == "deploy.status":
            parts = parts_buffer.get(event.message_id, [])
            part_index = len(parts)
            deploy_part = {
                "type": "deploy_status",
                "deployment": event.deployment.model_dump(by_alias=True),
            }
            parts.append(deploy_part)
            parts_buffer[event.message_id] = parts
            await _update_message_parts(event.message_id, parts)
            publish(
                PartStartEvent(
                    conversation_id=event.conversation_id,
                    timestamp=now_ms(),
                    message_id=event.message_id,
                    part_index=part_index,
                    part=deploy_part,
                )
            )

        if event.type == "message.end":
            current_message_id = None
        if event.type == "tool.result":
            tool_name = tool_name_by_call_id.get(event.call_id)
            if tool_name and not event.is_error and tool_name == REPORT_TASK_RESULT_TOOL_NAME:
                report, _err = parse_and_normalize(event.result)
                if report:
                    task_report = report
            handoff = _read_artifact_handoff_result(event.result)
            if handoff:
                output_key_by_artifact_id[handoff[0]] = handoff[1]
        if event.type == "tool.call":
            control = on_tool_call(event) if on_tool_call else None
            if control and control.get("stop"):
                if "result" in control:
                    result_event = ToolResultEvent(
                        conversation_id=event.conversation_id,
                        timestamp=now_ms(),
                        message_id=event.message_id,
                        call_id=event.call_id,
                        result=control["result"],
                        is_error=bool(control.get("isError", False)),
                    )
                    await persist_event(
                        result_event, parts_buffer, run_id, agent_id, output_message_ids, artifact_ids
                    )
                    publish(result_event)

                end_event = MessageEndEvent(
                    conversation_id=event.conversation_id,
                    timestamp=now_ms(),
                    message_id=event.message_id,
                )
                await persist_event(
                    end_event, parts_buffer, run_id, agent_id, output_message_ids, artifact_ids
                )
                publish(end_event)
                current_message_id = None
                break

    return RunExecutionResult(
        artifact_ids=artifact_ids,
        output_message_ids=output_message_ids,
        output_artifacts=output_artifacts,
        task_report=task_report,
    )


def _read_artifact_handoff_result(result: Any) -> tuple[str, str] | None:
    if not isinstance(result, dict):
        return None
    artifact_id = result.get("artifactId")
    output_key = result.get("outputKey")
    if not isinstance(artifact_id, str) or not isinstance(output_key, str):
        return None
    if not output_key.strip():
        return None
    return artifact_id, output_key


async def persist_event(
    event: StreamEvent,
    parts_buffer: dict[str, list[dict]],
    run_id: str,
    agent_id: str,
    output_message_ids: list[str],
    artifact_ids: list[str],
) -> None:
    """Persist a stream event into the messages / runs tables (camelCase parts)."""
    etype = event.type
    if etype == "run.usage":
        # adapter-reported run token usage -> agent_runs.usage (latest wins)
        async with get_db() as db:
            run = (
                await db.execute(select(AgentRun).where(AgentRun.id == event.run_id))
            ).scalar_one_or_none()
            if run is not None:
                run.usage_dict = event.usage.model_dump(by_alias=True)
        return
    if etype == "message.usage":
        async with get_db() as db:
            msg = (
                await db.execute(select(Message).where(Message.id == event.message_id))
            ).scalar_one_or_none()
            if msg is not None:
                msg.usage_dict = event.usage.model_dump(by_alias=True)
        return
    if etype == "message.start":
        parts_buffer[event.message_id] = []
        output_message_ids.append(event.message_id)
        async with get_db() as db:
            msg = Message(
                id=event.message_id,
                conversation_id=event.conversation_id,
                role="agent",
                agent_id=agent_id,
                status="streaming",
                run_id=run_id,
                created_at=event.timestamp,
            )
            msg.parts_list = []
            msg.mentioned_agent_ids_list = []
            db.add(msg)
        return
    if etype == "part.start":
        parts = parts_buffer.get(event.message_id, [])
        # grow the list so part_index lands in place (TS array index assignment)
        while len(parts) <= event.part_index:
            parts.append({})
        parts[event.part_index] = event.part
        parts_buffer[event.message_id] = parts
        await _update_message_parts(event.message_id, parts)
        return
    if etype == "part.delta":
        parts = parts_buffer.get(event.message_id)
        if not parts:
            return
        if event.part_index >= len(parts):
            return
        part = parts[event.part_index]
        if not part:
            return
        dtype = event.delta.get("type")
        text = event.delta.get("text", "")
        # each append delta only applies to its matching part type
        appendable = {"text.append": "text", "thinking.append": "thinking", "code.append": "code"}
        if appendable.get(dtype) == part.get("type"):
            part["content"] = part.get("content", "") + text
        await _update_message_parts(event.message_id, parts)
        return
    if etype == "tool.call":
        parts = parts_buffer.get(event.message_id, [])
        parts.append(
            {
                "type": "tool_use",
                "callId": event.call_id,
                "toolName": event.tool_name,
                "args": event.args,
            }
        )
        parts_buffer[event.message_id] = parts
        await _update_message_parts(event.message_id, parts)
        return
    if etype == "tool.result":
        parts = parts_buffer.get(event.message_id, [])
        parts.append(
            {
                "type": "tool_result",
                "callId": event.call_id,
                "result": event.result,
                "isError": event.is_error,
            }
        )
        parts_buffer[event.message_id] = parts
        await _update_message_parts(event.message_id, parts)
        return
    if etype == "message.end":
        async with get_db() as db:
            msg = (
                await db.execute(select(Message).where(Message.id == event.message_id))
            ).scalar_one_or_none()
            if msg is not None:
                msg.status = "complete"
        parts_buffer.pop(event.message_id, None)
        return
    if etype == "artifact.create":
        artifact_ids.append(event.artifact.id)
        return


async def _update_message_parts(message_id: str, parts: list[dict]) -> None:
    async with get_db() as db:
        msg = (
            await db.execute(select(Message).where(Message.id == message_id))
        ).scalar_one_or_none()
        if msg is not None:
            msg.parts_list = parts


# ─── DB / event helpers ──────────────────────────────────────────────────────
async def insert_run(run_id: str, args: RunArgs, agent_id: str) -> None:
    async with get_db() as db:
        db.add(
            AgentRun(
                id=run_id,
                conversation_id=args.conversation_id,
                agent_id=agent_id,
                trigger_message_id=args.trigger_message_id,
                status="running",
                parent_run_id=args.parent_run_id,
                started_at=now_ms(),
            )
        )


async def finalize(
    run_id: str,
    args: RunArgs,
    status: str,  # 'complete' | 'failed' | 'aborted'
    result: RunExecutionResult,
    error: str | None = None,
) -> RunResult:
    finished_at = now_ms()

    if status in ("failed", "aborted"):
        await _persist_unresolved_tool_failures(
            run_id, args.conversation_id, status, error, finished_at
        )

    async with get_db() as db:
        run = (
            await db.execute(select(AgentRun).where(AgentRun.id == run_id))
        ).scalar_one_or_none()
        if run is not None:
            run.status = status
            run.finished_at = finished_at
            run.error = error

        # any message still 'streaming' for this run -> terminal status
        streaming = (
            await db.execute(
                select(Message).where(
                    and_(Message.run_id == run_id, Message.status == "streaming")
                )
            )
        ).scalars().all()
        terminal = "complete" if status == "complete" else "aborted" if status == "aborted" else "error"
        for msg in streaming:
            msg.status = terminal

    if status in ("failed", "aborted"):
        await _emit_error_visualisation(run_id, args, status, error, result.output_message_ids)

    async with get_db() as db:
        conv = (
            await db.execute(select(Conversation).where(Conversation.id == args.conversation_id))
        ).scalar_one_or_none()
        if conv is not None:
            conv.updated_at = finished_at

    publish(
        RunEndEvent(
            conversation_id=args.conversation_id,
            timestamp=finished_at,
            run_id=run_id,
            status=status,
            error=error,
        )
    )

    return RunResult(
        run_id=run_id,
        status=status,
        error=error,
        artifact_ids=result.artifact_ids,
        output_message_ids=result.output_message_ids,
        output_artifacts=result.output_artifacts,
        task_report=result.task_report,
    )


async def _emit_error_visualisation(
    run_id: str,
    args: RunArgs,
    status: str,  # 'failed' | 'aborted'
    error: str | None,
    output_message_ids: list[str],
) -> None:
    error_text = "[已中止]" if status == "aborted" else f"[失败] {error or '未知错误'}"
    now = now_ms()

    # prefer: append the error to this run's latest agent message, if any
    last_message_id = output_message_ids[-1] if output_message_ids else None
    if last_message_id:
        async with get_db() as db:
            msg = (
                await db.execute(select(Message).where(Message.id == last_message_id))
            ).scalar_one_or_none()
            if msg is not None:
                parts = [*msg.parts_list, {"type": "text", "content": error_text}]
                msg.parts_list = parts
                publish(
                    PartStartEvent(
                        conversation_id=args.conversation_id,
                        timestamp=now,
                        message_id=last_message_id,
                        part_index=len(parts) - 1,
                        part={"type": "text", "content": error_text},
                    )
                )
                return

    # else: create a fresh error message
    error_message_id = f"msg_err_{run_id}"
    async with get_db() as db:
        msg = Message(
            id=error_message_id,
            conversation_id=args.conversation_id,
            role="agent",
            agent_id=args.agent_id,
            status="error",
            run_id=run_id,
            created_at=now,
        )
        msg.parts_list = [{"type": "text", "content": error_text}]
        msg.mentioned_agent_ids_list = []
        db.add(msg)
    publish(
        MessageStartEvent(
            conversation_id=args.conversation_id,
            timestamp=now,
            message_id=error_message_id,
            agent_id=args.agent_id,
            run_id=run_id,
        )
    )
    publish(
        PartStartEvent(
            conversation_id=args.conversation_id,
            timestamp=now,
            message_id=error_message_id,
            part_index=0,
            part={"type": "text", "content": error_text},
        )
    )
    publish(
        MessageEndEvent(
            conversation_id=args.conversation_id,
            timestamp=now,
            message_id=error_message_id,
        )
    )


async def _persist_unresolved_tool_failures(
    run_id: str,
    conversation_id: str,
    status: str,  # 'failed' | 'aborted'
    error: str | None,
    timestamp: int,
) -> None:
    """Close any tool_use parts with no matching tool_result (synthesize an error)."""
    result = _build_unresolved_tool_failure_result(status, error)
    async with get_db() as db:
        messages = (
            await db.execute(select(Message).where(Message.run_id == run_id))
        ).scalars().all()
        published: list[tuple[str, str]] = []
        for message in messages:
            next_parts = [*message.parts_list]
            completed_call_ids = {
                p["callId"] for p in next_parts if p.get("type") == "tool_result"
            }
            missing_call_ids: list[str] = []
            for part in list(next_parts):
                if part.get("type") != "tool_use" or part.get("callId") in completed_call_ids:
                    continue
                call_id = part["callId"]
                next_parts.append(
                    {
                        "type": "tool_result",
                        "callId": call_id,
                        "result": result,
                        "isError": True,
                    }
                )
                completed_call_ids.add(call_id)
                missing_call_ids.append(call_id)

            if not missing_call_ids:
                continue
            message.parts_list = next_parts
            for call_id in missing_call_ids:
                published.append((message.id, call_id))

    for message_id, call_id in published:
        publish(
            ToolResultEvent(
                conversation_id=conversation_id,
                timestamp=timestamp,
                message_id=message_id,
                call_id=call_id,
                result=result,
                is_error=True,
            )
        )


def _build_unresolved_tool_failure_result(status: str, error: str | None) -> str:
    if status == "aborted":
        return "工具调用未完成：本次运行已中止。"
    return (
        f"工具调用未完成：本次运行失败。{error}"
        if error
        else "工具调用未完成：本次运行失败。"
    )


async def finalize_ok(run_id: str, args: RunArgs, result: RunExecutionResult) -> RunResult:
    return await finalize(run_id, args, "complete", result)


async def finalize_failed(run_id: str, args: RunArgs, error: str) -> RunResult:
    return await finalize(run_id, args, "failed", _empty_run_execution_result(), error)


def publish(event: StreamEvent) -> None:
    event_bus.publish(event)


# ─── Adapter input construction (port of buildAdapterInput) ──────────────────
# system note appended in group chats so an agent reads `[name] ...` user lines
# as other agents' turns, not its own. Port of agent-runner.ts GROUP_CHAT_SYSTEM_NOTE.
GROUP_CHAT_SYSTEM_NOTE = "\n".join(
    [
        "<group_chat_context>",
        "你正处在一个多 agent 群聊里。历史消息中以 `[某个名字]` 开头的 user 消息，",
        "是群里其他成员（人类用户或别的 agent）的发言，不是你自己的输出。",
        "不以名字前缀开头的 user 消息是当前需要你回应的请求。",
        "请据此理解上下文，不要把别人的发言当成自己说过的话。",
        "</group_chat_context>",
    ]
)


def _build_skill_metadata_block(agent: Agent) -> str:
    """Render equipped skills as name+description only (progressive disclosure).

    The SKILL.md body is NEVER inlined here — the model calls load_skill(slug) to
    read it on demand. Only custom agents consume skills; missing slugs are skipped.
    """
    if agent.adapter_name != "custom" or not agent.skill_names_list:
        return ""
    from app.services.skill_service import list_skills

    by_slug = {m.slug: m for m in list_skills()}
    equipped = [by_slug[s] for s in agent.skill_names_list if s in by_slug]
    if not equipped:
        return ""

    lines = [
        "【可用技能】你装备了以下技能。当任务匹配某技能描述时，先调用 "
        "load_skill(name=<slug>) 读取其完整说明再执行；技能附带的脚本/文件用 "
        "fs_read 读取、用 bash 运行。",
    ]
    for m in equipped:
        lines.append(f"- {m.slug}: {m.description}")
    return "\n".join(lines)


async def build_adapter_input(
    args: RunArgs,
    agent: Agent,
    run_id: str,
    prompt: str,
    workspace: Workspace,
    tool_names: list[str],
    system_prompt_override: str | None,
    attachments: list[AdapterAttachment],
) -> AdapterInput:
    effective_cwd = get_effective_cwd(workspace)
    base_system_prompt = system_prompt_override or agent.system_prompt
    system_prompt_with_workspace = (
        _build_workspace_context_block(workspace) + "\n\n" + base_system_prompt
    )
    tool_guidance = _build_agent_hub_tool_guidance(agent, tool_names, workspace)
    if tool_guidance:
        system_prompt_with_workspace += "\n\n" + tool_guidance

    skill_block = _build_skill_metadata_block(agent)
    if skill_block:
        system_prompt_with_workspace += "\n\n" + skill_block

    # key precedence: agent.api_key > app_settings.* > adapter env fallback.
    # only inject global settings when the per-agent field is empty.
    effective_api_key = agent.api_key
    effective_api_base_url = agent.api_base_url
    if not effective_api_key or (
        not effective_api_base_url and agent.adapter_name == "claude-code"
    ):
        settings = await get_app_settings()
        if not effective_api_key:
            effective_api_key = _pick_settings_key(settings, agent)
        if not effective_api_base_url and agent.adapter_name == "claude-code":
            effective_api_base_url = settings.anthropic_base_url

    # cross-run history (only CustomAdapter consumes it; SDK adapters resume sessions).
    history: list[dict] = []
    if agent.adapter_name == "custom" and not args.override_prompt:
        async with get_db() as db:
            conv = (
                await db.execute(
                    select(Conversation).where(Conversation.id == args.conversation_id)
                )
            ).scalar_one_or_none()
            agent_count = len(conv.agent_ids_list) if conv else 0
        if agent_count > 1:
            system_prompt_with_workspace += "\n\n" + GROUP_CHAT_SYSTEM_NOTE

        limits = get_model_limits(agent.model_provider, agent.model_id)
        prompt_estimate = (
            estimate_tokens(system_prompt_with_workspace) + estimate_tokens(prompt) + 512
        )
        history_budget = max(0, limits.context_window - limits.output_reserve - prompt_estimate)
        try:
            history = await build_history_for(
                agent.id,
                args.conversation_id,
                BuildHistoryOptions(
                    exclude_message_id=args.trigger_message_id,
                    token_budget=history_budget,
                ),
            )
        except Exception as err:  # noqa: BLE001 - degrade to no-history rather than crash
            logger.warning(
                "[agent-runner] build_history_for failed; continuing without history: %s",
                err,
            )
            history = []

    # ─── PromptAssembler enrichment (Task 5.3) ───
    assembler = _get_prompt_assembler()
    if assembler and not args.override_prompt:
        try:
            from app.services.prompt_assembler import Query
            mode = "react" if "plan_tasks" in (tool_names or []) else "chat"
            q = Query(mode=mode, text=prompt, conversation_id=args.conversation_id)
            ctx = await assembler.assemble(q)
            enriched = ctx.render_system_prompt()
            if enriched:
                system_prompt_with_workspace += "\n\n" + enriched
        except Exception as err:  # noqa: BLE001 - assembler is best-effort
            logger.warning("[agent-runner] PromptAssembler enrichment failed: %s", err)

    effective_prompt = prompt
    if agent.adapter_name in ("claude-code", "codex") and not args.override_prompt:
        try:
            effective_prompt = await prefix_prompt_with_context_summary(
                args.conversation_id, prompt
            )
        except Exception as err:  # noqa: BLE001 - summary is best-effort
            logger.warning(
                "[agent-runner] prefix_prompt_with_context_summary failed; "
                "continuing without summary: %s",
                err,
            )
            effective_prompt = prompt

    custom_config = (
        CustomConfig(
            model_provider=agent.model_provider,
            supports_vision=agent.supports_vision,
        )
        if agent.adapter_name == "custom" and agent.model_provider and agent.model_id
        else None
    )

    return AdapterInput(
        agent_id=agent.id,
        conversation_id=args.conversation_id,
        run_id=run_id,
        prompt=effective_prompt,
        workspace_path=effective_cwd,
        system_prompt=system_prompt_with_workspace,
        api_key=effective_api_key,
        api_base_url=effective_api_base_url,
        model_id=agent.model_id,
        tool_names=tool_names,
        attachments=attachments if len(attachments) > 0 else None,
        history=history if len(history) > 0 else None,
        custom_config=custom_config,
    )


def _pick_settings_key(settings: Any, agent: Agent) -> str | None:
    """Pick the global settings key matching the agent's adapter / provider."""
    import os

    if agent.adapter_name == "claude-code":
        return (
            settings.anthropic_api_key
            or os.environ.get("ANTHROPIC_AUTH_TOKEN")
            or os.environ.get("ANTHROPIC_API_KEY")
        )
    if agent.adapter_name == "codex":
        return (
            settings.openai_api_key
            or os.environ.get("CODEX_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
        )
    provider = agent.model_provider
    if provider == "anthropic":
        return settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
    if provider == "openai":
        return settings.openai_api_key or os.environ.get("OPENAI_API_KEY")
    if provider == "deepseek":
        return settings.deepseek_api_key or os.environ.get("DEEPSEEK_API_KEY")
    if provider == "volcano-ark":
        return settings.ark_api_key or os.environ.get("ARK_API_KEY")
    return None


def _build_workspace_context_block(workspace: Workspace) -> str:
    """Inject a `<workspace_info>` block so the LLM knows its real cwd / mode."""
    cwd = get_effective_cwd(workspace)
    if workspace.mode == "local":
        return "\n".join(
            [
                "<workspace_info>",
                f"  <cwd>{cwd}</cwd>",
                "  <mode>local</mode>",
                "  <note>This directory is the user's REAL local project on their machine. "
                "Files inside it are their actual code. When you use fs_list / fs_read / "
                "fs_write / bash, you are reading and modifying real files — be careful. "
                "You CAN access these files directly via the workspace tools; do not tell "
                "the user you cannot access local files.</note>",
                "</workspace_info>",
            ]
        )
    return "\n".join(
        [
            "<workspace_info>",
            f"  <cwd>{cwd}</cwd>",
            "  <mode>sandbox</mode>",
            "  <note>This is an isolated sandbox directory (under .agenthub-data/). It is NOT "
            "the user's real codebase. Files you write here are only visible inside this "
            "conversation.</note>",
            "</workspace_info>",
        ]
    )


def _build_agent_hub_tool_guidance(
    agent: Agent, tool_names: list[str], workspace: Workspace
) -> str:
    """Build the per-tool usage guidance appended to the system prompt."""
    tools = set(tool_names)
    is_sdk_agent = agent.adapter_name in ("claude-code", "codex")
    if is_sdk_agent:
        sdk_agent_hub_tools = (
            ["read_artifact", "read_attachment", "fs_list", ASK_USER_TOOL_NAME]
            if "plan_tasks" in tools
            else [
                "write_artifact",
                "read_artifact",
                "deploy_artifact",
                "deploy_workspace",
                ASK_USER_TOOL_NAME,
                REPORT_TASK_RESULT_TOOL_NAME,
            ]
        )
        tools.update(sdk_agent_hub_tools)
    is_plan_stage = "plan_tasks" in tools

    sections: list[str] = []

    def add(lines: list[str]) -> None:
        sections.append("\n".join(lines))

    has_workspace_file_tools = not is_plan_stage and (
        "fs_read" in tools or "fs_write" in tools or "bash" in tools or is_sdk_agent
    )

    if len(tools) > 0:
        add(
            [
                "## AChat 工具调用规范",
                "- 需要调用工具时，必须用工具调用通道提交结构化参数，不要把 JSON 示例写进普通回复里假装调用。",
                "- 字段名必须严格使用工具 schema 里的 camelCase，例如 artifactId、attachmentId、"
                "parentArtifactId、outputKey、dependsOn、expectedOutputs、acceptanceCriteria、acceptanceResults。",
                "- 不要编造 artifactId、attachmentId、outputKey、文件路径；只能使用上下文里明确给出的 id / 路径。",
                "- 工具返回 ok:false 或 isError=true 时，先根据错误修正参数；不要继续基于失败结果推进。",
            ]
        )

    if workspace.mode == "local" and has_workspace_file_tools:
        add(
            [
                "## 本地项目模式",
                "当前 workspace 是用户绑定的真实本地文件夹。用户要求创建、修改、初始化、调试、构建前后端项目或源码文件时，必须优先直接操作 workspace 文件。",
                (
                    "- 使用 SDK 自带的 Read / Write / Edit / Bash / shell 工具读写文件、安装依赖、运行构建与测试。"
                    if is_sdk_agent
                    else "- 使用 fs_read / fs_write / bash 读写文件、安装依赖、运行构建与测试。"
                ),
                "- 不要用 write_artifact 保存应该落盘到本地项目的源码、package.json、tsconfig、server/client 文件或构建配置。",
                "- 如果本地项目已经构建出 dist / build / out / client/dist 等静态目录，可用 deploy_workspace 为该目录生成部署预览卡。",
                "- write_artifact 只用于用户明确要求 artifact / 可预览原型 / 独立 demo / 文档交接，或任务本身声明需要 artifact handoff。",
                "- 完成本地项目改动后，优先运行必要的验证命令（install / typecheck / build / test）；如果无法运行，说明具体原因。",
            ]
        )
    elif workspace.mode == "local" and "write_artifact" in tools:
        add(
            [
                "## 本地项目模式",
                "当前 workspace 是用户绑定的真实本地文件夹，但这个 agent 没有文件/命令工具，不能直接修改本地项目。",
                "- 如果用户要求写入本地项目源码，应说明当前 agent 缺少 fs_read / fs_write / bash 或 SDK 本地工具，而不是用 write_artifact 假装已经落盘。",
                "- 只有用户明确要求 artifact / 可预览原型 / 独立 demo / 文档交接时，才使用 write_artifact。",
            ]
        )

    if ASK_USER_TOOL_NAME in tools:
        add(
            [
                "### ask_user",
                "用途：当继续执行前需要用户在有限方案中选择时，发起结构化问答；不要只在普通文本里问。",
                '正确案例：产品范围不清，调用 ask_user({ questions: [{ header: "范围", question: "这次先做哪个范围?", options: [{ label: "核心流程", description: "先打通主路径，风险最低" }, { label: "完整后台", description: "覆盖更多页面，但耗时更长" }] }] })。',
                "参数规则：每次 1-4 个 questions，每题 2-4 个 options；header 是短标签，question 是完整问题，label 是按钮短文本，description 写清选择后果。",
                "错误案例：直接回复“你想做核心流程还是完整后台？”然后停止；这样 UI 不会出现结构化选择，也不会阻塞 run 等待答案。",
                "不要滥用：开放式讨论、非关键细节、或可以保守决策时，直接说明假设并继续。",
            ]
        )

    if "read_attachment" in tools:
        add(
            [
                "### read_attachment",
                "用途：用户上传了文本/文件附件且任务依赖附件内容时，先读取附件；不要只凭文件名猜测。",
                '正确案例：看到上下文有 attachmentId="att_123"，调用 read_attachment({ attachmentId: "att_123" }) 后再总结或实现。',
                "常见错误：传 { id: \"att_123\" } 或把 art_* 产物 id 传给 read_attachment；产物必须用 read_artifact。",
                "错误案例：把“需求.docx”文件名当作完整需求内容。",
            ]
        )

    if "read_artifact" in tools:
        add(
            [
                "### read_artifact",
                "用途：需要基于已有产物继续设计、实现、审查或修改时，先读取完整产物内容。",
                '正确案例：上游只给出 <artifact id="art_123" />，调用 read_artifact({ artifactId: "art_123" })。',
                "常见错误：传 { id: \"art_123\" }、{ artifact_id: \"art_123\" }，或把 att_* 附件 id 传给 read_artifact。",
                "错误案例：只根据 artifact 标题或摘要判断内容，直接改写或审查。",
            ]
        )

    if "write_artifact" in tools:
        add(
            [
                "### write_artifact",
                "用途：创建用户需要预览、下载、交接或长期保存的产物；不要用它记录普通聊天结论。",
                "硬性要求：调用前必须已经准备好完整参数；严禁 write_artifact({})，严禁先空调用工具再补参数。",
                "调用前自检：type 必须是工具 schema 允许的枚举值，title 必须是非空字符串，content 必须是对应类型的原始对象。",
                "project 产物不能用 write_artifact 创建；代码任务通过 fs_write / bash 写入 workspace 文件后由 AChat 自动生成 project。",
            ]
        )

    if "deploy_artifact" in tools:
        add(
            [
                "### deploy_artifact",
                "用途：web_app 产物完成后生成可打开的预览部署卡。",
                '正确流程：先 write_artifact 得到 artifactId="art_123"，再 deploy_artifact({ artifactId: "art_123" })。',
                "不要对 document/image/ppt 调用 deploy_artifact；它只接受 web_app。",
            ]
        )

    if "deploy_workspace" in tools:
        add(
            [
                "### deploy_workspace",
                "用途：把当前 workspace 内已有的静态输出目录部署成预览卡，例如 dist、build、out、client/dist。",
                "正确流程：先用 bash 运行项目构建命令，确认静态目录存在且包含 index.html，再 deploy_workspace({ path: \"dist\", title: \"前端构建预览\" })。",
            ]
        )

    if not is_plan_stage and (
        "fs_list" in tools or "fs_read" in tools or "fs_write" in tools or "bash" in tools
    ):
        add(
            [
                "### workspace 文件与命令工具",
                "用途：只操作当前 workspace 内的真实文件；路径必须在 <workspace_info><cwd> 下。",
                'fs_list 正确案例：fs_list({ path: "" }) 查看根目录；fs_list({ path: "src/server" }) 查看子目录。',
                'fs_read 正确案例：fs_read({ path: "src/app/page.tsx" })，先看现有代码再改。',
                'fs_write 正确案例：fs_write({ path: "src/app/page.tsx", content: "完整的新文件内容" })；content 是完整文件内容，不是 diff patch。',
                'bash 正确案例：bash({ command: "pnpm typecheck" })；子目录命令用 bash({ command: "pnpm build", cwd: "frontend", timeoutMs: 300000 })，不要写 cd frontend && pnpm build。',
            ]
        )

    if "plan_tasks" in tools:
        add(
            [
                "### plan_tasks",
                "用途：Orchestrator 用结构化计划拆分子任务；执行顺序只认 dependsOn 字段。",
                "字段名必须是 agentId、dependsOn、expectedOutputs、acceptanceCriteria、taskKind、targetPaths、expectedWorkspaceChanges、requiredCommands、requiredEvidence；不要写 snake_case。",
            ]
        )

    if "memory_recall" in tools:
        add(
            [
                "### memory_recall",
                "用途：检索长期记忆与用户偏好，自动在对话中积累和回忆信息。",
                "正确案例：当用户提到之前的偏好或历史上下文时，调用 memory_recall({ query: \"用户偏好\" }) 检索相关记忆。",
                "无需手动调用：记忆系统会在每次对话后自动存储，需要时检索即可。",
            ]
        )

    has_rag = any(t in tools for t in ("rag_search", "rag_ingest", "rag_list_documents", "rag_delete_document"))
    if has_rag:
        rag_lines = [
            "### RAG 知识库工具",
            "当前会话已启用 RAG 知识库检索，你可以使用以下工具操作知识库：",
        ]
        if "rag_search" in tools:
            rag_lines.append(
                '- rag_search({ query: "检索关键词" })：在知识库中检索相关文档片段，返回匹配的文本块和来源信息。'
            )
        if "rag_ingest" in tools:
            rag_lines.append(
                '- rag_ingest({ document: "文本内容", title: "文档标题" })：将新内容入库到知识库，供后续检索使用。'
            )
        if "rag_list_documents" in tools:
            rag_lines.append(
                '- rag_list_documents({})：列出知识库中已有的文档列表。'
            )
        if "rag_delete_document" in tools:
            rag_lines.append(
                '- rag_delete_document({ document_id: "doc_xxx" })：从知识库中删除指定文档。'
            )
        rag_lines.append(
            "使用建议：用户提问涉及已有知识库内容时，优先调用 rag_search 检索；用户要求保存信息时，用 rag_ingest 入库。"
        )
        add(rag_lines)

    return "\n\n".join(sections)


# ─── Misc helpers ────────────────────────────────────────────────────────────
def _extract_text_from_parts(parts: list[dict]) -> str:
    out: list[str] = []
    for p in parts:
        ptype = p.get("type")
        if ptype in ("text", "thinking"):
            out.append(p.get("content", ""))
        elif ptype == "code":
            out.append("```" + p.get("language", "") + "\n" + p.get("content", "") + "\n```")
        elif ptype == "image_attachment":
            out.append(
                f"[图片附件: {p['fileName']} ({_format_size(p['size'])}, "
                f"{p['mimeType']}) · id={p['attachmentId']}]"
            )
        elif ptype == "file_attachment":
            out.append(
                f"[文件附件: {p['fileName']} ({_format_size(p['size'])}, "
                f"{p['mimeType']}) · id={p['attachmentId']}]"
            )
    return "\n\n".join(s for s in out if s)


def _format_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes}B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f}KB"
    return f"{num_bytes / 1024 / 1024:.1f}MB"


def _ensure_includes(arr: list[str], v: str) -> list[str]:
    return arr if v in arr else [*arr, v]


# ─── Wire the real runner in (phase 5) ───────────────────────────────────────
runner_registry.set_agent_runner(AgentRunnerImpl())
