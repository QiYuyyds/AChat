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
import json
import logging
import time
from collections.abc import AsyncIterable, AsyncIterator, Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from sqlalchemy import and_, select, update

from app.adapters.base import AdapterAttachment, AdapterInput, CustomConfig
from app.adapters.registry import agent_registry
from app.config import get_settings
from app.db.engine import get_local_db
from app.db.models import Agent, AgentRun, Artifact, Conversation, Message, Workspace
from app.schemas.artifacts import ArtifactRecord
from app.schemas.events import (
    ArtifactCreateEvent,
    DeployStatusEvent,
    FileWritePreviewCompleteEvent,
    MessageEndEvent,
    MessageStartEvent,
    PlanCreatedEvent,
    PlanStepUpdateEvent,
    PartStartEvent,
    RunEndEvent,
    RunQueuedEvent,
    RunStartEvent,
    RunUsageEvent,
    StreamEvent,
    ToolResultEvent,
    TurnMetricEvent,
    TurnTokenBreakdown,
)
from app.schemas.messages import DeployStatusRecord, MessageUsage
from app.services import runner_registry
from app.services.attachment_service import get_attachment_absolute_path
from app.services.context_compaction_service import (
    AUTO_COMPACT_WATERMARK,
    CompactionSkipped,
    compact_conversation,
    count_uncompacted_messages,
    estimate_uncompacted_tokens,
    prefix_prompt_with_context_summary,
)
from app.services.conversation_context import BuildHistoryOptions, build_history_for
from app.services.event_bus import event_bus
from app.services.project_artifact import build_project_files
from app.services.runner_registry import RunHandle
from app.services.settings_service import get_app_settings, get_user_settings
from app.tools.base import ToolContext
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
from app.utils.platform import IS_WINDOWS
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


def _get_task_mem_buffer():
    """Retrieve the TaskMemBuffer from app.state, or None."""
    try:
        from app.main import _app_ref
        if _app_ref is None:
            return None
        return getattr(_app_ref.state, "task_mem_buffer", None)
    except Exception:
        return None


def _get_tool_state_tracker():
    """Retrieve the ToolStateTracker from app.state, or None."""
    try:
        from app.main import _app_ref
        if _app_ref is None:
            return None
        return getattr(_app_ref.state, "tool_state_tracker", None)
    except Exception:
        return None


def _get_hook_registry():
    """Retrieve the HookRegistry from app.state, or None."""
    try:
        from app.main import _app_ref
        if _app_ref is None:
            return None
        return getattr(_app_ref.state, "hook_registry", None)
    except Exception:
        return None


async def _push_tool_observation(
    call_id: str,
    tool_name: str,
    result: Any,
    is_error: bool,
) -> None:
    """Push StepObservation and ToolCallTrace after a tool execution.

    Best-effort: silently skips if buffers are not available.
    """
    buf = _get_task_mem_buffer()
    tracker = _get_tool_state_tracker()

    # Build summary string from result
    summary = ""
    if isinstance(result, str):
        summary = result
    elif isinstance(result, dict):
        summary = str(result.get("error") or result.get("value") or result)
    else:
        summary = str(result)

    if buf is not None:
        try:
            from app.services.prompt_assembler import StepObservation
            await buf.push(StepObservation(
                step_id=call_id,
                tool_name=tool_name,
                result=summary if not is_error else "",
                error=summary if is_error else "",
                success=not is_error,
            ))
        except Exception as e:
            logger.warning("TaskMemBuffer push failed: %s", e)

    if tracker is not None:
        try:
            from app.services.prompt_assembler import ToolCallTrace
            await tracker.record(ToolCallTrace(
                tool_name=tool_name,
                success=not is_error,
                summary=summary,
            ))
        except Exception as e:
            logger.warning("ToolStateTracker record failed: %s", e)


async def _post_run_memory_hook(
    prompt: str,
    result: RunExecutionResult,
    conversation_id: str,
    agent_id: str = "",
    user_id: str | None = None,
) -> None:
    """Background hook: write user prompt + agent output into memory subsystem.

    Runs as an asyncio.create_task so it never blocks the main run path.
    """
    ms = _get_memory_service()
    if ms is None:
        return
    try:
        await ms.on_message_end("user", prompt, conversation_id=conversation_id, user_id=user_id)
        # Collect agent output text from output_message_ids
        if result.output_message_ids:
            async with get_local_db() as db:
                from app.db.models import Message
                for msg_id in result.output_message_ids:
                    msg = (
                        await db.execute(select(Message).where(Message.id == msg_id))
                    ).scalar_one_or_none()
                    if msg:
                        parts = msg.parts_list
                        text_parts = [
                            p.get("content", "")
                            for p in parts
                            if p.get("type") == "text"
                        ]
                        agent_text = "\n".join(text_parts)

                        # Collect tool call failures so memory_writer can
                        # extract them as memories. Tool errors live in
                        # tool_result parts (isError=True), not in the
                        # assistant's text output — without this they
                        # are invisible to extract_ltm_memories.
                        import json as _json
                        tool_names: dict[str, str] = {}
                        for p in parts:
                            if p.get("type") == "tool_use":
                                cid = p.get("callId", "")
                                if cid:
                                    tool_names[cid] = p.get("toolName", "unknown")
                        tool_errors: list[str] = []
                        for p in parts:
                            if p.get("type") == "tool_result" and p.get("isError"):
                                cid = p.get("callId", "")
                                tool_name = tool_names.get(cid, "unknown")
                                err = p.get("result", "")
                                try:
                                    err = _json.dumps(err, ensure_ascii=False)
                                except (TypeError, ValueError):
                                    err = str(err)
                                tool_errors.append(f"{tool_name} 调用失败: {err}")
                        if tool_errors:
                            err_block = "\n".join(tool_errors)
                            agent_text = (
                                f"{agent_text}\n\n[工具执行错误]\n{err_block}"
                                if agent_text
                                else f"[工具执行错误]\n{err_block}"
                            )

                        if agent_text:
                            await ms.on_message_end("assistant", agent_text, agent_id, conversation_id=conversation_id, user_id=user_id)
    except Exception as e:
        logger.warning("_post_run_memory_hook error: %s", e)


async def _maybe_generate_summary_hook(
    conversation_id: str,
    agent_id: str,
    user_message: str,
    result: RunExecutionResult,
) -> None:
    """Background hook: generate conversation summary after first successful reply.

    Extracts the first agent reply text and calls maybe_generate_summary.
    Fails silently — summary generation is best-effort.
    """
    if not result.output_message_ids:
        logger.info("[summary_hook] Skipped: no output_message_ids")
        return
    try:
        from app.services.conversation_service import maybe_generate_summary

        async with get_local_db() as db:
            first_msg = (
                await db.execute(
                    select(Message).where(Message.id == result.output_message_ids[0])
                )
            ).scalar_one_or_none()
            if first_msg is None:
                logger.warning(
                    "[summary_hook] Message not found in DB: id=%s",
                    result.output_message_ids[0],
                )
                return
            text_parts = [
                p.get("content", "")
                for p in first_msg.parts_list
                if p.get("type") == "text"
            ]
            agent_reply = "\n".join(text_parts)
            if not agent_reply.strip():
                logger.info("[summary_hook] Skipped: empty agent_reply")
                return

        logger.info(
            "[summary_hook] Calling maybe_generate_summary for conv=%s agent=%s",
            conversation_id,
            agent_id,
        )
        await maybe_generate_summary(
            conversation_id=conversation_id,
            agent_id=agent_id,
            user_message=user_message,
            agent_reply=agent_reply,
        )
    except Exception as e:
        logger.warning("_maybe_generate_summary_hook error: %s", e)


async def _maybe_auto_compact_hook(
    conversation_id: str,
    override_prompt: str | None = None,
    agent_id: str | None = None,
) -> None:
    """Background hook: auto-compact conversation context when watermark reached.

    Triggers ``compact_conversation(silent=True)`` when either:
    - the uncompacted message count >= AUTO_COMPACT_WATERMARK (10), OR
    - the estimated token usage of uncompacted messages exceeds 87% of the
      model's context window (when agent model info is available).

    Skipped for sub-agent runs (``override_prompt`` non-empty) to avoid
    side-effects on the parent conversation's context.

    All exceptions (including ``CompactionSkipped``) are best-effort caught and
    logged as warnings — this hook MUST NOT affect the run's final status.
    """
    # Guard: sub-agent runs (override_prompt non-empty) are exempt
    if override_prompt:
        logger.info(
            "[auto-compact] skipped: override_prompt set (sub-agent run), conv=%s",
            conversation_id,
        )
        return

    try:
        watermark = await count_uncompacted_messages(conversation_id)
        logger.info(
            "[auto-compact] conv=%s watermark=%d threshold=%d",
            conversation_id,
            watermark,
            AUTO_COMPACT_WATERMARK,
        )
        if watermark >= AUTO_COMPACT_WATERMARK:
            result = await compact_conversation(conversation_id, silent=True)
            logger.info(
                "[auto-compact] conv=%s compacted=%d silent=True summary_id=%s "
                "ctx_before=%d ctx_after=%d",
                conversation_id,
                result.summary.source_message_count,
                result.summary.id,
                result.ctx_before,
                result.ctx_after,
            )
            return

        # O1: token-based trigger — compacts when estimated tokens > 87% of
        # the model's context window, even if the message count is low.
        if agent_id:
            model_limit = await _get_agent_model_limit(agent_id)
            if model_limit and model_limit > 0:
                token_threshold = int(model_limit * 0.87)
                estimated_tokens = await estimate_uncompacted_tokens(conversation_id)
                logger.info(
                    "[auto-compact] conv=%s estimated_tokens=%d token_threshold=%d "
                    "(87%% of %d)",
                    conversation_id,
                    estimated_tokens,
                    token_threshold,
                    model_limit,
                )
                if estimated_tokens > token_threshold:
                    result = await compact_conversation(conversation_id, silent=True)
                    logger.info(
                        "[auto-compact] conv=%s compacted (token trigger) "
                        "summary_id=%s ctx_before=%d ctx_after=%d",
                        conversation_id,
                        result.summary.id,
                        result.ctx_before,
                        result.ctx_after,
                    )
                    return
    except CompactionSkipped as skip:
        logger.info(
            "[auto-compact] conv=%s skipped: %s (silent)",
            conversation_id,
            skip.reason,
        )
    except Exception as e:
        logger.warning("[auto-compact] conv=%s error: %s", conversation_id, e)


async def _get_agent_model_limit(agent_id: str) -> int | None:
    """Look up the context window for the agent's configured model."""
    try:
        from app.infra.cache_helpers import get_agent_cached

        agent = await get_agent_cached(agent_id)
        if agent is None:
            return None
        limits = get_model_limits(agent.model_provider, agent.model_id)
        return limits.context_window
    except Exception as e:
        logger.warning("[auto-compact] failed to get model limit for agent %s: %s", agent_id, e)
        return None


# ─── IP geolocation for auto location detection ─────────────────────────────

_cached_location: str | None = None
"""Module-level cache for detected location. Persists for the process lifetime."""


