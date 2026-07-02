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


# ─── Base Event ─────────────────────────────────────
class BaseEvent(BaseModel):
    """Base class for all stream events."""

    conversation_id: str = Field(alias="conversationId")
    timestamp: int

    model_config = {"populate_by_name": True}


# ─── Run Events ─────────────────────────────────────
class RunStartEvent(BaseEvent):
    """Event when a run starts."""

    type: Literal["run.start"] = "run.start"
    run_id: str = Field(alias="runId")
    agent_id: str = Field(alias="agentId")
    trigger_message_id: str = Field(alias="triggerMessageId")
    parent_run_id: str | None = Field(default=None, alias="parentRunId")

    model_config = {"populate_by_name": True}


class RunEndEvent(BaseEvent):
    """Event when a run ends."""

    type: Literal["run.end"] = "run.end"
    run_id: str = Field(alias="runId")
    status: Literal["complete", "failed", "aborted"]
    error: str | None = None

    model_config = {"populate_by_name": True}


class RunUsageEvent(BaseEvent):
    """Event with run token usage."""

    type: Literal["run.usage"] = "run.usage"
    run_id: str = Field(alias="runId")
    usage: RunUsage

    model_config = {"populate_by_name": True}


# ─── Message Events ─────────────────────────────────────
class MessageRecord(BaseModel):
    """Full message record for events."""

    id: str
    conversation_id: str = Field(alias="conversationId")
    role: Literal["user", "agent", "system"]
    agent_id: str | None = Field(default=None, alias="agentId")
    parts: list[dict]  # Will be parsed to MessagePart list
    status: Literal["streaming", "complete", "error", "aborted"]
    parent_message_id: str | None = Field(default=None, alias="parentMessageId")
    mentioned_agent_ids: list[str] = Field(alias="mentionedAgentIds")
    run_id: str | None = Field(default=None, alias="runId")
    usage: MessageUsage | None = None
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


# ─── Heartbeat Event ─────────────────────────────────────
class HeartbeatEvent(BaseEvent):
    """Heartbeat event to keep SSE connection alive."""

    type: Literal["heartbeat"] = "heartbeat"


# ─── Summary Updated Event ─────────────────────────────────────
class SummaryUpdatedEvent(BaseEvent):
    """Event when a conversation summary is updated."""

    type: Literal["summary.updated"] = "summary.updated"
    summary: str | None = None


# ─── Union Type ─────────────────────────────────────
StreamEvent = Annotated[
    Union[  # noqa: UP007 - keep Union[] for the Pydantic discriminated union
        # Run events
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
        # Heartbeat
        HeartbeatEvent,
        # Summary
        SummaryUpdatedEvent,
    ],
    Field(discriminator="type"),
]
