"""StreamEvent Pydantic schemas.

Corresponds to StreamEvent union type from src/shared/types.ts
"""

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from app.schemas.artifacts import ArtifactRecord
from app.schemas.dispatch import (
    DispatchPlanItem,
    DispatchTaskEndStatus,
    PendingBashCommand,
    PendingDispatchPlan,
    PendingQuestion,
    PendingWrite,
)
from app.schemas.messages import (
    DeployStatusRecord,
    MessageUsage,
    RunUsage,
)
from app.schemas.plan import PlanComplexity, PlanStep


# ─── Base Event ─────────────────────────────────────
class BaseEvent(BaseModel):
    """Base class for all stream events."""

    conversation_id: str = Field(alias="conversationId")
    timestamp: int

    model_config = {"populate_by_name": True}


# ─── Run Events ─────────────────────────────────────
class RunQueuedEvent(BaseEvent):
    """Event when a run is queued waiting for active runs to finish."""

    type: Literal["run.queued"] = "run.queued"
    run_id: str = Field(alias="runId")
    agent_id: str = Field(alias="agentId")
    trigger_message_id: str = Field(alias="triggerMessageId")

    model_config = {"populate_by_name": True}


class RunStartEvent(BaseEvent):
    """Event when a run starts."""

    type: Literal["run.start"] = "run.start"
    run_id: str = Field(alias="runId")
    agent_id: str = Field(alias="agentId")
    trigger_message_id: str = Field(alias="triggerMessageId")
    parent_run_id: str | None = Field(default=None, alias="parentRunId")
    is_resume: bool = Field(default=False, alias="isResume")

    model_config = {"populate_by_name": True}


class RunEndEvent(BaseEvent):
    """Event when a run ends."""

    type: Literal["run.end"] = "run.end"
    run_id: str = Field(alias="runId")
    status: Literal["complete", "failed", "aborted"]
    error: str | None = None
    # Custom ReAct termination metadata (optional; absent for CLI / older clients)
    stop_reason: str | None = Field(default=None, alias="stopReason")
    stop_reason_label: str | None = Field(default=None, alias="stopReasonLabel")

    model_config = {"populate_by_name": True}


class RunUsageEvent(BaseEvent):
    """Event with run token usage."""

    type: Literal["run.usage"] = "run.usage"
    run_id: str = Field(alias="runId")
    usage: RunUsage
    # Carries stop reason from the Custom ReAct loop so consume_stream/finalize
    # can attach it to RunEndEvent (CLI paths leave these None).
    stop_reason: str | None = Field(default=None, alias="stopReason")
    stop_reason_label: str | None = Field(default=None, alias="stopReasonLabel")
    # CLI agent session ID (claude --resume / codex thread). Consumed by
    # consume_stream to persist into AgentRun.cli_session_id for cross-run resume.
    session_id: str | None = Field(default=None, alias="sessionId")

    model_config = {"populate_by_name": True}


# ─── Message Events ─────────────────────────────────────
class MessageRecord(BaseModel):
    """Full message record for events."""

    id: str
    conversation_id: str = Field(alias="conversationId")
    role: Literal["user", "agent", "system"]
    agent_id: str | None = Field(default=None, alias="agentId")
    parts: list[dict]  # Will be parsed to MessagePart list
    status: Literal["streaming", "complete", "error", "aborted", "interrupted"]
    parent_message_id: str | None = Field(default=None, alias="parentMessageId")
    mentioned_agent_ids: list[str] = Field(alias="mentionedAgentIds")
    run_id: str | None = Field(default=None, alias="runId")
    usage: MessageUsage | None = None
    hidden: bool = False
    created_at: int = Field(alias="createdAt")

    model_config = {"populate_by_name": True}


class MessageStartEvent(BaseEvent):
    """Event when a message starts streaming."""

    type: Literal["message.start"] = "message.start"
    message_id: str = Field(alias="messageId")
    agent_id: str = Field(alias="agentId")
    run_id: str = Field(alias="runId")

    model_config = {"populate_by_name": True}


class MessageEndEvent(BaseEvent):
    """Event when a message finishes."""

    type: Literal["message.end"] = "message.end"
    message_id: str = Field(alias="messageId")

    model_config = {"populate_by_name": True}