async def _detect_location() -> str:
    """Best-effort IP geolocation to detect the user's city.

    Uses ip-api.com (free, no API key, 45 req/min limit). Results are cached
    at module level so we only call the API once per process.

    Returns the detected city name (in zh-CN when available), or "Unknown"
    if detection fails (offline, timeout, API error).
    """
    global _cached_location
    if _cached_location is not None:
        return _cached_location
    try:
        import httpx

        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(
                "http://ip-api.com/json/?lang=zh-CN&fields=city,status"
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") == "success":
                city = data.get("city", "")
                if city:
                    _cached_location = city
                    logger.info("[session] auto-detected location: %s", city)
                    return city
    except Exception as err:
        logger.debug("[session] location auto-detection failed: %s", err)
    _cached_location = "Unknown"
    return _cached_location


def _blunt_metadata(
    language: str, timezone: str, location: str, current_time: datetime
) -> tuple[str, str, str, str]:
    """Blunt session metadata into coarse-grained tags for prompt injection.

    - ``language`` → locale tag (e.g. ``zh-CN``)
    - ``timezone`` → offset tag (e.g. ``GMT+8``)
    - ``location`` → region/city tag (e.g. ``Beijing``)
    - ``current_time`` → ``{date}_{Weekday}_{Period}`` (e.g. ``2026年7月5日_Sunday_Morning``)

    Static metadata (language/timezone/location) goes into the system prompt
    (cache-stable prefix). The time_bucket (with date) goes into the user
    message tail (dynamic, changes daily).
    """
    weekday = current_time.strftime("%A")  # Monday..Sunday
    hour = current_time.hour
    if 5 <= hour <= 11:
        period = "Morning"
    elif 12 <= hour <= 17:
        period = "Afternoon"
    elif 18 <= hour <= 22:
        period = "Evening"
    else:
        period = "LateNight"
    date_str = f"{current_time.year}年{current_time.month}月{current_time.day}日"
    time_bucket = f"{date_str}_{weekday}_{period}"
    return (language, timezone, location, time_bucket)


# ─── Args / results (mirror the TS interfaces) ───────────────────────────────
@dataclass
class RunArgs:
    agent_id: str
    conversation_id: str
    trigger_message_id: str
    parent_run_id: str | None = None
    # sub-agent dispatch: external prompt assembled by the Orchestrator
    override_prompt: str | None = None
    # unified loop: override system prompt (solo/coordinated modes)
    override_system_prompt: str | None = None
    # override tool list (e.g. coordinated mode adds task_dispatch)
    override_tool_names: list[str] | None = None
    # parent run's cancel signal — cascade: parent abort -> child abort
    parent_cancel_event: asyncio.Event | None = None
    # resume: reuse existing run_id, load checkpoint, continue ReAct loop
    resume_from_checkpoint: bool = False
    # worktree isolation: when set, this path overrides the workspace effective
    # cwd so the child run (and its tools / CLI cwd) operate inside the worktree.
    override_workspace_path: str | None = None
    # universal subagent dispatch: recursion depth (0 = top-level)
    dispatch_depth: int = 0
    # universal subagent dispatch: "visible" (group-member) or "hidden" (clone-self)
    dispatch_visibility: str = "visible"
    # universal subagent dispatch: effective loop mode ("solo" / "coordinated" / "subagent")
    dispatch_mode: str = "solo"
    # multi-user: owning user for SSE event filtering and data isolation
    user_id: str | None = None


@dataclass
class RunResult:
    run_id: str
    status: str  # 'complete' | 'failed' | 'aborted'
    error: str | None = None
    artifact_ids: list[str] = field(default_factory=list)
    output_message_ids: list[str] = field(default_factory=list)
    output_artifacts: dict[str, str] = field(default_factory=dict)
    stop_reason: str | None = None
    stop_reason_label: str | None = None


@dataclass
class RunExecutionResult:
    artifact_ids: list[str] = field(default_factory=list)
    output_message_ids: list[str] = field(default_factory=list)
    output_artifacts: dict[str, str] = field(default_factory=dict)
    plan_stats: dict | None = None
    stop_reason: str | None = None
    stop_reason_label: str | None = None


def _empty_run_execution_result() -> RunExecutionResult:
    return RunExecutionResult(
        artifact_ids=[],
        output_message_ids=[],
        output_artifacts={},
        plan_stats=None,
        stop_reason=None,
        stop_reason_label=None,
    )


# ─── TurnResult (internal to the SDK ReAct loop) ───────────────────────────────
@dataclass
class ToolCallInfo:
    id: str
    name: str
    args: dict


@dataclass
class TurnResult:
    """Extracted from call_once events after consumption."""

    message_id: str
    text_content: str
    tool_calls: list[ToolCallInfo]
    finish_reason: str | None
    usage: MessageUsage | None
    assistant_message: dict  # written back to messages list (includes reasoning_content)


# ─── Adapter classification ─────────────────────────────────────────────────
# CLI agents use vendor CLI subprocesses with their own tool sets and auth.
CLI_ADAPTERS = frozenset({"claude-code", "codex"})
# SDK agents call LLM APIs via SDKs; AChat manages tools, auth, and history.
SDK_ADAPTERS = frozenset({"custom"})
# mock is neither CLI nor SDK; it is test-only and ignored by tool injection.

# Baseline tools always enabled for every SDK (custom) agent at runtime.
# These are NOT selectable in the UI — they are implicitly always-on and merged
# into the tool list by execute_simple_run. Must match _BASELINE_AGENT_TOOLS in
# app/api/agents.py (both are internal mirrors of the same design contract).
# CLI agents (claude-code / codex) use their own CLI built-in tools and skip
# this merge.
_BASELINE_AGENT_TOOLS: tuple[str, ...] = (
    "read_attachment",
    "ask_user",
    "fs_list",
    "fs_read",
    "fs_write",
    "fs_edit",
    "fs_grep",
    "fs_glob",
    "bash",
)

# Management tools are only injected into guide agents (is_guide=True).
# Non-guide agents are filtered even if tool_names mistakenly lists them.
_MANAGEMENT_TOOL_NAMES: frozenset[str] = frozenset({
    "manage_agents",
    "manage_skills",
    "manage_mcp",
    "manage_documents",
    "manage_memory",
    "manage_profile",
    "manage_conversations",
})

# Deprecated product default removed: Custom loop ends on model-done / budget /
# breakers. Absolute safety bound lives in react_loop_termination.SAFETY_MAX_MODEL_CALLS.
# Kept as alias for any external imports; do not use as a product max-steps cap.
REACT_LOOP_MAX_TURNS = None

# O2 Step 5: only read-only tools are cached within a single _run_react_loop call.
READONLY_CACHEABLE_TOOLS = frozenset({"fs_read", "read_artifact", "read_attachment"})

# Tools that indicate the agent is exploring/analyzing (not writing). Used by
# the plan-reminder injection: if the agent makes 3+ exploration calls across
# 2+ turns without creating a plan, inject a nudge to use create_plan.
_EXPLORATION_TOOLS = frozenset({
    "fs_read", "fs_list", "fs_glob", "fs_grep", "code_explore",
})

# Thresholds for plan-reminder injection.
_PLAN_REMINDER_MIN_TURNS = 2  # turns without a plan before reminding
_PLAN_REMINDER_MIN_EXPLORATION = 3  # exploration tool calls before reminding


# ─── Parallel tool execution helper ──────────────────────────────────────────
@dataclass
class _ToolCallExecResult:
    """Result of executing a single tool call (for parallel dispatch)."""

    events: list[StreamEvent]
    tool_message: dict
    extra_messages: list[dict]  # e.g. system hints from post_tool_use inject


async def _execute_tool_call_to_result(
    *,
    tc: ToolCallInfo,
    conversation_id: str,
    message_id: str,
    run_id: str,
    agent_id: str,
    cancel_event: asyncio.Event,
    tool_call_cache: dict[str, Any],
    mcp_manager: Any | None,
    ctx: ToolContext,
    hook_registry: Any | None,
    user_id: str | None = None,
) -> _ToolCallExecResult:
    """Execute a single tool call and return its events + messages.

    This is extracted from the _run_react_loop body so that multiple tool_calls
    can be dispatched in parallel via asyncio.gather.

    Handles: cache hits, MCP tools (with approval gate), and standard tools
    (with hooks). Returns a _ToolCallExecResult with all yieldable events and
    message-append dicts.
    """
    events: list[StreamEvent] = []
    extra_messages: list[dict] = []

    if cancel_event.is_set():
        value = {"error": "cancelled"}
        events.append(ToolResultEvent(
            conversation_id=conversation_id,
            timestamp=now_ms(),
            message_id=message_id,
            call_id=tc.id,
            result=value,
            is_error=True,
        ))
        return _ToolCallExecResult(
            events=events,
            tool_message={"role": "tool", "tool_call_id": tc.id, "content": json.dumps(value)},
            extra_messages=extra_messages,
        )

    # O2 Step 5: check read-only tool cache
    cache_key: str | None = None
    if tc.name in READONLY_CACHEABLE_TOOLS:
        cache_key = f"{tc.name}:{json.dumps(tc.args, sort_keys=True)}"
        if cache_key in tool_call_cache:
            value = f"[cached] {tool_call_cache[cache_key]}"
            events.append(ToolResultEvent(
                conversation_id=conversation_id,
                timestamp=now_ms(),
                message_id=message_id,
                call_id=tc.id,
                result=value,
                is_error=False,
            ))
            return _ToolCallExecResult(
                events=events,
                tool_message={"role": "tool", "tool_call_id": tc.id, "content": json.dumps(value)},
                extra_messages=extra_messages,
            )

    # ── MCP tool routing: mcp__ prefix → mcp_manager ──
    if mcp_manager is not None and tc.name.startswith("mcp__"):
        server_name = mcp_manager.get_server_name(tc.name)
        trust = mcp_manager.get_trust(server_name) if server_name else "ask"
        needs_approval = trust == "ask"
        if needs_approval:
            from app.services.pending_mcp_calls import pending_mcp_calls
            from app.utils.approval import await_pending_decision

            if pending_mcp_calls.is_rejected(conversation_id, tc.name):
                value = {"error": "User denied MCP tool call", "isError": True}
                events.append(ToolResultEvent(
                    conversation_id=conversation_id,
                    timestamp=now_ms(),
                    message_id=message_id,
                    call_id=tc.id,
                    result=value,
                    is_error=True,
                ))
                return _ToolCallExecResult(
                    events=events,
                    tool_message={"role": "tool", "tool_call_id": tc.id, "content": json.dumps(value)},
                    extra_messages=extra_messages,
                )

            if not pending_mcp_calls.is_approved(conversation_id, tc.name):
                pending = pending_mcp_calls.register(
                    conversation_id=conversation_id,
                    agent_id=agent_id,
                    run_id=run_id,
                    tool_name=tc.name,
                    args=tc.args,
                    server_trust=trust,
                    user_id=user_id,
                )
                decision = await await_pending_decision(
                    attach_resolver=lambda r, pid=pending.id: pending_mcp_calls.attach_resolver(pid, r),
                    cancel=lambda pid=pending.id: pending_mcp_calls.cancel(pid),
                    cancel_event=cancel_event,
                    cancelled_value={"approved": False},
                )
                approved = bool(decision.get("approved")) if isinstance(decision, dict) else False
                if not approved:
                    value = {"error": "User denied MCP tool call", "isError": True}
                    events.append(ToolResultEvent(
                        conversation_id=conversation_id,
                        timestamp=now_ms(),
                        message_id=message_id,
                        call_id=tc.id,
                        result=value,
                        is_error=True,
                    ))
                    return _ToolCallExecResult(
                        events=events,
                        tool_message={"role": "tool", "tool_call_id": tc.id, "content": json.dumps(value)},
                        extra_messages=extra_messages,
                    )

        try:
            value = await mcp_manager.call_tool(tc.name, tc.args)
            is_error = isinstance(value, dict) and value.get("isError", False)
        except Exception as mcp_err:  # noqa: BLE001 - surface to LLM
            logger.warning("[AgentRunner] MCP call_tool failed: %s", mcp_err)
            value = {"error": f"MCP tool call failed: {mcp_err}"}
            is_error = True

        events.append(ToolResultEvent(
            conversation_id=conversation_id,
            timestamp=now_ms(),
            message_id=message_id,
            call_id=tc.id,
            result=value,
            is_error=is_error,
        ))
        return _ToolCallExecResult(
            events=events,
            tool_message={"role": "tool", "tool_call_id": tc.id, "content": json.dumps(value)},
            extra_messages=extra_messages,
        )

    # ── Standard tool execution (with hooks) ──
    result = await tool_registry.execute_with_hooks(tc.name, tc.args, ctx, hook_registry)
    value = result.value if result.ok else {"error": result.error}

    # O2 Step 5: cache successful read-only tool results
    if cache_key is not None and result.ok:
        tool_call_cache[cache_key] = value

    events.append(ToolResultEvent(
        conversation_id=conversation_id,
        timestamp=now_ms(),
        message_id=message_id,
        call_id=tc.id,
        result=value,
        is_error=not result.ok,
    ))

    if tc.name == "write_artifact" and result.ok and _has_artifact_id(value):
        from app.adapters.custom_adapter import _load_artifact_event
        artifact_event = await _load_artifact_event(conversation_id, value["artifactId"])
        if artifact_event is not None:
            events.append(artifact_event)

    if (
        tc.name in ("deploy_artifact", "deploy_workspace")
        and result.ok
        and _is_deploy_status_record(value)
    ):
        events.append(DeployStatusEvent(
            conversation_id=conversation_id,
            timestamp=now_ms(),
            message_id=message_id,
            deployment=DeployStatusRecord.model_validate(value),
        ))

    # ── Plan tool event generation (symmetric to write_artifact → ArtifactCreateEvent) ──
    if tc.name == "create_plan" and result.ok and isinstance(value, dict) and "planId" in value:
        from app.schemas.plan import PlanStep as PlanStepModel
        events.append(PlanCreatedEvent(
            conversation_id=conversation_id,
            timestamp=now_ms(),
            planId=value["planId"],
            steps=[PlanStepModel.model_validate(s) for s in value.get("steps", [])],
            complexity=value.get("complexity", "moderate"),
        ))

    if tc.name in ("plan_step", "add_plan_steps") and result.ok and isinstance(value, dict) and "planId" in value:
        from app.schemas.plan import PlanStep as PlanStepModel
        events.append(PlanStepUpdateEvent(
            conversation_id=conversation_id,
            timestamp=now_ms(),
            planId=value["planId"],
            steps=[PlanStepModel.model_validate(s) for s in value.get("updatedSteps", [])],
        ))

    # fs_write / fs_edit: append FileWritePreviewCompleteEvent for frontend preview updates
    if tc.name in ("fs_write", "fs_edit"):
        if result.ok and isinstance(value, dict):
            events.append(FileWritePreviewCompleteEvent(
                conversation_id=conversation_id,
                timestamp=now_ms(),
                message_id=message_id,
                call_id=tc.id,
                path=value.get("path", ""),
                oldContent=value.get("oldContent"),
                newContent=value.get("newContent"),
                status="complete",
            ))
        else:
            events.append(FileWritePreviewCompleteEvent(
                conversation_id=conversation_id,
                timestamp=now_ms(),
                message_id=message_id,
                call_id=tc.id,
                path="",
                oldContent=None,
                newContent=None,
                status="failed",
            ))

    # O8: check post_tool_use inject action (e.g. skill_auto_activator)
    post_result = ctx.last_post_hook_result
    if post_result and post_result.action == "inject" and post_result.data:
        for item in post_result.data:
            if isinstance(item, dict) and item.get("type") == "system_hint":
                extra_messages.append({
                    "role": "system",
                    "content": item.get("content", ""),
                })

    return _ToolCallExecResult(
        events=events,
        tool_message={"role": "tool", "tool_call_id": tc.id, "content": json.dumps(value)},
        extra_messages=extra_messages,
    )


# ─── SDK ReAct loop (Phase 1: call_once + TurnResult) ─────────────────────────
def _mid_run_compact(messages: list[dict]) -> list[dict]:
    """Structurally compress messages list mid-run without calling an LLM.

    Adapts prune_old_tool_results + fold_old_messages logic for the dict-based
    messages used in _run_react_loop. No LLM summarization (latency constraint).
    """
    # 1. Prune large old tool results (keep last 6 messages intact)
    recent_keep = 6
    if len(messages) > recent_keep:
        for msg in messages[:-recent_keep]:
            if msg.get("role") == "tool":
                content = msg.get("content", "")
                if isinstance(content, str) and estimate_tokens(content) > 2000:
                    msg["content"] = "[tool_result 已裁剪（mid-run compact）]"

    # 2. Fold old messages when count exceeds threshold
    fold_threshold = 20
    keep_recent = 15
    if len(messages) > fold_threshold:
        first = messages[0] if messages[0].get("role") == "system" else None
        recent = messages[-keep_recent:]
        old_start = 1 if first else 0
        old_count = len(messages) - keep_recent - old_start
        if old_count > 0:
            fold_marker = {
                "role": "system",
                "content": f"[已折叠 {old_count} 条消息（mid-run compact）]",
            }
            messages = [first, fold_marker, *recent] if first else [fold_marker, *recent]

    return messages


# ─── SDK ReAct loop (Phase 1: call_once + TurnResult) ─────────────────────────
async def _run_react_loop(  # noqa: C901
    adapter: Any,
    adapter_input: AdapterInput,
    cancel_event: asyncio.Event,
    run_id: str,
    agent_id: str,
    conversation_id: str,
    model_id: str | None,
    model_provider: str | None = None,
    resume_from_turn: int | None = None,
    mcp_manager: Any | None = None,
    dispatch_depth: int = 0,
    dispatch_mode: str = "solo",
    user_id: str | None = None,
) -> AsyncIterator[StreamEvent]:
    """ReAct loop for SDK adapters: call_once → yield events → execute tools → repeat.

    Termination is model-done primary (0 tool calls), with a unified budget
    compact → soft → forced → hard pipeline and behavioral circuit breakers.
    See ``react_loop_termination``.
    """
    from app.adapters.custom_adapter import _RunUsage, _to_run_usage
    from app.config import get_settings
    from app.services.compact_markers import CompactSuccessJudge
    from app.services.compact_pipeline import estimate_messages_tokens, run_compact_pipeline
    from app.services.hook_registry import HookContext, HookEvent
    from app.services.react_loop_termination import (
        SAFETY_MAX_MODEL_CALLS,
        StopReason,
        TerminationState,
        build_forced_messages,
        decide_pre_model,
        mark_compact_result,
        stable_tool_fingerprint,
        stop_reason_label,
    )

    hook_registry = _get_hook_registry()

    # Initialize messages: system + history + user
    image_attachments = adapter_input.attachments or []
    if adapter_input.messages is not None:
        messages: list[dict] = list(adapter_input.messages)
    else:
        from app.adapters.custom_adapter import _build_multimodal_user_content
        supports_vision = (
            adapter_input.custom_config.supports_vision
            if adapter_input.custom_config
            else False
        )
        imgs = [a for a in image_attachments if a.kind == "image"]
        use_multimodal = bool(supports_vision) and len(imgs) > 0
        user_content: object = (
            _build_multimodal_user_content(adapter_input.prompt, imgs)
            if use_multimodal
            else adapter_input.prompt
        )
        messages = [
            {"role": "system", "content": adapter_input.system_prompt},
            *(adapter_input.history or []),
            {"role": "user", "content": user_content},
        ]

    full_tool_names = list(adapter_input.tool_names or [])

    ctx = ToolContext(
        conversation_id=conversation_id,
        workspace_path=adapter_input.workspace_path,
        agent_id=agent_id,
        run_id=run_id,
        cancel_event=cancel_event,
        hook_registry=hook_registry,
        tool_names=full_tool_names,
        dispatch_depth=dispatch_depth,
        dispatch_mode=dispatch_mode,
        user_id=user_id,
    )

    tool_call_cache: dict[str, Any] = {}

    model_limit = 0
    if model_id:
        try:
            model_limit = get_model_limits(model_provider, model_id).context_window
        except Exception:
            model_limit = 0

    settings = get_settings()
    raw_fuse = getattr(settings, "max_tool_turns", None)
    max_tool_turns = raw_fuse if isinstance(raw_fuse, int) and raw_fuse > 0 else None
    term = TerminationState(max_tool_turns=max_tool_turns)

    # Read the compaction pipeline toggle once (avoid per-turn config reads).
    compact_pipeline_enabled = bool(getattr(settings, "compact_pipeline_enabled", True))

    # ── on_run_start hook ──
    if hook_registry and hook_registry.has_handlers(HookEvent.ON_RUN_START):
        run_start_result = await hook_registry.dispatch(HookContext(
            event=HookEvent.ON_RUN_START,
            run_id=run_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            user_id=user_id,
            messages=messages,
            tool_names=full_tool_names,
        ))
        if run_start_result.action == "inject" and run_start_result.data:
            for item in run_start_result.data:
                if isinstance(item, dict) and item.get("type") == "system_hint":
                    messages.append({
                        "role": "system",
                        "content": item.get("content", ""),
                    })

    run_usage = _RunUsage()
    start_turn = resume_from_turn or 0
    turn = start_turn

    def _emit_run_usage(reason: StopReason) -> RunUsageEvent:
        term.final_stop_reason = reason
        return RunUsageEvent(
            conversation_id=conversation_id,
            timestamp=now_ms(),
            run_id=run_id,
            usage=_to_run_usage(run_usage, model_id or "", turn_count=term.model_call_count),
            stop_reason=reason.value,
            stop_reason_label=stop_reason_label(reason),
        )

    # Plan-reminder tracking: detect when the agent is doing multi-turn
    # exploration without creating a plan, and inject a nudge. This mirrors
    # Claude Code's TodoWrite reminder — "提示词压力" to drive structured
    # planning rather than ad-huc exploration.
    has_created_plan = False
    turns_without_plan = 0
    exploration_tool_count = 0
    plan_reminder_injected = False

    try:
        while term.model_call_count < SAFETY_MAX_MODEL_CALLS:
            if cancel_event.is_set():
                yield _emit_run_usage(StopReason.CANCELLED)
                break

            turn_start = time.monotonic()
            if model_limit > 0:
                if compact_pipeline_enabled:
                    total_tokens = estimate_messages_tokens(messages)
                else:
                    total_tokens = estimate_tokens(json.dumps(messages, ensure_ascii=False))
            else:
                total_tokens = 0
            decision = decide_pre_model(
                state=term,
                total_tokens=total_tokens,
                model_limit=model_limit,
                pipeline_enabled=compact_pipeline_enabled,
            )

            if decision.action == "hard_stop":
                logger.warning(
                    "[AgentRunner] hard stop: reason=%s tokens=%d limit=%d",
                    decision.stop_reason, total_tokens, model_limit,
                )
                yield _emit_run_usage(decision.stop_reason or StopReason.BUDGET_EXHAUSTED)
                break

            # ── Compaction (stages 1/2/3 pipeline OR legacy single-point) ──
            if decision.action in ("summarize", "prune", "fold", "compact"):
                pre_compact_count = len(messages)
                pre_tokens = total_tokens
                _stage_map = {"summarize": 1, "prune": 2, "fold": 3}
                try:
                    if compact_pipeline_enabled and decision.action in _stage_map:
                        stage = _stage_map[decision.action]
                        messages = run_compact_pipeline(messages, stage=stage)
                        logger.info(
                            "[AgentRunner] compact stage %d (%s): %d -> %d messages",
                            stage, decision.action, pre_compact_count, len(messages),
                        )
                    else:
                        # Legacy single-point compact path (pipeline disabled)
                        messages = _mid_run_compact(messages)
                        logger.info(
                            "[AgentRunner] legacy mid-run compact: %d -> %d messages",
                            pre_compact_count, len(messages),
                        )
                    # Recompute tokens with the same estimator used pre-compact.
                    if compact_pipeline_enabled:
                        post_tokens = estimate_messages_tokens(messages)
                    else:
                        post_tokens = estimate_tokens(json.dumps(messages, ensure_ascii=False))
                    # Strict success: token must drop ≥15% (not just len change).
                    success = CompactSuccessJudge.judge(
                        pre_tokens, post_tokens, pre_compact_count, len(messages),
                    )
                    mark_compact_result(term, success=success)
                    logger.info(
                        "[AgentRunner] compact result: success=%s %d -> %d tokens",
                        success, pre_tokens, post_tokens,
                    )
                except Exception as compact_err:  # noqa: BLE001
                    logger.warning("[AgentRunner] compact failed: %s", compact_err)
                    mark_compact_result(term, success=False)
                # Re-evaluate after compact on same iteration
                continue

            if decision.action == "soft_inject" and decision.inject_message:
                # Model-visible, user-hidden: system role in messages only (no SSE bubble)
                messages.append({"role": "system", "content": decision.inject_message})
                logger.info(
                    "[AgentRunner] soft wrap-up inject: reason=%s",
                    decision.pending_reason,
                )

            force_final = decision.action == "force_final"
            if force_final:
                for msg in build_forced_messages(term):
                    messages.append(msg)
                adapter_input.tool_names = []
                # Also clear MCP tools so forced final has no tools
                saved_mcp = adapter_input.mcp_tools
                adapter_input.mcp_tools = None
                term.forced_done = True
                logger.info(
                    "[AgentRunner] forced final call: reason=%s",
                    decision.pending_reason,
                )
            else:
                adapter_input.tool_names = full_tool_names
                saved_mcp = None

            adapter_input.messages = messages
            term.model_call_count += 1
            turn += 1

            # ── pre_turn hook ──
            if hook_registry and hook_registry.has_handlers(HookEvent.PRE_TURN):
                await hook_registry.dispatch(HookContext(
                    event=HookEvent.PRE_TURN,
                    run_id=run_id,
                    agent_id=agent_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    turn_number=turn,
                ))

            text_content = ""
            reasoning_content = ""
            tool_calls: list[ToolCallInfo] = []
            pre_resolved: set[str] = set()
            pre_resolved_messages: list[dict] = []
            message_id: str | None = None
            deferred_events: list[StreamEvent] = []
            turn_input_tokens = 0
            turn_output_tokens = 0
            turn_cache_read_tokens = 0

            try:
                async for event in adapter.call_once(adapter_input, cancel_event):
                    if event.type == "message.start":
                        message_id = event.message_id
                    elif event.type == "part.delta":
                        dtype = event.delta.get("type")
                        text = event.delta.get("text", "")
                        if dtype == "text.append":
                            text_content += text
                        elif dtype == "thinking.append":
                            reasoning_content += text
                    elif event.type == "tool.call":
                        if force_final:
                            # Ignore tool calls on forced final
                            continue
                        tool_calls.append(ToolCallInfo(
                            id=event.call_id,
                            name=event.tool_name,
                            args=event.args if isinstance(event.args, dict) else {},
                        ))
                    elif event.type == "tool.result":
                        pre_resolved.add(event.call_id)
                        pre_resolved_messages.append({
                            "role": "tool",
                            "tool_call_id": event.call_id,
                            "content": json.dumps(event.result),
                        })
                    elif event.type == "message.usage":
                        run_usage.input_tokens += event.usage.input_tokens
                        run_usage.output_tokens += event.usage.output_tokens
                        run_usage.cache_read_tokens += event.usage.cache_read_tokens
                        run_usage.last_input_tokens = event.usage.input_tokens
                        run_usage.last_cache_read_tokens = event.usage.cache_read_tokens
                        run_usage.last_output_tokens = event.usage.output_tokens
                        turn_input_tokens += event.usage.input_tokens
                        turn_output_tokens += event.usage.output_tokens
                        turn_cache_read_tokens += event.usage.cache_read_tokens
                        deferred_events.append(event)
                        continue
                    elif event.type == "message.end":
                        deferred_events.append(event)
                        continue

                    yield event
            except Exception:
                logger.exception("[AgentRunner] _run_react_loop call_once error")
                if force_final and saved_mcp is not None:
                    adapter_input.mcp_tools = saved_mcp
                yield _emit_run_usage(StopReason.BUDGET_EXHAUSTED)
                return

            if force_final and saved_mcp is not None:
                adapter_input.mcp_tools = saved_mcp
            adapter_input.tool_names = full_tool_names

            if message_id is None:
                yield _emit_run_usage(
                    term.forced_trigger_reason or StopReason.BUDGET_EXHAUSTED
                    if force_final else StopReason.COMPLETE
                )
                return

            assistant_msg: dict = {"role": "assistant", "content": text_content or None}
            if tool_calls:
                assistant_msg["tool_calls"] = [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.name, "arguments": json.dumps(tc.args)}}
                    for tc in tool_calls
                ]
            if reasoning_content:
                assistant_msg["reasoning_content"] = reasoning_content
            messages.append(assistant_msg)
            messages.extend(pre_resolved_messages)

            # No tool calls → natural or soft complete
            if len(tool_calls) == 0:
                for ev in deferred_events:
                    yield ev
                yield TurnMetricEvent(
                    conversation_id=conversation_id,
                    timestamp=now_ms(),
                    run_id=run_id,
                    turn=turn,
                    tokens=TurnTokenBreakdown(
                        input_tokens=turn_input_tokens,
                        output_tokens=turn_output_tokens,
                        cache_read_tokens=turn_cache_read_tokens,
                    ),
                    tool_calls=[],
                    duration_ms=int((time.monotonic() - turn_start) * 1000),
                )
                if hook_registry and hook_registry.has_handlers(HookEvent.POST_TURN):
                    await hook_registry.dispatch(HookContext(
                        event=HookEvent.POST_TURN,
                        run_id=run_id,
                        agent_id=agent_id,
                        conversation_id=conversation_id,
                        user_id=user_id,
                        turn_number=turn,
                        message_id=message_id,
                        tool_calls=[],
                        finish_reason="stop",
                        messages=messages,
                    ))
                if hook_registry and hook_registry.has_handlers(HookEvent.ON_STOP):
                    await hook_registry.dispatch(HookContext(
                        event=HookEvent.ON_STOP,
                        run_id=run_id,
                        agent_id=agent_id,
                        conversation_id=conversation_id,
                        user_id=user_id,
                        turn_number=turn,
                    ))
                if force_final:
                    reason = term.forced_trigger_reason or StopReason.BUDGET_FORCED_FINAL
                elif term.soft_done and term.soft_trigger_reason:
                    # Soft injected then model finished with 0 tools
                    reason = (
                        term.soft_trigger_reason
                        if term.soft_trigger_reason != StopReason.BUDGET_SOFT_COMPLETE
                        else StopReason.BUDGET_SOFT_COMPLETE
                    )
                    if term.soft_trigger_reason in (
                        StopReason.DUPLICATE_TOOL_BREAKER,
                        StopReason.TOOL_ERROR_BREAKER,
                        StopReason.COMPACT_FAILURE_BREAKER,
                        StopReason.MAX_TOOL_TURNS,
                    ):
                        # Soft-only recovery for breakers/fuse counts as that reason
                        reason = term.soft_trigger_reason
                else:
                    reason = StopReason.COMPLETE
                yield _emit_run_usage(reason)
                break

            # Forced final ignored tool_calls above; if somehow still here, stop.
            if force_final:
                for ev in deferred_events:
                    yield ev
                yield _emit_run_usage(
                    term.forced_trigger_reason or StopReason.BUDGET_FORCED_FINAL
                )
                break

            # Soft already done and model still emits tools → force next iteration
            if term.soft_done and not term.forced_done:
                term.force_after_soft = True
                if term.soft_trigger_reason == StopReason.DUPLICATE_TOOL_BREAKER:
                    term.force_after_duplicate = True
                elif term.soft_trigger_reason == StopReason.TOOL_ERROR_BREAKER:
                    term.force_after_tool_error = True

            # ── Execute tools ──
            executable = [tc for tc in tool_calls if tc.id not in pre_resolved]
            exec_names: list[str] = []
            exec_fps: list[str] = []
            exec_errors: list[bool] = []

            if not cancel_event.is_set() and executable:
                tasks = [
                    asyncio.create_task(
                        _execute_tool_call_to_result(
                            tc=tc,
                            conversation_id=conversation_id,
                            message_id=message_id,
                            run_id=run_id,
                            agent_id=agent_id,
                            cancel_event=cancel_event,
                            tool_call_cache=tool_call_cache,
                            mcp_manager=mcp_manager,
                            ctx=replace(ctx) if len(executable) > 1 else ctx,
                            hook_registry=hook_registry,
                            user_id=user_id,
                        )
                    )
                    for tc in executable
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)

                for i, tc in enumerate(executable):
                    if i >= len(results):
                        break
                    res = results[i]
                    fp = stable_tool_fingerprint(tc.name, tc.args)
                    exec_names.append(tc.name)
                    exec_fps.append(fp)

                    if isinstance(res, Exception):
                        value = {"error": f"Tool execution error: {res}"}
                        exec_errors.append(True)
                        yield ToolResultEvent(
                            conversation_id=conversation_id,
                            timestamp=now_ms(),
                            message_id=message_id,
                            call_id=tc.id,
                            result=value,
                            is_error=True,
                        )
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": json.dumps(value),
                        })
                        continue

                    is_err = any(
                        getattr(ev, "type", None) == "tool.result" and getattr(ev, "is_error", False)
                        for ev in res.events
                    )
                    exec_errors.append(is_err)
                    for ev in res.events:
                        yield ev
                    messages.append(res.tool_message)
                    for extra_msg in res.extra_messages:
                        messages.append(extra_msg)

            term.record_tool_calls(exec_names, exec_fps, exec_errors)

            for ev in deferred_events:
                yield ev
            yield TurnMetricEvent(
                conversation_id=conversation_id,
                timestamp=now_ms(),
                run_id=run_id,
                turn=turn,
                tokens=TurnTokenBreakdown(
                    input_tokens=turn_input_tokens,
                    output_tokens=turn_output_tokens,
                    cache_read_tokens=turn_cache_read_tokens,
                ),
                tool_calls=[tc.name for tc in tool_calls],
                duration_ms=int((time.monotonic() - turn_start) * 1000),
            )
            if hook_registry and hook_registry.has_handlers(HookEvent.POST_TURN):
                await hook_registry.dispatch(HookContext(
                    event=HookEvent.POST_TURN,
                    run_id=run_id,
                    agent_id=agent_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    turn_number=turn,
                    message_id=message_id,
                    tool_calls=[{"id": tc.id, "name": tc.name, "args": tc.args} for tc in tool_calls],
                    finish_reason=None,
                    messages=messages,
                ))

            # ── Plan-reminder injection (Just-in-time Prompting) ──
            # Track tool call patterns and nudge the agent to create a plan
            # when it's doing multi-turn exploration without one.
            for tc in tool_calls:
                if tc.name == "create_plan":
                    has_created_plan = True
                    turns_without_plan = 0
                elif tc.name in _EXPLORATION_TOOLS:
                    exploration_tool_count += 1

            if not has_created_plan:
                turns_without_plan += 1
                if (
                    not plan_reminder_injected
                    and turns_without_plan >= _PLAN_REMINDER_MIN_TURNS
                    and exploration_tool_count >= _PLAN_REMINDER_MIN_EXPLORATION
                ):
                    messages.append({
                        "role": "system",
                        "content": (
                            "你已进行了多轮文件探索操作，但尚未创建执行计划。"
                            "如果这是一个需要多步骤的复杂任务（如分析项目、理解代码库、生成文档），"
                            "建议先调用 create_plan 创建执行计划，让用户看到你的工作安排和进度。"
                            "如果这是一个简单任务（1-2 步就能完成），可以忽略此提醒。"
                        ),
                    })
                    plan_reminder_injected = True
                    logger.info(
                        "[AgentRunner] plan-reminder injected: run=%s turn=%d "
                        "exploration_calls=%d turns_without_plan=%d",
                        run_id, turn, exploration_tool_count, turns_without_plan,
                    )
        else:
            yield _emit_run_usage(StopReason.BUDGET_EXHAUSTED)
    finally:
        if hook_registry and hook_registry.has_handlers(HookEvent.ON_RUN_END):
            await hook_registry.dispatch(HookContext(
                event=HookEvent.ON_RUN_END,
                run_id=run_id,
                agent_id=agent_id,
                conversation_id=conversation_id,
                user_id=user_id,
            ))


