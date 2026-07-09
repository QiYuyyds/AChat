"""Dispatch-related Pydantic schemas.

Corresponds to DispatchPlanItem and pending-action types from src/shared/types.ts.
Legacy verification schemas (DispatchExpectedOutput, DispatchRequiredCommand,
TaskResultReport and related evidence types) have been removed — the Unified
Agent Loop no longer uses verification gates or task result reports.
"""

from typing import Literal

from pydantic import BaseModel, Field

# ─── Dispatch Types ─────────────────────────────────────
DispatchTaskKind = Literal["code", "test", "review", "design", "doc", "analysis"]
DispatchTaskStatus = Literal["pending", "running", "complete", "failed", "aborted", "skipped", "merge_conflict"]
DispatchTaskEndStatus = Literal["complete", "failed", "aborted", "skipped", "merge_conflict"]


class DispatchTaskInput(BaseModel):
    """Input from another task."""

    from_task_id: str = Field(alias="fromTaskId")
    output_id: str = Field(alias="outputId")
    required: bool | None = None
    description: str | None = None

    model_config = {"populate_by_name": True}


class DispatchPlanItem(BaseModel):
    """A single task in a dispatch plan."""

    id: str
    agent_id: str = Field(alias="agentId")
    task: str
    task_kind: DispatchTaskKind | None = Field(default=None, alias="taskKind")
    depends_on: list[str] | None = Field(default=None, alias="dependsOn")
    inputs: list[DispatchTaskInput] | None = None
    acceptance_criteria: list[str] | None = Field(
        default=None, alias="acceptanceCriteria"
    )
    target_paths: list[str] | None = Field(default=None, alias="targetPaths")
    expected_workspace_changes: list[str] | None = Field(
        default=None, alias="expectedWorkspaceChanges"
    )
    # advisory fields
    complexity: str | None = None
    explored: list[str] | None = None
    # advisory context level — controls sub-agent context amount
    context_level: Literal["isolated", "standard"] | None = Field(
        default=None, alias="contextLevel"
    )

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