class MessageUsageEventPayload(BaseEvent):
    """Event with message token usage."""

    type: Literal["message.usage"] = "message.usage"
    message_id: str = Field(alias="messageId")
    usage: MessageUsage

    model_config = {"populate_by_name": True}


class MessageAddedEvent(BaseEvent):
    """Event when a message is added."""

    type: Literal["message.added"] = "message.added"
    message: MessageRecord


class MessageRemovedEvent(BaseEvent):
    """Event when messages are removed."""

    type: Literal["message.removed"] = "message.removed"
    message_ids: list[str] = Field(alias="messageIds")
    artifact_ids: list[str] = Field(alias="artifactIds")

    model_config = {"populate_by_name": True}


# ─── Part Events ─────────────────────────────────────
class PartStartEvent(BaseEvent):
    """Event when a message part starts."""

    type: Literal["part.start"] = "part.start"
    message_id: str = Field(alias="messageId")
    part_index: int = Field(alias="partIndex")
    part: dict  # Will be parsed to MessagePart

    model_config = {"populate_by_name": True}


class PartDeltaEvent(BaseEvent):
    """Event for incremental part updates."""

    type: Literal["part.delta"] = "part.delta"
    message_id: str = Field(alias="messageId")
    part_index: int = Field(alias="partIndex")
    delta: dict  # Will be parsed to PartDelta

    model_config = {"populate_by_name": True}


class PartEndEvent(BaseEvent):
    """Event when a message part finishes."""

    type: Literal["part.end"] = "part.end"
    message_id: str = Field(alias="messageId")
    part_index: int = Field(alias="partIndex")

    model_config = {"populate_by_name": True}


# ─── Tool Events ─────────────────────────────────────
class ToolCallEvent(BaseEvent):
    """Event when a tool is called."""

    type: Literal["tool.call"] = "tool.call"
    message_id: str = Field(alias="messageId")
    call_id: str = Field(alias="callId")
    tool_name: str = Field(alias="toolName")
    args: dict | list | str | None = None

    model_config = {"populate_by_name": True}


class ToolResultEvent(BaseEvent):
    """Event with tool result."""

    type: Literal["tool.result"] = "tool.result"
    message_id: str = Field(alias="messageId")
    call_id: str = Field(alias="callId")
    result: dict | list | str | None = None
    is_error: bool = Field(alias="isError")

    model_config = {"populate_by_name": True}


# ─── Artifact Events ─────────────────────────────────────
class ArtifactCreateEvent(BaseEvent):
    """Event when an artifact is created."""

    type: Literal["artifact.create"] = "artifact.create"
    artifact: ArtifactRecord


class ArtifactUpdateEvent(BaseEvent):
    """Event when an artifact is updated."""

    type: Literal["artifact.update"] = "artifact.update"
    artifact_id: str = Field(alias="artifactId")
    patch: dict  # Partial ArtifactContent

    model_config = {"populate_by_name": True}


# ─── Deploy Events ─────────────────────────────────────
class DeployStatusEvent(BaseEvent):
    """Event with deployment status."""

    type: Literal["deploy.status"] = "deploy.status"
    message_id: str = Field(alias="messageId")
    deployment: DeployStatusRecord

    model_config = {"populate_by_name": True}


# ─── Dispatch Events ─────────────────────────────────────
class DispatchPlanPendingEvent(BaseEvent):
    """Event when a dispatch plan is pending approval."""

    type: Literal["dispatch.plan.pending"] = "dispatch.plan.pending"
    pending_plan: PendingDispatchPlan = Field(alias="pendingPlan")

    model_config = {"populate_by_name": True}


class DispatchPlanResolvedEvent(BaseEvent):
    """Event when a dispatch plan is resolved."""

    type: Literal["dispatch.plan.resolved"] = "dispatch.plan.resolved"
    pending_id: str = Field(alias="pendingId")
    run_id: str = Field(alias="runId")
    approved: bool
    revising: bool | None = None

    model_config = {"populate_by_name": True}