def _has_artifact_id(value: object) -> bool:
    return isinstance(value, dict) and isinstance(value.get("artifactId"), str)


def _is_deploy_status_record(value: object) -> bool:
    created_at = value.get("createdAt") if isinstance(value, dict) else None
    return (
        isinstance(value, dict)
        and isinstance(value.get("id"), str)
        and isinstance(value.get("artifactId"), str)
        and isinstance(value.get("previewPath"), str)
        and value.get("status") in ("ready", "failed")
        and isinstance(created_at, (int, float))
        and not isinstance(created_at, bool)
    )

# ─── Constants (port of agent-runner.ts:212) ─────────────────────────────────
SUB_AGENT_CONTEXT_RECENT_LIMIT = 5
MAX_CONCURRENT_SUB_AGENT_RUNS = 4
ASK_USER_TOOL_NAME = "ask_user"


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


# ─── Queued runs (per-conversation FIFO queue) ──────────────────────────────
@dataclass
class _QueuedRunSpec:
    """Parameters for a run waiting in the per-conversation queue."""

    run_id: str
    agent_id: str
    conversation_id: str
    trigger_message_id: str
    user_id: str | None


_queued_runs: dict[str, list[_QueuedRunSpec]] = {}


def enqueue_run(
    *,
    agent_id: str,
    conversation_id: str,
    trigger_message_id: str,
    user_id: str | None = None,
) -> str:
    """Create a queued run: AgentRun row with status='queued' + RunQueuedEvent.

    The run will be started automatically when all active runs for the same
    conversation finish (see :func:`_drain_queued_runs`).
    """
    run_id = new_run_id()
    now = now_ms()

    async def _create_and_publish() -> None:
        async with get_local_db() as db:
            db.add(
                AgentRun(
                    id=run_id,
                    conversation_id=conversation_id,
                    agent_id=agent_id,
                    trigger_message_id=trigger_message_id,
                    status="queued",
                    parent_run_id=None,
                    started_at=now,
                )
            )
        publish(
            RunQueuedEvent(
                conversation_id=conversation_id,
                timestamp=now,
                run_id=run_id,
                agent_id=agent_id,
                trigger_message_id=trigger_message_id,
            ),
            user_id=user_id,
        )

    # Fire-and-forget: the DB insert and event publish happen asynchronously.
    # The caller returns run_id immediately so the API response includes it.
    asyncio.ensure_future(_create_and_publish())
    _queued_runs.setdefault(conversation_id, []).append(
        _QueuedRunSpec(
            run_id=run_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            trigger_message_id=trigger_message_id,
            user_id=user_id,
        )
    )
    return run_id


