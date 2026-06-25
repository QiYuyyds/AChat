"""Dispatch/Orchestrator-related Pydantic schemas.

Corresponds to DispatchPlanItem, TaskResultReport types from src/shared/types.ts
"""

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.artifacts import DispatchExpectedOutputType

# ─── Dispatch Types ─────────────────────────────────────
DispatchTaskKind = Literal["code", "test", "review", "design", "doc", "analysis"]
DispatchTaskStatus = Literal["pending", "running", "complete", "failed", "aborted", "skipped"]
DispatchTaskEndStatus = Literal["complete", "failed", "aborted", "skipped"]
TaskResultReportStatus = Literal["complete", "failed", "blocked"]


class DispatchExpectedOutput(BaseModel):
    """Expected output from a dispatch task."""

    id: str
    type: DispatchExpectedOutputType
    required: bool | None = None
    description: str | None = None


class DispatchTaskInput(BaseModel):
    """Input from another task."""

    from_task_id: str = Field(alias="fromTaskId")
    output_id: str = Field(alias="outputId")
    required: bool | None = None
    description: str | None = None

    model_config = {"populate_by_name": True}


class DispatchRequiredCommand(BaseModel):
    """Required command to run for verification."""

    command: str
    description: str | None = None
    cwd: str | None = None
    timeout_ms: int | None = Field(default=None, alias="timeoutMs")

    model_config = {"populate_by_name": True}


class DispatchPlanItem(BaseModel):
    """A single task in a dispatch plan."""

    id: str
    agent_id: str = Field(alias="agentId")
    task: str
    task_kind: DispatchTaskKind | None = Field(default=None, alias="taskKind")
    depends_on: list[str] | None = Field(default=None, alias="dependsOn")
    expected_outputs: list[DispatchExpectedOutput] | None = Field(
        default=None, alias="expectedOutputs"
    )
    inputs: list[DispatchTaskInput] | None = None
    acceptance_criteria: list[str] | None = Field(
        default=None, alias="acceptanceCriteria"
    )
    target_paths: list[str] | None = Field(default=None, alias="targetPaths")
    expected_workspace_changes: list[str] | None = Field(
        default=None, alias="expectedWorkspaceChanges"
    )
    required_commands: list[DispatchRequiredCommand] | None = Field(
        default=None, alias="requiredCommands"
    )
    required_evidence: list[str] | None = Field(default=None, alias="requiredEvidence")

    model_config = {"populate_by_name": True}


# ─── Task Result Report ─────────────────────────────────────
class TaskAcceptanceResult(BaseModel):
    """Result of an acceptance criterion check."""

    criterion: str
    passed: bool
    evidence: str


class TaskFileEvidence(BaseModel):
    """File-related evidence from task execution."""

    path: str
    action: Literal["created", "modified", "deleted", "verified"] | None = None


class TaskCommandEvidence(BaseModel):
    """Command execution evidence from task."""

    command: str
    exit_code: int | None = Field(alias="exitCode")
    cwd: str | None = None
    timed_out: bool | None = Field(default=None, alias="timedOut")
    summary: str | None = None

    model_config = {"populate_by_name": True}


class TaskTestEvidence(BaseModel):
    """Test execution evidence from task."""

    command: str
    passed: bool
    summary: str | None = None


class TaskResultReport(BaseModel):
    """Report from a completed task."""

    status: TaskResultReportStatus
    summary: str
    acceptance_results: list[TaskAcceptanceResult] | None = Field(
        default=None, alias="acceptanceResults"
    )
    files_changed: list[TaskFileEvidence] | None = Field(
        default=None, alias="filesChanged"
    )
    commands_run: list[TaskCommandEvidence] | None = Field(
        default=None, alias="commandsRun"
    )
    tests: list[TaskTestEvidence] | None = None
    blockers: list[str] | None = None

    model_config = {"populate_by_name": True}


# ─── Pending Items ─────────────────────────────────────
class AskUserOption(BaseModel):
    """Option for ask_user question."""

    label: str
    description: str | None = None
    preview: str | None = None


class AskUserQuestionItem(BaseModel):
    """Question item for ask_user."""

    question: str
    header: str
    options: list[AskUserOption]
    multi_select: bool | None = Field(default=False, alias="multiSelect")

    model_config = {"populate_by_name": True}


class AskUserAnswer(BaseModel):
    """Answer to an ask_user question."""

    selected_labels: list[str] = Field(alias="selectedLabels")
    freeform_note: str | None = Field(default=None, alias="freeformNote")

    model_config = {"populate_by_name": True}


class PendingWrite(BaseModel):
    """Pending file write awaiting approval."""

    id: str
    conversation_id: str = Field(alias="conversationId")
    agent_id: str = Field(alias="agentId")
    run_id: str = Field(alias="runId")
    path: str
    absolute_path: str = Field(alias="absolutePath")
    old_content: str | None = Field(alias="oldContent")
    new_content: str = Field(alias="newContent")
    created_at: int = Field(alias="createdAt")

    model_config = {"populate_by_name": True}


class PendingQuestion(BaseModel):
    """Pending question awaiting user answer."""

    id: str
    conversation_id: str = Field(alias="conversationId")
    agent_id: str = Field(alias="agentId")
    run_id: str = Field(alias="runId")
    questions: list[AskUserQuestionItem]
    created_at: int = Field(alias="createdAt")

    model_config = {"populate_by_name": True}


class PendingDispatchPlan(BaseModel):
    """Pending dispatch plan awaiting approval."""

    id: str
    conversation_id: str = Field(alias="conversationId")
    agent_id: str = Field(alias="agentId")
    run_id: str = Field(alias="runId")
    plan: list[DispatchPlanItem]
    created_at: int = Field(alias="createdAt")

    model_config = {"populate_by_name": True}


class PendingBashCommand(BaseModel):
    """Pending bash command awaiting approval."""

    id: str
    conversation_id: str = Field(alias="conversationId")
    agent_id: str = Field(alias="agentId")
    run_id: str = Field(alias="runId")
    command: str
    cwd: str
    reason: str
    created_at: int = Field(alias="createdAt")

    model_config = {"populate_by_name": True}