class DispatchPlanEvent(BaseEvent):
    """Event with approved dispatch plan."""

    type: Literal["dispatch.plan"] = "dispatch.plan"
    run_id: str = Field(alias="runId")
    plan: list[DispatchPlanItem]

    model_config = {"populate_by_name": True}


class DispatchStartEvent(BaseEvent):
    """Event when a dispatch task starts."""

    type: Literal["dispatch.start"] = "dispatch.start"
    parent_run_id: str = Field(alias="parentRunId")
    child_run_id: str = Field(alias="childRunId")
    task_id: str = Field(alias="taskId")
    agent_id: str = Field(alias="agentId")

    model_config = {"populate_by_name": True}


class DispatchEndEvent(BaseEvent):
    """Event when a dispatch task ends."""

    type: Literal["dispatch.end"] = "dispatch.end"
    parent_run_id: str = Field(alias="parentRunId")
    child_run_id: str | None = Field(default=None, alias="childRunId")
    task_id: str = Field(alias="taskId")
    status: DispatchTaskEndStatus
    error: str | None = None

    model_config = {"populate_by_name": True}


class DispatchRetryEvent(BaseEvent):
    """Event when a dispatch task is retried by the harness loop."""

    type: Literal["dispatch.retry"] = "dispatch.retry"
    parent_run_id: str = Field(alias="parentRunId")
    task_id: str = Field(alias="taskId")
    attempt: int
    max_attempts: int = Field(alias="maxAttempts")
    error: str | None = None

    model_config = {"populate_by_name": True}


# ─── Approval Events ─────────────────────────────────────
class FsWritePendingEvent(BaseEvent):
    """Event when a file write is pending approval."""

    type: Literal["fs_write.pending"] = "fs_write.pending"
    pending_write: PendingWrite = Field(alias="pendingWrite")

    model_config = {"populate_by_name": True}


class FsWriteResolvedEvent(BaseEvent):
    """Event when a file write is resolved."""

    type: Literal["fs_write.resolved"] = "fs_write.resolved"
    pending_id: str = Field(alias="pendingId")
    applied: bool

    model_config = {"populate_by_name": True}


class BashCommandPendingEvent(BaseEvent):
    """Event when a bash command is pending approval."""

    type: Literal["bash_command.pending"] = "bash_command.pending"
    pending_command: PendingBashCommand = Field(alias="pendingCommand")

    model_config = {"populate_by_name": True}


class BashCommandResolvedEvent(BaseEvent):
    """Event when a bash command is resolved."""

    type: Literal["bash_command.resolved"] = "bash_command.resolved"
    pending_id: str = Field(alias="pendingId")
    approved: bool

    model_config = {"populate_by_name": True}


class AskUserPendingEvent(BaseEvent):
    """Event when a question is pending user answer."""

    type: Literal["ask_user.pending"] = "ask_user.pending"
    pending_question: PendingQuestion = Field(alias="pendingQuestion")

    model_config = {"populate_by_name": True}


class AskUserResolvedEvent(BaseEvent):
    """Event when a question is answered."""

    type: Literal["ask_user.resolved"] = "ask_user.resolved"
    pending_id: str = Field(alias="pendingId")
    answered: bool

    model_config = {"populate_by_name": True}


# ─── MCP Call Approval Events ─────────────────────────────────────
class PendingMcpCall(BaseModel):
    """A pending MCP tool call awaiting user approval."""

    id: str
    conversation_id: str = Field(alias="conversationId")
    agent_id: str = Field(alias="agentId")
    run_id: str = Field(alias="runId")
    tool_name: str = Field(alias="toolName")
    args: dict
    server_trust: str = Field(alias="serverTrust")
    created_at: int = Field(alias="createdAt")

    model_config = {"populate_by_name": True}


class McpCallPendingEvent(BaseEvent):
    """Event when an MCP tool call is pending approval."""

    type: Literal["mcp_call.pending"] = "mcp_call.pending"
    pending_call: PendingMcpCall = Field(alias="pendingCall")

    model_config = {"populate_by_name": True}


class McpCallResolvedEvent(BaseEvent):
    """Event when an MCP tool call is resolved."""

    type: Literal["mcp_call.resolved"] = "mcp_call.resolved"
    pending_id: str = Field(alias="pendingId")
    approved: bool

    model_config = {"populate_by_name": True}