def cancel_queued_run(run_id: str) -> bool:
    """Remove a queued run from the queue and mark it as aborted in DB.

    Returns True if the run was found and cancelled, False otherwise.
    """
    for conv_id, queue in _queued_runs.items():
        for i, spec in enumerate(queue):
            if spec.run_id == run_id:
                queue.pop(i)
                if not queue:
                    _queued_runs.pop(conv_id, None)
                # Mark as aborted in DB + emit RunEndEvent
                async def _cancel() -> None:
                    now = now_ms()
                    try:
                        async with get_local_db() as db:
                            run = (
                                await db.execute(
                                    select(AgentRun).where(AgentRun.id == run_id)
                                )
                            ).scalar_one_or_none()
                            if run is not None and run.status == "queued":
                                run.status = "aborted"
                                run.finished_at = now
                    except RuntimeError:
                        pass
                    publish(
                        RunEndEvent(
                            conversation_id=spec.conversation_id,
                            timestamp=now,
                            run_id=run_id,
                            status="aborted",
                            error=None,
                        ),
                        user_id=spec.user_id,
                    )

                asyncio.ensure_future(_cancel())
                return True
    return False


def has_queued_runs(conversation_id: str) -> bool:
    """Check if a conversation has any queued runs."""
    return bool(_queued_runs.get(conversation_id))


def _start_queued_run(spec: _QueuedRunSpec) -> None:
    """Start a queued run: update DB row to 'running' and spawn execute_run."""
    run_id = spec.run_id
    cancel_event = asyncio.Event()
    args = RunArgs(
        agent_id=spec.agent_id,
        conversation_id=spec.conversation_id,
        trigger_message_id=spec.trigger_message_id,
        user_id=spec.user_id,
    )
    task = asyncio.create_task(execute_run(run_id, cancel_event, args))
    _active_runs[run_id] = (task, cancel_event)
    task.add_done_callback(lambda _t: _active_runs.pop(run_id, None))
    task.add_done_callback(_log_uncaught)


def _drain_queued_runs(conversation_id: str) -> None:
    """Start the next queued run for a conversation (FIFO order)."""
    queue = _queued_runs.get(conversation_id)
    if not queue:
        return
    spec = queue.pop(0)
    if not queue:
        _queued_runs.pop(conversation_id, None)
    _start_queued_run(spec)


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
        user_id: str | None = None,
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
            user_id=user_id,
        )
        task = asyncio.create_task(execute_run(run_id, cancel_event, args))
        _active_runs[run_id] = (task, cancel_event)
        task.add_done_callback(lambda _t: _active_runs.pop(run_id, None))
        task.add_done_callback(_log_uncaught)
        return RunHandle(run_id=run_id)

    def abort(self, run_id: str) -> bool:
        if cancel_queued_run(run_id):
            return True
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

    watcher: asyncio.Task[None] | None = None
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
    if watcher is not None:
        task.add_done_callback(lambda _t: watcher.cancel())
    return run_id, task, cancel_event


# ─── Main entry ──────────────────────────────────────────────────────────────
async def execute_run(
    run_id: str, cancel_event: asyncio.Event, args: RunArgs
) -> RunResult:
    """Load prerequisites, dispatch to simple/orchestrator, always finalize."""
    from app.infra.cache_helpers import get_agent_cached, get_workspace_cached

    agent = await get_agent_cached(args.agent_id)
    if not agent:
        return await finalize_failed(run_id, args, f"Agent not found: {args.agent_id}")

    workspace = await get_workspace_cached(args.conversation_id)
    if not workspace:
        return await finalize_failed(
            run_id, args, f"Workspace not found for conversation: {args.conversation_id}"
    )

    async with get_local_db() as db:
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

        # Load conversation to determine dispatch_mode
        conv = (
            await db.execute(
                select(Conversation).where(Conversation.id == args.conversation_id)
            )
        ).scalar_one_or_none()

        # Resolve user_id from conversation for SSE event filtering.
        # Subagent runs inherit user_id from parent via args; top-level runs
        # resolve it from the conversation record.
        if args.user_id is None and conv is not None:
            args = replace(args, user_id=conv.user_id)

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

    await _insert_run_or_resume(run_id, args, args.agent_id)
    publish(
        RunStartEvent(
            conversation_id=args.conversation_id,
            timestamp=now_ms(),
            run_id=run_id,
            agent_id=args.agent_id,
            trigger_message_id=args.trigger_message_id,
            parent_run_id=args.parent_run_id,
            is_resume=args.resume_from_checkpoint,
        ),
        user_id=args.user_id,
    )

    from app.observability import start_span
    with start_span(
        "agent.run",
        agent_id=args.agent_id,
        run_id=run_id,
        conversation_id=args.conversation_id,
        dispatch_mode=args.dispatch_mode,
    ) as root_span:
        try:
            from app.services.agent_loop import get_dispatch_mode, run_agent_loop

            # Subagent runs (override_prompt set) use subagent mode.
            # Top-level runs branch on conversation.dispatch_mode.
            if args.override_prompt:
                subagent_args = replace(args, dispatch_mode="subagent")
                result = await run_agent_loop(
                    run_id, cancel_event, subagent_args, prompt, attachments,
                    mode="subagent",
                )
            else:
                dispatch_mode = get_dispatch_mode(conv)
                if dispatch_mode == "coordinated" and is_orchestrator:
                    coord_args = replace(args, dispatch_mode="coordinated")
                    result = await run_agent_loop(
                        run_id, cancel_event, coord_args, prompt, attachments,
                        mode="coordinated",
                    )
                else:
                    result = await run_agent_loop(
                        run_id, cancel_event, args, prompt, attachments,
                        mode="solo",
                    )
            if cancel_event.is_set():
                return await finalize(run_id, args, "aborted", result)
            final_result = await finalize_ok(run_id, args, result)
            # ─── Post-run memory hook (Task 5.4) ───
            asyncio.create_task(
                _post_run_memory_hook(prompt, result, args.conversation_id, args.agent_id, user_id=args.user_id)
            )
            # ─── Summary generation hook (first reply only) ───
            asyncio.create_task(
                _maybe_generate_summary_hook(
                    args.conversation_id, args.agent_id, prompt, result
                )
            )
            # ─── Auto-compact hook (watermark + token based silent compaction) ───
            asyncio.create_task(
                _maybe_auto_compact_hook(
                    args.conversation_id, args.override_prompt, args.agent_id
                )
            )
            # ─── Online rule evaluation hook ───
            asyncio.create_task(
                _run_online_eval_hook(run_id)
            )
            return final_result
        except asyncio.CancelledError:
            return await finalize(run_id, args, "aborted", _empty_run_execution_result())
        except Exception as err:  # noqa: BLE001 - faithful catch-all; surfaced via finalize
            logger.exception("[AgentRunner] run failed: %s", err)
            if cancel_event.is_set():
                return await finalize(run_id, args, "aborted", _empty_run_execution_result())
            return await finalize(
                run_id, args, "failed", _empty_run_execution_result(), str(err)
            )


# ─── Simple agent ────────────────────────────────────────────────────────────


async def _resolve_mcp_configs(agent: Agent) -> list[Any]:
    """Resolve MCP server configs for an agent from the database.

    Returns a list of McpServerConfig objects for enabled MCP servers
    referenced by the agent's mcp_server_ids. Returns empty list for
    non-SDK agents or agents with no MCP servers.
    """
    if agent.adapter_name not in SDK_ADAPTERS:
        return []
    server_ids = agent.mcp_server_ids_list
    if not server_ids:
        return []
    from app.db.models import McpServer
    from app.mcp.client_manager import build_mcp_server_configs_from_db

    async with get_local_db() as db:
        result = await db.execute(
            select(McpServer).where(
                McpServer.id.in_(server_ids),
                McpServer.enabled == True,  # noqa: E712 - SQLAlchemy filter
            )
        )
        rows = result.scalars().all()
    if not rows:
        return []
    return build_mcp_server_configs_from_db(list(rows))


def _inject_code_intelligence_tool(
    tool_names: list[str],
    agent: Agent,
    workspace: Workspace,
) -> list[str]:
    if (
        agent.adapter_name != "custom"
        or workspace.mode != "local"
        or not workspace.bound_path
        or "code_explore" in tool_names
    ):
        return list(tool_names)

    from app.code_intelligence.metadata import MetadataStore

    metadata = MetadataStore(workspace.root_path).read()
    if not metadata.enabled or metadata.status != "ready":
        return list(tool_names)
    return [*tool_names, "code_explore"]


async def execute_simple_run(
    run_id: str,
    cancel_event: asyncio.Event,
    args: RunArgs,
    prompt: str,
    attachments: list[AdapterAttachment],
) -> RunExecutionResult:
    from app.infra.cache_helpers import get_agent_cached, get_workspace_cached
    from app.observability import start_span

    agent = await get_agent_cached(args.agent_id)
    if agent is None:
        raise ValueError(f"Agent not found: {args.agent_id}")
    workspace = await get_workspace_cached(args.conversation_id)
    if workspace is None:
        raise ValueError(f"Workspace not found for conversation: {args.conversation_id}")

    # Merge baseline tools for SDK (custom) agents. Baseline tools are always-on
    # and not UI-selectable; they are prepended here so the agent always has
    # fs_read / fs_write / bash / etc. Old agents that already have baseline
    # tools in toolNames are deduped (order preserved, baseline first).
    # CLI agents (claude-code / codex) skip this merge — they use CLI built-ins.
    configured = args.override_tool_names or agent.tool_names_list
    is_guide = getattr(agent, "is_guide", False)
    if agent.adapter_name in SDK_ADAPTERS and not is_guide:
        base_tool_names = list(dict.fromkeys(
            list(_BASELINE_AGENT_TOOLS) + list(configured)
        ))
    else:
        base_tool_names = list(configured)

    # Reset task memory buffer at the start of a new task (clears previous observations)
    buf = _get_task_mem_buffer()
    if buf is not None:
        try:
            await buf.reset()
        except Exception as e:
            logger.warning("TaskMemBuffer reset failed: %s", e)

    # Task 1.1: Implicitly inject memory_recall for SDK agents only.
    # CLI agents bring their own tools; memory/RAG/skill injection is skipped.
    # Guide agents also skip this (they only own management tools + ask_user).
    if agent.adapter_name in SDK_ADAPTERS and not is_guide:
        if "memory_recall" not in base_tool_names:
            base_tool_names = ["memory_recall"] + list(base_tool_names)
            logger.info(
                "[AgentRunner] Implicitly injected memory_recall tool for SDK agent %s",
                args.agent_id,
            )

    # memory_store: only for SDK agents with memory_enabled=true.
    # CLI agents skip this (they self-manage context and tools).
    if agent.adapter_name in SDK_ADAPTERS and getattr(agent, "memory_enabled", False):
        if "memory_store" not in base_tool_names:
            base_tool_names = list(base_tool_names) + ["memory_store"]
            logger.info(
                "[AgentRunner] Injected memory_store for SDK agent %s (memory_enabled=true)",
                args.agent_id,
            )
    # Inject load_skill when an SDK agent has equipped (and still-present) skills.
    if agent.adapter_name in SDK_ADAPTERS and agent.skill_names_list:
        from app.services.skill_service import list_skills
        available = {m.slug for m in list_skills()}
        if any(s in available for s in agent.skill_names_list) and "load_skill" not in base_tool_names:
            base_tool_names = list(base_tool_names) + ["load_skill"]
            logger.info(
                "[AgentRunner] Injected load_skill for SDK agent %s (equipped skills)",
                args.agent_id,
            )

    # Task 4.1: Dynamically inject RAG tools if conversation has rag_enabled=true
    RAG_TOOLS = ["rag_search", "rag_ingest", "rag_list_documents", "rag_delete_document"]
    async with get_local_db() as db:
        from app.db.models import Conversation
        conv = (
            await db.execute(select(Conversation).where(Conversation.id == args.conversation_id))
        ).scalar_one_or_none()
        if conv and conv.rag_enabled:
            if agent.adapter_name in SDK_ADAPTERS:
                existing = set(base_tool_names)
                new_tools = [t for t in RAG_TOOLS if t not in existing]
                if new_tools:
                    base_tool_names = list(base_tool_names) + new_tools
                    logger.info(
                        "[AgentRunner] Injected RAG tools %s for conversation %s (rag_enabled=true)",
                        new_tools,
                        args.conversation_id,
                    )

    base_tool_names = _inject_code_intelligence_tool(
        list(base_tool_names), agent, workspace
    )

    # Auto-inject companion artifact tools when write_artifact is present.
    # CLI agents get all hub tools via _build_agent_hub_tool_guidance; SDK (Custom)
    # agents only have tools explicitly listed in tool_names. If write_artifact is
    # configured, the agent also needs read_artifact and update_artifact to handle
    # truncation recovery and incremental file writes.
    if agent.adapter_name in SDK_ADAPTERS and "write_artifact" in base_tool_names:
        companion_tools = ["read_artifact", "update_artifact", "deploy_artifact"]
        existing = set(base_tool_names)
        new_tools = [t for t in companion_tools if t not in existing]
        if new_tools:
            base_tool_names = list(base_tool_names) + new_tools
            logger.info(
                "[AgentRunner] Injected companion artifact tools %s for SDK agent %s",
                new_tools,
                args.agent_id,
            )

    # ── Guide agent tool injection guard ──────────────────────────────
    # Guide agents: inject ask_user (since baseline merge was skipped) and
    # keep only management tools + ask_user (filter out any non-management
    # tools that may have leaked in from agent.tool_names).
    # Non-guide agents: filter out management tools even if mistakenly listed.
    if is_guide:
        if "ask_user" not in base_tool_names:
            base_tool_names = ["ask_user"] + list(base_tool_names)
        base_tool_names = [
            t for t in base_tool_names
            if t in _MANAGEMENT_TOOL_NAMES or t == "ask_user"
        ]
    else:
        filtered = [t for t in base_tool_names if t not in _MANAGEMENT_TOOL_NAMES]
        if len(filtered) != len(base_tool_names):
            removed = set(base_tool_names) - set(filtered)
            logger.warning(
                "[AgentRunner] Filtered management tools from non-guide agent %s: %s",
                args.agent_id, removed,
            )
        base_tool_names = filtered

    tool_names = base_tool_names

    adapter = agent_registry.get_adapter(agent)
    with start_span("agent.build_context", agent_id=args.agent_id, run_id=run_id):
        adapter_input = await build_adapter_input(
            args, agent, run_id, prompt, workspace, tool_names,
            args.override_system_prompt, attachments,
            worktree_path=args.override_workspace_path,
        )

    # ── Persist effective_prompt for cache-stable history reconstruction ──
    # The effective_prompt (with dynamic_prefix + [current_time]) must be
    # persisted so that build_history_for can replay the exact same user
    # message, keeping DeepSeek's prefix cache continuous across turns.
    # Without this, history rebuilds from raw text only, losing the injected
    # prefix/suffix and breaking the cache prefix at messages[1].
    if adapter_input.prompt and adapter_input.prompt != prompt:
        try:
            async with get_local_db() as db:
                result = await db.execute(
                    select(Message).where(Message.id == args.trigger_message_id)
                )
                msg = result.scalar_one_or_none()
                if msg and msg.role == "user":
                    parts = msg.parts_list or []
                    if not any(p.get("type") == "effective_prompt" for p in parts):
                        parts.append({"type": "effective_prompt", "content": adapter_input.prompt})
                        msg.parts_list = parts
                        await db.commit()
                        logger.debug(
                            "[agent-runner] Persisted effective_prompt for msg=%s (len=%d)",
                            args.trigger_message_id, len(adapter_input.prompt),
                        )
        except Exception as err:  # noqa: BLE001 - best-effort persistence
            logger.warning("[agent-runner] Failed to persist effective_prompt: %s", err)

    settings = get_settings()
    if agent.adapter_name in SDK_ADAPTERS and settings.use_react_loop:
        resume_from_turn: int | None = None
        if args.resume_from_checkpoint:
            from app.services.checkpoint_service import load_latest_checkpoint

            checkpoint = await load_latest_checkpoint(run_id)
            if checkpoint is not None:
                adapter_input.messages = list(checkpoint.messages_json or [])
                resume_from_turn = checkpoint.turn_number
                logger.info(
                    "[AgentRunner] resume from checkpoint: run=%s turn=%d messages=%d",
                    run_id, resume_from_turn, len(adapter_input.messages),
                )

        # ── MCP lifecycle: connect, discover tools, inject into adapter_input ──
        mcp_manager: Any | None = None
        mcp_configs = await _resolve_mcp_configs(agent)
        if mcp_configs:
            from app.mcp.client_manager import McpClientManager
            mcp_manager = McpClientManager()
            try:
                await mcp_manager.connect_all(mcp_configs)
                mcp_tools = await mcp_manager.list_tools_as_api()
                if mcp_tools:
                    adapter_input.mcp_tools = mcp_tools
                    logger.info(
                        "[AgentRunner] MCP tools injected: %d tools from %d servers",
                        len(mcp_tools), len(mcp_configs),
                    )
            except Exception as err:  # noqa: BLE001 - MCP is best-effort
                logger.warning("[AgentRunner] MCP connect_all failed: %s", err)

        try:
            stream = _run_react_loop(
                adapter, adapter_input, cancel_event,
                run_id, args.agent_id, args.conversation_id, agent.model_id,
                model_provider=agent.model_provider,
                resume_from_turn=resume_from_turn,
                mcp_manager=mcp_manager,
                dispatch_depth=args.dispatch_depth,
                dispatch_mode=args.dispatch_mode,
                user_id=args.user_id,
            )
            result = await consume_stream(
                stream, args.agent_id, run_id,
                hidden=(args.dispatch_visibility == "hidden"),
                user_id=args.user_id,
            )
        finally:
            if mcp_manager is not None:
                await mcp_manager.close_all()
                logger.info("[AgentRunner] MCP connections closed")
    else:
        stream = adapter.stream(adapter_input, cancel_event)

        result = await consume_stream(
            stream, args.agent_id, run_id,
            hidden=(args.dispatch_visibility == "hidden"),
            user_id=args.user_id,
        )
    if args.parent_run_id:
        return result

    try:
        await maybe_create_project_artifact(
            evidence_run_id=run_id,
            conversation_id=args.conversation_id,
            agent_id=args.agent_id,
            result=result,
            user_id=args.user_id,
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
    user_id: str | None = None,
    task_id: str | None = None,
) -> str | None:
    """Auto-create a 'project' artifact from applied fs_write evidence."""
    evidence = get_run_tool_evidence(evidence_run_id)
    if len(evidence.file_writes) == 0:
        return None

    from app.infra.cache_helpers import get_workspace_cached

    workspace = await get_workspace_cached(conversation_id)
    if not workspace:
        return None
    effective_cwd = get_effective_cwd(workspace)

    files = build_project_files(evidence.file_writes, effective_cwd)
    if len(files) == 0:
        return None

    from app.infra.cache_helpers import get_agent_cached

    agent = await get_agent_cached(agent_id)
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
    async with get_local_db() as db:
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
        ),
        user_id=user_id,
    )
    return artifact_id