# ─── Worktree Events ─────────────────────────────────────
class WorktreeEvent(BaseEvent):
    """Event when a dispatch task worktree is created/merged/cleaned."""

    type: Literal["worktree.created", "worktree.merged", "worktree.cleaned"]
    task_id: str = Field(alias="taskId")
    branch_name: str | None = Field(default=None, alias="branchName")
    path: str | None = None
    merge_status: Literal["success", "conflict"] | None = Field(default=None, alias="mergeStatus")
    conflict_files: list[str] | None = Field(default=None, alias="conflictFiles")
    resolution_status: Literal[
        "success", "llm_resolved", "manual_resolved", "abandoned", "conflict"
    ] | None = Field(default=None, alias="resolutionStatus")

    model_config = {"populate_by_name": True}


# ─── Merge Conflict Approval Events ─────────────────────────────
class MergeConflictPendingEvent(BaseEvent):
    """Event when a merge conflict is pending human approval (Layer 3)."""

    type: Literal["merge_conflict.pending"] = "merge_conflict.pending"
    pending_id: str = Field(alias="pendingId")
    task_id: str = Field(alias="taskId")
    conflict_files: list[str] = Field(alias="conflictFiles")
    workspace_path: str = Field(alias="workspacePath")

    model_config = {"populate_by_name": True}


class MergeConflictResolvedEvent(BaseEvent):
    """Event when a merge conflict has been resolved."""

    type: Literal["merge_conflict.resolved"] = "merge_conflict.resolved"
    pending_id: str = Field(alias="pendingId")
    resolution_strategy: str = Field(alias="resolutionStrategy")
    resolved_files: list[str] = Field(default_factory=list, alias="resolvedFiles")

    model_config = {"populate_by_name": True}


# ─── Heartbeat Event ─────────────────────────────────────
class HeartbeatEvent(BaseEvent):
    """Heartbeat event to keep SSE connection alive."""

    type: Literal["heartbeat"] = "heartbeat"


# ─── Turn Metric Event ─────────────────────────────────────
class TurnTokenBreakdown(BaseModel):
    """Per-turn token breakdown."""

    input_tokens: int = Field(alias="inputTokens")
    output_tokens: int = Field(alias="outputTokens")
    cache_read_tokens: int = Field(alias="cacheReadTokens")

    model_config = {"populate_by_name": True}


class TurnMetricEvent(BaseEvent):
    """Per-turn metrics: token usage, tool calls, and duration."""

    type: Literal["turn.metric"] = "turn.metric"
    run_id: str = Field(alias="runId")
    turn: int  # 1-based
    tokens: TurnTokenBreakdown
    tool_calls: list[str] = Field(alias="toolCalls")
    duration_ms: int = Field(alias="durationMs")

    model_config = {"populate_by_name": True}


# ─── Summary Updated Event ─────────────────────────────────────
class SummaryUpdatedEvent(BaseEvent):
    """Event when a conversation summary is updated."""

    type: Literal["summary.updated"] = "summary.updated"
    summary: str | None = None


# ─── Plan Events ─────────────────────────────────────
class PlanCreatedEvent(BaseEvent):
    """Event when an execution plan is created via create_plan tool."""

    type: Literal["plan.created"] = "plan.created"
    plan_id: str = Field(alias="planId")
    steps: list[PlanStep]
    complexity: PlanComplexity

    model_config = {"populate_by_name": True}


class PlanStepUpdateEvent(BaseEvent):
    """Event when plan step status changes (via plan_step / add_plan_steps tools or run-end cleanup)."""

    type: Literal["plan.step_update"] = "plan.step_update"
    plan_id: str = Field(alias="planId")
    steps: list[PlanStep]

    model_config = {"populate_by_name": True}


# ─── File Write Preview Events ─────────────────────────────────────
class FileWritePreviewCompleteEvent(BaseEvent):
    """Event when fs_write/fs_edit tool execution completes with diff data."""

    type: Literal["file_write_preview.complete"] = "file_write_preview.complete"
    message_id: str = Field(alias="messageId")
    call_id: str = Field(alias="callId")
    path: str
    old_content: str | None = Field(default=None, alias="oldContent")
    new_content: str | None = Field(default=None, alias="newContent")
    status: Literal["complete", "failed"]

    model_config = {"populate_by_name": True}