# ─── Stream consumption + persistence (port of consumeStream) ────────────────
# onToolCall control: return None, or {"stop": True, "result": ..., "isError": bool}
ToolCallControl = dict[str, Any] | None


# Event types that produce visible UI content (messages, parts, tool calls, artifacts).
# When hidden=True (clone-subagent runs), these are persisted to DB but NOT published
# to the SSE bus — the frontend never sees them.
_VISIBLE_EVENT_TYPES = frozenset({
    "message.start", "message.end", "message.usage",
    "part.start", "part.delta", "part.end",
    "tool.call", "tool.result",
    "artifact.create", "deploy.status",
    "plan.created", "plan.step_update",
"file_write_preview.complete",
})


async def consume_stream(
    stream: AsyncIterable[StreamEvent],
    agent_id: str,
    run_id: str,
    on_tool_call: Callable[[StreamEvent], ToolCallControl] | None = None,
    hidden: bool = False,
    user_id: str | None = None,
) -> RunExecutionResult:
    parts_buffer: dict[str, list[dict]] = {}
    artifact_ids: list[str] = []
    output_message_ids: list[str] = []
    output_artifacts: dict[str, str] = {}
    output_key_by_artifact_id: dict[str, str] = {}
    tool_name_by_call_id: dict[str, str] = {}
    current_message_id: str | None = None
    completed_message_ids: set[str] = set()
    _plan_stats_payload: dict | None = None
    stop_reason: str | None = None
    stop_reason_label: str | None = None

    # Direct-write mode: persist all events to local SQLite (dual-DB) or remote PG (server mode).
    # Redis Stream write-behind has been removed in the dual-DB migration.

    # Wrap the stream iteration in a try/finally so that the underlying async
    # generator is always properly closed — even when we break early on a
    # terminal tool call. Without this, CLI adapter subprocesses
    # are left running after the stream consumer stops reading.
    try:
        async for event in stream:
            if event.type == "message.start":
                current_message_id = event.message_id
            if event.type == "tool.call":
                tool_name_by_call_id[event.call_id] = event.tool_name
            if event.type == "run.usage":
                if getattr(event, "stop_reason", None):
                    stop_reason = event.stop_reason
                    stop_reason_label = getattr(event, "stop_reason_label", None)

            # Publish to SSE before persisting — SSE delivery is never blocked
            # by remote database write latency.
            if not (hidden and event.type in _VISIBLE_EVENT_TYPES):
                publish(event, user_id=user_id)
            await persist_event(
                event, parts_buffer, run_id, agent_id, output_message_ids, artifact_ids, hidden
            )

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
                if not hidden:
                    publish(
                        PartStartEvent(
                            conversation_id=event.conversation_id,
                            timestamp=now_ms(),
                            message_id=current_message_id,
                            part_index=part_index,
                            part=ref_part,
                        ),
                        user_id=user_id,
                    )
                await _persist_or_stream(None, run_id, event, parts, False, message_id=current_message_id)
            # deploy.status: append a deploy_status part to the live message
            if event.type == "deploy.status" and current_message_id:
                parts = parts_buffer.get(current_message_id, [])
                part_index = len(parts)
                deploy_part = {
                    "type": "deploy_status",
                    "deployment": event.deployment.model_dump(by_alias=True),
                }
                parts.append(deploy_part)
                parts_buffer[current_message_id] = parts
                if not hidden:
                    publish(
                        PartStartEvent(
                            conversation_id=event.conversation_id,
                            timestamp=now_ms(),
                            message_id=current_message_id,
                            part_index=part_index,
                            part=deploy_part,
                        ),
                        user_id=user_id,
                    )
                await _persist_or_stream(None, run_id, event, parts, False, message_id=current_message_id)
            if event.type == "plan.created" and current_message_id:
                parts = parts_buffer.get(current_message_id, [])
                part_index = len(parts)
                plan_part = {
                    "type": "execution_plan",
                    "planId": event.plan_id,
                    "steps": [s.model_dump(by_alias=True) for s in event.steps],
                    "complexity": event.complexity,
                }
                parts.append(plan_part)
                parts_buffer[current_message_id] = parts
                if not hidden:
                    publish(
                        PartStartEvent(
                            conversation_id=event.conversation_id,
                            timestamp=now_ms(),
                            message_id=current_message_id,
                            part_index=part_index,
                            part=plan_part,
                        ),
                        user_id=user_id,
                    )
                await _persist_or_stream(None, run_id, event, parts, False, message_id=current_message_id)

            # plan.step_update: update execution_plan part steps in parts_buffer
            if event.type == "plan.step_update" and current_message_id:
                parts = parts_buffer.get(current_message_id, [])
                updated_steps = [s.model_dump(by_alias=True) for s in event.steps]
                for p in parts:
                    if p.get("type") == "execution_plan" and p.get("planId") == event.plan_id:
                        p["steps"] = updated_steps
                        break
                parts_buffer[current_message_id] = parts
                if not hidden:
                    publish(event, user_id=user_id)
                await _persist_or_stream(None, run_id, event, parts, False, message_id=current_message_id)

            # dispatch.start → plan step auto-update (mark step as in_progress)
            if event.type == "dispatch.start":
                from app.services.plan_dispatch_mapping import plan_dispatch_mapping as _pdm
                from app.services.plan_registry import plan_registry as _preg
                mapping_key = _pdm.lookup_by_task(event.task_id)
                if mapping_key is not None:
                    plan_id, step_id = mapping_key
                    plan = _preg.get(plan_id)
                    if plan is not None:
                        target = next((s for s in plan.steps if s.id == step_id), None)
                        if target is not None and target.status == "pending":
                            target.status = "in_progress"
                            _preg.update(plan)
                            if not hidden:
                                publish(
                                    PlanStepUpdateEvent(
                                        conversation_id=event.conversation_id,
                                        timestamp=now_ms(),
                                        planId=plan_id,
                                        steps=plan.steps,
                                    ),
                                    user_id=user_id,
                                )

            # dispatch.end → plan step auto-update (aggregate status)
            if event.type == "dispatch.end":
                from app.services.plan_dispatch_mapping import plan_dispatch_mapping as _pdm
                from app.services.plan_registry import plan_registry as _preg
                mapping_key = _pdm.lookup_by_task(event.task_id)
                if mapping_key is not None:
                    plan_id, step_id = mapping_key
                    # Update dispatch task status
                    _pdm.update_task_status(event.task_id, event.status)
                    # Aggregate status across all tasks for this step
                    agg = _pdm.aggregate_step_status(plan_id, step_id)
                    if agg is not None and agg != "in_progress":
                        plan = _preg.get(plan_id)
                        if plan is not None:
                            target = next((s for s in plan.steps if s.id == step_id), None)
                            if target is not None and target.status != agg:
                                target.status = agg  # type: ignore[assignment]
                                _preg.update(plan)
                                if not hidden:
                                    publish(
                                        PlanStepUpdateEvent(
                                            conversation_id=event.conversation_id,
                                            timestamp=now_ms(),
                                            planId=plan_id,
                                            steps=plan.steps,
                                        ),
                                        user_id=user_id,
                                    )

            # file_write_preview.complete: update matching file_write_preview part in parts_buffer
            if event.type == "file_write_preview.complete" and current_message_id:
                parts = parts_buffer.get(current_message_id, [])
                for p in parts:
                    if p.get("type") == "file_write_preview" and p.get("callId") == event.call_id:
                        p["status"] = event.status
                        p["path"] = event.path
                        p["oldContent"] = event.old_content
                        p["newContent"] = event.new_content
                        break
                parts_buffer[current_message_id] = parts
                if not hidden:
                    publish(event, user_id=user_id)
                await _persist_or_stream(None, run_id, event, parts, False, message_id=current_message_id)

            # Run-end cleanup: finalize all execution_plan parts + write stats + clear registries
            if event.type == "run.end":
                # Finalize execution_plan step statuses in parts_buffer
                if current_message_id:
                    run_status = event.status
                    parts = parts_buffer.get(current_message_id, [])
                    plan_changed = False
                    for p in parts:
                        if p.get("type") != "execution_plan":
                            continue
                        for step in p.get("steps", []):
                            if step.get("status") == "in_progress":
                                step["status"] = "done" if run_status == "complete" else "failed"
                                plan_changed = True
                            elif step.get("status") == "pending":
                                step["status"] = "skipped"
                                plan_changed = True
                    if plan_changed:
                        parts_buffer[current_message_id] = parts
                        from app.schemas.plan import PlanStep as PlanStepModel
                        for p in parts:
                            if p.get("type") == "execution_plan":
                                if not hidden:
                                    publish(
                                        PlanStepUpdateEvent(
                                            conversation_id=event.conversation_id,
                                            timestamp=now_ms(),
                                            planId=p["planId"],
                                            steps=[PlanStepModel.model_validate(s) for s in p.get("steps", [])],
                                        ),
                                        user_id=user_id,
                                    )
                        await _persist_or_stream(None, run_id, event, parts, False, message_id=current_message_id)

            # Run-end cleanup (DB write deferred to finalize to avoid connection pool errors)
                from app.services.plan_registry import plan_registry as _plan_registry_stats
                _all_plans = list(_plan_registry_stats._plans.values())
                if _all_plans:
                    plan = _all_plans[-1]
                    # Mirror the finalized statuses from parts_buffer into the registry
                    if current_message_id:
                        for p in parts_buffer.get(current_message_id, []):
                            if p.get("type") != "execution_plan":
                                continue
                            finalized_by_id = {s["id"]: s["status"] for s in p.get("steps", [])}
                            for step in plan.steps:
                                if step.id in finalized_by_id:
                                    step.status = finalized_by_id[step.id]
                    completed_steps = sum(1 for s in plan.steps if s.status == "done")
                    skipped_steps = sum(1 for s in plan.steps if s.status == "skipped")
                    _plan_stats_payload = {
                        "created": True,
                        "complexity": plan.complexity,
                        "stepCount": len(plan.steps),
                        "completedSteps": completed_steps,
                        "skippedSteps": skipped_steps,
                        "addedStepsCount": plan.added_steps_count,
                    }
                else:
                    _plan_stats_payload = None

                # Clean up plan_dispatch_mapping and plan_registry on run end
                from app.services.plan_dispatch_mapping import plan_dispatch_mapping as _pdm
                from app.services.plan_registry import plan_registry as _plan_registry
                _pdm.cleanup_run()
                _plan_registry.cleanup_run()

            if event.type == "message.end":
                completed_message_ids.add(event.message_id)
                current_message_id = None
            if event.type == "tool.result":
                tool_name = tool_name_by_call_id.get(event.call_id)
                handoff = _read_artifact_handoff_result(event.result)
                if handoff:
                    output_key_by_artifact_id[handoff[0]] = handoff[1]
                # Push StepObservation + ToolCallTrace to shared buffers
                if tool_name:
                    await _push_tool_observation(
                        event.call_id, tool_name, event.result, event.is_error,
                    )
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
                        if not hidden:
                            publish(result_event, user_id=user_id)
                        await persist_event(
                            result_event, parts_buffer, run_id, agent_id, output_message_ids, artifact_ids, hidden
                        )

                    end_event = MessageEndEvent(
                        conversation_id=event.conversation_id,
                        timestamp=now_ms(),
                        message_id=event.message_id,
                    )
                    if not hidden:
                        publish(end_event, user_id=user_id)
                    await persist_event(
                        end_event, parts_buffer, run_id, agent_id, output_message_ids, artifact_ids, hidden
                    )
                    current_message_id = None
                    break
    finally:
        # Final synchronous flush: ensure all parts are in the DB before cleanup.
        if parts_buffer:
            try:
                async with get_local_db() as db:
                    for msg_id, parts in parts_buffer.items():
                        values: dict[str, Any] = {"parts": parts}
                        if msg_id in completed_message_ids:
                            values["status"] = "complete"
                        await db.execute(
                            update(Message).where(Message.id == msg_id).values(**values)
                        )
            except Exception:
                logger.debug("[consume_stream] final sync flush failed", exc_info=True)
        # Ensure the underlying stream's async generator is closed so that
        # adapter cleanup (subprocess shutdown, connection close) runs.
        # aclose() is a no-op on an already-exhausted generator.
        _aclose = getattr(stream, "aclose", None)
        if _aclose is not None:
            try:
                await _aclose()
            except Exception:
                logger.debug("[consume_stream] stream.aclose() failed", exc_info=True)

    return RunExecutionResult(
        artifact_ids=artifact_ids,
        output_message_ids=output_message_ids,
        output_artifacts=output_artifacts,
        plan_stats=_plan_stats_payload,
        stop_reason=stop_reason,
        stop_reason_label=stop_reason_label,
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
    hidden: bool = False,
) -> None:
    """Persist a stream event into the messages / runs tables (camelCase parts).

    All events are written directly to local SQLite (dual-DB mode) or remote
    PostgreSQL (server mode) via get_db(). Usage events (run.usage,
    message.usage) are persisted via fire-and-forget asyncio.create_task.
    Redis Stream write-behind has been removed in the dual-DB migration.
    """
    etype = event.type

    if etype == "run.usage":
        asyncio.create_task(_update_run_usage(event.run_id, event.usage.model_dump(by_alias=True)))
        return
    if etype == "message.usage":
        asyncio.create_task(_update_message_usage(event.message_id, event.usage.model_dump(by_alias=True)))
        return
    if etype == "message.start":
        parts_buffer[event.message_id] = []
        output_message_ids.append(event.message_id)
        async with get_local_db() as db:
            msg = Message(
                id=event.message_id,
                conversation_id=event.conversation_id,
                role="agent",
                agent_id=agent_id,
                status="streaming",
                run_id=run_id,
                created_at=event.timestamp,
                hidden=hidden,
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
        part_dict = event.part
        # Capture timestamp into startedAt for thinking/text/code parts
        if isinstance(part_dict, dict) and part_dict.get("type") in ("thinking", "text", "code"):
            part_dict["startedAt"] = event.timestamp
        parts[event.part_index] = part_dict
        parts_buffer[event.message_id] = parts
        await _persist_or_stream(None, run_id, event, parts, False)
        return
    if etype == "part.end":
        parts = parts_buffer.get(event.message_id)
        if parts is not None and event.part_index < len(parts):
            part = parts[event.part_index]
            if isinstance(part, dict) and part.get("type") == "thinking":
                part["endedAt"] = event.timestamp
        await _persist_or_stream(None, run_id, event, parts or [], False)
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
        await _persist_or_stream(None, run_id, event, parts, False)
        return
    if etype == "tool.call":
        parts = parts_buffer.get(event.message_id, [])
        parts.append(
            {
                "type": "tool_use",
                "callId": event.call_id,
                "toolName": event.tool_name,
                "args": event.args,
                "startedAt": event.timestamp,
            }
        )
        parts_buffer[event.message_id] = parts
        await _persist_or_stream(None, run_id, event, parts, False)
        return
    if etype == "tool.result":
        parts = parts_buffer.get(event.message_id, [])
        parts.append(
            {
                "type": "tool_result",
                "callId": event.call_id,
                "result": event.result,
                "isError": event.is_error,
                "endedAt": event.timestamp,
            }
        )
        parts_buffer[event.message_id] = parts
        await _persist_or_stream(None, run_id, event, parts, False)
        return
    if etype == "message.end":
        final_parts = parts_buffer.get(event.message_id, [])
        async with get_local_db() as db:
            await db.execute(
                update(Message)
                .where(Message.id == event.message_id)
                .values(status="complete", parts=final_parts)
            )
        return
    if etype == "artifact.create":
        artifact_ids.append(event.artifact.id)
        return


async def _persist_or_stream(
    _redis_client: Any | None,
    _run_id: str,
    _event: StreamEvent,
    parts: list[dict],
    _use_stream: bool,
    *,
    message_id: str | None = None,
) -> None:
    """Directly write message parts to the database.

    Redis Stream write-behind has been removed. This function now always
    writes directly to the database (SQLite in dual-DB mode, PG in server mode).
    The signature is kept for backward compatibility with callers that haven't
    been updated yet.
    """
    fallback_id = message_id if message_id is not None else _event.message_id
    await _update_message_parts(fallback_id, parts)


async def _update_message_parts(message_id: str, parts: list[dict]) -> None:
    async with get_local_db() as db:
        await db.execute(
            update(Message).where(Message.id == message_id).values(parts=parts)
        )


async def _update_run_usage(run_id: str, usage: dict) -> None:
    """Fire-and-forget: update agent_runs.usage (latest wins, failures logged)."""
    try:
        async with get_local_db() as db:
            await db.execute(
                update(AgentRun).where(AgentRun.id == run_id).values(usage=usage)
            )
    except Exception as e:
        logger.warning("[persist_event] fire-and-forget run.usage update failed for run %s: %s", run_id, e)


async def _update_message_usage(message_id: str, usage: dict) -> None:
    """Fire-and-forget: update messages.usage (latest wins, failures logged)."""
    try:
        async with get_local_db() as db:
            await db.execute(
                update(Message).where(Message.id == message_id).values(usage=usage)
            )
    except Exception as e:
        logger.warning("[persist_event] fire-and-forget message.usage update failed for msg %s: %s", message_id, e)


# ─── DB / event helpers ──────────────────────────────────────────────────────
async def insert_run(run_id: str, args: RunArgs, agent_id: str) -> None:
    now = now_ms()
    async with get_local_db() as db:
        existing = (
            await db.execute(select(AgentRun).where(AgentRun.id == run_id))
        ).scalar_one_or_none()
        if existing is not None:
            existing.status = "running"
            existing.started_at = now
            existing.finished_at = None
            existing.error = None
        else:
            db.add(
                AgentRun(
                    id=run_id,
                    conversation_id=args.conversation_id,
                    agent_id=agent_id,
                    trigger_message_id=args.trigger_message_id,
                    status="running",
                    parent_run_id=args.parent_run_id,
                    started_at=now,
                )
            )


async def _insert_run_or_resume(run_id: str, args: RunArgs, agent_id: str) -> None:
    """Insert a new run, or update an existing run for resume."""
    if not args.resume_from_checkpoint:
        await insert_run(run_id, args, agent_id)
        return

    async with get_local_db() as db:
        run = (
            await db.execute(select(AgentRun).where(AgentRun.id == run_id))
        ).scalar_one_or_none()
        if run is None:
            await insert_run(run_id, args, agent_id)
            return
        run.status = "running"
        run.finished_at = None
        run.error = None


async def finalize(
    run_id: str,
    args: RunArgs,
    status: str,  # 'complete' | 'failed' | 'aborted'
    result: RunExecutionResult,
    error: str | None = None,
) -> RunResult:
    finished_at = now_ms()

    # During shutdown the DB engine may already be closed; each DB-touching
    # step degrades independently so RunEndEvent still reaches the frontend.
    db_down = False

    if status in ("failed", "aborted"):
        try:
            await _persist_unresolved_tool_failures(
                run_id, args.conversation_id, status, error, finished_at,
                user_id=args.user_id,
            )
        except RuntimeError as e:
            db_down = True
            logger.warning("[finalize] skip _persist_unresolved_tool_failures: %s", e)

    try:
        async with get_local_db() as db:
            run = (
                await db.execute(select(AgentRun).where(AgentRun.id == run_id))
            ).scalar_one_or_none()
            if run is not None:
                run.status = status
                run.finished_at = finished_at
                run.error = error
                if result.plan_stats is not None:
                    usage = dict(run.usage or {})
                    usage["plan"] = result.plan_stats
                    run.usage = usage

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
    except RuntimeError as e:
        db_down = True
        logger.warning("[finalize] skip AgentRun/Message update: %s", e)

    if status in ("failed", "aborted"):
        try:
            await _emit_error_visualisation(run_id, args, status, error, result.output_message_ids)
        except RuntimeError as e:
            db_down = True
            logger.warning("[finalize] skip _emit_error_visualisation: %s", e)

    try:
        async with get_local_db() as db:
            conv = (
                await db.execute(select(Conversation).where(Conversation.id == args.conversation_id))
            ).scalar_one_or_none()
            if conv is not None:
                conv.updated_at = finished_at
    except RuntimeError as e:
        db_down = True
        logger.warning("[finalize] skip Conversation update: %s", e)

    if db_down:
        logger.warning("[finalize] run=%s status=%s finalized with DB unavailable", run_id, status)

    publish(
        RunEndEvent(
            conversation_id=args.conversation_id,
            timestamp=finished_at,
            run_id=run_id,
            status=status,
            error=error,
            stop_reason=result.stop_reason,
            stop_reason_label=result.stop_reason_label,
        ),
        user_id=args.user_id,
    )

    _drain_queued_runs(args.conversation_id)

    return RunResult(
        run_id=run_id,
        status=status,
        error=error,
        artifact_ids=result.artifact_ids,
        output_message_ids=result.output_message_ids,
        output_artifacts=result.output_artifacts,
        stop_reason=result.stop_reason,
        stop_reason_label=result.stop_reason_label,
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
        async with get_local_db() as db:
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
                    ),
                    user_id=args.user_id,
                )
                return

    # else: create a fresh error message
    error_message_id = f"msg_err_{run_id}"
    async with get_local_db() as db:
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
        ),
        user_id=args.user_id,
    )
    publish(
        PartStartEvent(
            conversation_id=args.conversation_id,
            timestamp=now,
            message_id=error_message_id,
            part_index=0,
            part={"type": "text", "content": error_text},
        ),
        user_id=args.user_id,
    )
    publish(
        MessageEndEvent(
            conversation_id=args.conversation_id,
            timestamp=now,
            message_id=error_message_id,
        ),
        user_id=args.user_id,
    )