# ─── Workspace Env Events ─────────────────────────────────────────────────
# See specs/workspace-env-isolation. These events drive the frontend env hint
# card that prompts the user to create a project venv when a Python project
# is bound without one.

class WorkspaceEnvHintEvent(BaseEvent):
    """Notify the frontend that a Python project without a venv needs a decision.

    The frontend renders a banner card with three options: create a .venv,
    skip, or use system Python. The user's choice is persisted to
    ``Workspace.env_preference`` so the hint is not shown again.
    """

    type: Literal["workspace_env_hint"] = "workspace_env_hint"
    language: Literal["python"] = "python"
    venv_present: bool = Field(alias="venvPresent")
    options: list[Literal["create", "skip", "system_python"]] = Field(
        default_factory=lambda: ["create", "skip", "system_python"]
    )

    model_config = {"populate_by_name": True}


class WorkspaceEnvStatusEvent(BaseEvent):
    """Report progress / result of a venv creation request.

    - ``status='creating'``  — ``python -m venv`` started.
    - ``status='ready'``     — venv created; ``venv_path`` carries the path.
    - ``status='failed'``    — venv creation failed; ``error`` carries the msg.
    """

    type: Literal["workspace_env_status"] = "workspace_env_status"
    status: Literal["creating", "ready", "failed"]
    venv_path: str | None = Field(default=None, alias="venvPath")
    error: str | None = None

    model_config = {"populate_by_name": True}


# ─── Guide Side Effect Event ─────────────────────────────────
class GuideSideEffectEvent(BaseEvent):
    """Event when a guide agent management tool causes a side effect.

    The frontend uses the ``target`` field to determine which panel to refresh.
    """

    type: Literal["guide_side_effect"] = "guide_side_effect"
    target: Literal["agents", "skills", "mcp", "documents", "memory", "profile", "conversations"]
    action: Literal["create", "update", "delete", "refresh", "optimize", "consolidate", "upload"]
    payload: dict | None = None

    model_config = {"populate_by_name": True}


# ─── Union Type ─────────────────────────────────────
StreamEvent = Annotated[
    Union[  # noqa: UP007 - keep Union[] for the Pydantic discriminated union
        # Run events
        RunQueuedEvent,
        RunStartEvent,
        RunEndEvent,
        RunUsageEvent,
        # Message events
        MessageStartEvent,
        MessageEndEvent,
        MessageUsageEventPayload,
        MessageAddedEvent,
        MessageRemovedEvent,
        # Part events
        PartStartEvent,
        PartDeltaEvent,
        PartEndEvent,
        # Tool events
        ToolCallEvent,
        ToolResultEvent,
        # Artifact events
        ArtifactCreateEvent,
        ArtifactUpdateEvent,
        # Deploy events
        DeployStatusEvent,
        # Dispatch events
        DispatchPlanPendingEvent,
        DispatchPlanResolvedEvent,
        DispatchPlanEvent,
        DispatchStartEvent,
        DispatchEndEvent,
        DispatchRetryEvent,
        # Approval events
        FsWritePendingEvent,
        FsWriteResolvedEvent,
        BashCommandPendingEvent,
        BashCommandResolvedEvent,
        AskUserPendingEvent,
        AskUserResolvedEvent,
        # MCP call approval events
        McpCallPendingEvent,
        McpCallResolvedEvent,
        # Worktree events
        WorktreeEvent,
        # Merge conflict approval events
        MergeConflictPendingEvent,
        MergeConflictResolvedEvent,
        # Heartbeat
        HeartbeatEvent,
        # Turn metrics
        TurnMetricEvent,
        # Summary
        SummaryUpdatedEvent,
        # Plan events
        PlanCreatedEvent,
        PlanStepUpdateEvent,
        # File write preview events
        FileWritePreviewCompleteEvent,
        # Workspace env events
        WorkspaceEnvHintEvent,
        WorkspaceEnvStatusEvent,
        # Guide side effect events
        GuideSideEffectEvent,
    ],
    Field(discriminator="type"),
]