async def _persist_unresolved_tool_failures(
    run_id: str,
    conversation_id: str,
    status: str,  # 'failed' | 'aborted'
    error: str | None,
    timestamp: int,
    user_id: str | None = None,
) -> None:
    """Close any tool_use parts with no matching tool_result (synthesize an error)."""
    result = _build_unresolved_tool_failure_result(status, error)
    async with get_local_db() as db:
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
            ),
            user_id=user_id,
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
    from app.observability import start_span
    with start_span(
        "agent.finalize",
        agent_id=args.agent_id,
        run_id=run_id,
        conversation_id=args.conversation_id,
        total_turns=getattr(result, 'turns', 0),
        total_tokens=getattr(result, 'total_tokens', 0),
        duration_ms=getattr(result, 'duration_ms', 0),
    ) as span:
        return await finalize(run_id, args, "complete", result)


async def finalize_failed(run_id: str, args: RunArgs, error: str) -> RunResult:
    from app.observability import start_span
    with start_span(
        "agent.finalize",
        agent_id=args.agent_id,
        run_id=run_id,
        conversation_id=args.conversation_id,
    ) as span:
        if span.is_recording():
            span.set_attribute("agenthub.success", False)
            span.set_attribute("agenthub.error", str(error)[:500])
        return await finalize(run_id, args, "failed", _empty_run_execution_result(), error)


def publish(event: StreamEvent, user_id: str | None = None) -> None:
    event_bus.publish(event, user_id=user_id)


async def _run_online_eval_hook(run_id: str) -> None:
    """Background hook: run online rule evaluation after agent run completes."""
    try:
        from app.observability.eval_rules import run_eval_and_log
        await run_eval_and_log(run_id, [])
    except Exception as e:
        logger.warning("Online eval hook failed: %s", e)


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
    worktree_path: str | None = None,
) -> AdapterInput:
    effective_cwd = worktree_path or get_effective_cwd(workspace)
    is_cli = agent.adapter_name in CLI_ADAPTERS
    is_sdk = agent.adapter_name in SDK_ADAPTERS

    # ── system prompt (shared by both paths) ──────────────────────────
    base_system_prompt = system_prompt_override or agent.system_prompt
    system_prompt_with_workspace = (
        _build_workspace_context_block(workspace, effective_cwd)
        + "\n\n" + base_system_prompt
    )

    # ── tool guidance: SDK only (CLI agents bring their own tools) ────
    if is_sdk:
        tool_guidance = _build_agent_hub_tool_guidance(agent, tool_names, workspace)
        if tool_guidance:
            system_prompt_with_workspace += "\n\n" + tool_guidance

    # ── skill metadata: SDK only (CLI agents skip AChat skill injection) ─
    if is_sdk:
        skill_block = _build_skill_metadata_block(agent)
        if skill_block:
            system_prompt_with_workspace += "\n\n" + skill_block

    # ── API key ─────────────────────────────────────────────────────
    if is_cli:
        # CLI agents use their own authentication (claude login / codex login).
        # Only inject per-agent API key override via extra_env when explicitly set.
        effective_api_key: str | None = None
        effective_api_base_url: str | None = None
        cli_extra_env: dict[str, str] = {}
        # Per-user HOME isolation: CLI tools (claude, codex) store config and
        # credentials under HOME/USERPROFILE. Override to a user-scoped directory
        # so different users' CLI sessions don't share credentials.
        if args.user_id:
            import os as _os
            user_home = _os.path.join(
                str(get_settings().data_path), "users", args.user_id, "home"
            )
            _os.makedirs(user_home, exist_ok=True)
            if IS_WINDOWS:
                cli_extra_env["USERPROFILE"] = user_home
            else:
                cli_extra_env["HOME"] = user_home
        if agent.api_key:
            if agent.adapter_name == "claude-code":
                cli_extra_env["ANTHROPIC_API_KEY"] = agent.api_key
            elif agent.adapter_name == "codex":
                cli_extra_env["OPENAI_API_KEY"] = agent.api_key
        if agent.api_base_url:
            if agent.adapter_name == "claude-code":
                cli_extra_env["ANTHROPIC_BASE_URL"] = agent.api_base_url
            elif agent.adapter_name == "codex":
                cli_extra_env["OPENAI_BASE_URL"] = agent.api_base_url
    else:
        # SDK path: full four-layer key chain (agent > app_settings > env > OAuth).
        effective_api_key = agent.api_key
        effective_api_base_url = agent.api_base_url
        cli_extra_env = {}
        if not effective_api_key or (
            not effective_api_base_url and agent.adapter_name == "claude-code"
        ):
            settings = await get_user_settings(args.user_id) if args.user_id else await get_app_settings()
            if not effective_api_key:
                effective_api_key = _pick_settings_key(settings, agent)
            if not effective_api_base_url and agent.adapter_name == "claude-code":
                effective_api_base_url = settings.anthropic_base_url

    # ── cross-run history: SDK only (CLI agents use session resume) ──
    history: list[dict] = []
    if is_sdk and not args.override_prompt:
        async with get_local_db() as db:
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
                user_id=args.user_id or "",
            )
        except Exception as err:  # noqa: BLE001 - degrade to no-history rather than crash
            logger.warning(
                "[agent-runner] build_history_for failed; continuing without history: %s",
                err,
            )
            history = []

    # ── PromptAssembler enrichment (SDK only; CLI agents self-manage context;
    #     guide agents skip — they use management tools for explicit queries,
    #     so ProfileSource/ToolStateSource DB lookups are pure overhead) ─
    dynamic_prefix = ""
    is_guide = getattr(agent, "is_guide", False)
    if is_sdk and not is_guide:
        assembler = _get_prompt_assembler()
        if assembler and not args.override_prompt:
            try:
                from app.services.prompt_assembler import Query
                if tool_names:
                    mode = "tool"
                else:
                    mode = "chat"
                q = Query(mode=mode, text=prompt, conversation_id=args.conversation_id, agent_id=args.agent_id, user_id=args.user_id or "")
                ctx = await assembler.assemble(q)
                enriched = ctx.render_static()
                if enriched:
                    system_prompt_with_workspace += "\n\n" + enriched
                dynamic_prefix = ctx.render_dynamic()
                _slot_summary = ", ".join(
                    f"{fs.kind}={'skip' if fs.skipped else len(fs.items)}"
                    for fs in ctx.filled
                )
                logger.info(
                    "[cache-debug] mode=%s static_len=%d dynamic_len=%d sys_prompt_hash=%d "
                    "slots=[%s]",
                    mode, len(enriched), len(dynamic_prefix),
                    hash(system_prompt_with_workspace), _slot_summary,
                )
            except Exception as err:  # noqa: BLE001 - assembler is best-effort
                logger.warning("[agent-runner] PromptAssembler enrichment failed: %s", err)

    # ── session metadata: static fields (SDK only; cache-stable prefix) ──
    _meta_time_bucket: str | None = None
    if is_sdk:
        try:
            _settings = get_settings()
            # Auto-detect location via IP geolocation when configured as 'auto'
            _loc = _settings.default_location
            if _loc == "auto":
                _loc = await _detect_location()
            _lang, _tz, _loc_out, _time_bucket = _blunt_metadata(
                _settings.default_language,
                _settings.default_timezone,
                _loc,
                datetime.now(),
            )
            _meta_time_bucket = _time_bucket
            system_prompt_with_workspace += (
                f"\n\n[session] language={_lang} timezone={_tz} location={_loc_out}"
            )
            logger.info(
                "[cache-debug] session_metadata injected: lang=%s tz=%s "
                "loc=%s time_bucket=%s sys_prompt_hash=%d",
                _lang, _tz, _loc_out, _time_bucket,
                hash(system_prompt_with_workspace),
            )
        except Exception as err:  # noqa: BLE001 - metadata is best-effort
            logger.warning("[agent-runner] session metadata injection failed: %s", err)

    # ── prompt: CLI agents may get a context-summary prefix ──────────
    effective_prompt = prompt
    if is_cli and not args.override_prompt:
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

    # ── dynamic content injection (SDK only; from PromptAssembler) ──
    if dynamic_prefix:
        effective_prompt = f"{dynamic_prefix}\n\n{effective_prompt}"
        logger.info(
            "[cache-debug] dynamic injected: prefix_len=%d effective_prompt_len=%d",
            len(dynamic_prefix), len(effective_prompt),
        )

    # ── session metadata: dynamic field (SDK only; prompt tail) ──
    if is_sdk and _meta_time_bucket:
        effective_prompt = f"{effective_prompt}\n\n[current_time: {_meta_time_bucket}]"

    # ── custom_config: SDK only ──────────────────────────────────────
    custom_config = (
        CustomConfig(
            model_provider=agent.model_provider,
            supports_vision=agent.supports_vision,
        )
        if is_sdk and agent.model_provider and agent.model_id
        else None
    )

    # ── CLI-specific fields ──────────────────────────────────────────
    cli_exec_path = agent.executable_path if is_cli else None
    cli_custom_args = agent.custom_args_list if is_cli else None
    # Session resume: deferred until AgentRun gets a session_id column.
    cli_resume_session_id: str | None = None

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
        # CLI fields
        executable_path=cli_exec_path,
        extra_env=cli_extra_env if cli_extra_env else None,
        custom_args=cli_custom_args,
        resume_session_id=cli_resume_session_id,
        mcp_config=None,  # MCP bridge deferred
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


def _build_workspace_context_block(workspace: Workspace, cwd_override: str | None = None) -> str:
    """Inject a `<workspace_info>` block so the LLM knows its real cwd / mode."""
    cwd = cwd_override or get_effective_cwd(workspace)
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
        sdk_agent_hub_tools = [
            "write_artifact",
            "read_artifact",
            "update_artifact",
            "deploy_artifact",
            "deploy_workspace",
            ASK_USER_TOOL_NAME,
        ]
        tools.update(sdk_agent_hub_tools)

    sections: list[str] = []

    def add(lines: list[str]) -> None:
        sections.append("\n".join(lines))

    has_workspace_file_tools = (
        "fs_read" in tools or "fs_write" in tools or "bash" in tools
        or "fs_list" in tools or "fs_glob" in tools or "fs_grep" in tools
        or "code_explore" in tools or is_sdk_agent
    )

    if len(tools) > 0:
        add(
            [
                "## AChat 工具调用规范",
                "- 需要调用工具时，必须用工具调用通道提交结构化参数，不要把 JSON 示例写进普通回复里假装调用。",
                "- 字段名必须严格使用工具 schema 里的 camelCase，例如 artifactId、attachmentId、"
                "parentArtifactId、outputKey、dependsOn、agentId、taskDescription。",
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
                "- 复杂任务（如分析项目、理解代码库、多文件改动）先调 create_plan 创建执行计划，再按步骤操作文件；简单任务直接做。",
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
                "内容过大时的策略：如果 write_artifact 因内容过长被截断报错，不要重试同样的大内容。改用 update_artifact 分片写入：先创建最小 web_app，再逐个添加文件。",
            ]
        )

    if "update_artifact" in tools:
        add(
            [
                "### update_artifact",
                "用途：向已有 web_app 产物追加、修改或删除文件，适用于大型 web 应用分片写入。",
                '正确流程：先 write_artifact 创建最小 web_app 得到 artifactId，再 update_artifact({ artifactId: "art_123", addFiles: { "style.css": "..." } }) 逐个添加文件。',
                "截断恢复：当 write_artifact 因内容过长报错时，立即改用 update_artifact 分片写入，不要重试大内容。",
                "限制：只支持 web_app 类型；每次最多 20 个文件操作；单文件最大 100KB；路径必须为相对路径。",
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

    has_file_tools = (
        "fs_list" in tools or "fs_read" in tools or "fs_write" in tools
        or "fs_edit" in tools or "fs_glob" in tools or "fs_grep" in tools
        or "code_explore" in tools or "bash" in tools
    )
    if has_file_tools:
        file_lines = [
            "### 文件探索与操作工具",
            "路径必须解析在 workspace 内。各工具适用场景：",
        ]
        if "fs_list" in tools:
            file_lines.append(
                "- fs_list：查看目录内容，支持 depth 参数递归展开。分析项目结构时用 fs_list({ depth: 3 }) 一次性获取多级目录概览，避免逐目录遍历"
            )
        if "fs_glob" in tools:
            file_lines.append("- fs_glob：按模式批量查找文件（如 **/*.py），一次调用覆盖整个项目")
        if "fs_grep" in tools:
            file_lines.append("- fs_grep：按正则搜索文件内容，定位符号位置而不用读全文")
        file_lines.append(
            "- 如果任务需要系统性探索（如分析项目、理解代码库），先调 create_plan 创建计划再按步骤探索，不要直接开始读文件。"
        )
        if "fs_read" in tools:
            file_lines.append(
                "- fs_read：读取文件内容，支持三种模式：full（默认完整读取）、outline（只返回结构骨架，token 消耗约 1/10）、head（只读前 N 行）"
            )
        if "fs_list" in tools and "fs_read" in tools:
            file_lines.append(
                '- 探索项目时的推荐流程：先用 fs_list({ depth: 3 }) 获取项目结构概览，再用 fs_read({ path: "...", mode: "outline" }) 快速了解各文件结构，最后对关键文件用 fs_read({ path: "..." }) 完整读取'
            )
        if "fs_write" in tools or "fs_edit" in tools:
            parts = []
            if "fs_write" in tools:
                parts.append("fs_write")
            if "fs_edit" in tools:
                parts.append("fs_edit")
            file_lines.append(f"- {' / '.join(parts)}：写入新文件 / 精准修改已有文件")
        if "bash" in tools:
            file_lines.append("- bash：运行构建、测试、安装等 shell 命令")

        file_lines.append("")
        if "fs_list" in tools:
            file_lines.append(
                'fs_list 正确案例：fs_list({ path: "", depth: 3 }) 获取项目多级结构概览；'
                'fs_list({ path: "src/server" }) 查看单个子目录；'
                '需要查看 .env.example 等隐藏文件时用 fs_list({ showHidden: true })。'
            )
        if "fs_glob" in tools:
            file_lines.append('fs_glob 正确案例：fs_glob({ pattern: "**/*.py" }) 一次拿到全部 Python 文件清单。')
        if "fs_grep" in tools:
            file_lines.append('fs_grep 正确案例：fs_grep({ pattern: "def |class ", glob: "*.py" }) 定位所有函数和类定义。')
        if "fs_read" in tools:
            file_lines.append(
                'fs_read 正确案例：fs_read({ path: "src/app/page.tsx", mode: "outline" }) 快速了解文件结构；'
                'fs_read({ path: "src/app/page.tsx" }) 完整读取先看现有代码再改；'
                'fs_read({ path: "...", mode: "head" }) 快速预览文件开头；'
                '大文件截断后用 fs_read({ path: "...", offset: 200, limit: 100 }) 继续读取。'
            )
        if "fs_write" in tools:
            file_lines.append(
                'fs_write 正确案例：fs_write({ path: "src/app/page.tsx", content: "完整的新文件内容" })；content 是完整文件内容，不是 diff patch。'
            )
        if "bash" in tools:
            file_lines.append(
                'bash 正确案例：bash({ command: "pnpm typecheck" })；子目录命令用 bash({ command: "pnpm build", cwd: "frontend", timeoutMs: 300000 })，不要写 cd frontend && pnpm build。'
            )
        add(file_lines)

    if "code_explore" in tools:
        add(
            [
                "### code_explore",
                "用途：基于代码图谱回答结构性问题（项目入口、调用链、模块依赖、修改影响范围）。",
                '适用问题："主要流程是什么""X 从哪里被调用""改这个函数会影响哪些模块"。',
                "不适用问题：读某个文件的具体内容（用 fs_read）、搜索某个字符串（用 fs_grep）。",
                '正确案例：理解项目架构时，先 code_explore({ query: "项目入口和主要流程" }) 获取全局视角，再针对性读文件。',
                '降级方案：如果返回"图谱未就绪"，改用 fs_list({ depth: 3 }) + fs_grep + fs_read(mode="outline") 手动探索。',
            ]
        )

    if "task_dispatch" in tools:
        add(
            [
                "### task_dispatch",
                "用途：协调者将任务派发给其他 Agent 执行；子 Agent 独立运行并返回结果。",
                "字段名必须是 agentId、taskDescription、dependsOn；不要写 snake_case。",
                "简单任务自己做，不要每件事都派发。",
            ]
        )

    if "memory_recall" in tools:
        add(
            [
                "### memory_recall",
                "用途：按语义检索长期记忆与用户偏好，在任务开始时或用户引用历史时主动召回。",
                '正确案例：用户说"上次那个项目"，调用 memory_recall({ query: "用户上次提到的项目" }) 确认具体指什么。',
                "query 写法：用自然语言问题或具体关键词，不要只写分类标签如\"偏好\"。",
                "注意：记忆存储是自动的（对话后系统自动提取），你只需负责召回；召回结果为空说明没有相关记忆，不要反复重试。",
            ]
        )

    if "memory_store" in tools:
        add(
            [
                "### memory_store",
                "用途：主动存储长期稳定的记忆，跨会话持久化。",
                '正确案例：发现用户项目用 React 19 + Next.js 16，调用 memory_store({ content: "用户项目使用 React 19 + Next.js 16 App Router", category: "fact", importance: 0.8 })。',
                '错误案例：存储"用户刚才问了登录问题"（临时对话细节）、"项目有 package.json"（可从代码推导）。',
                "category 选择：fact=技术事实/环境约束，policy=用户偏好/规则，tool_failure=工具失败经验。",
                "不要滥用：单次操作结果、临时上下文、可从代码或对话推导的信息都不要存。",
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
