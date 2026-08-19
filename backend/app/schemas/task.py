"""Task and TaskComment Pydantic schemas.

camelCase field aliases for frontend compatibility.
"""

from typing import Literal

from pydantic import BaseModel, Field

TaskStatus = Literal[
    "backlog", "todo", "in_progress", "in_review", "done", "blocked", "canceled"
]
TaskPriority = Literal["none", "urgent", "high", "medium", "low"]
TaskCreatorType = Literal["user", "agent"]
TaskCommentAuthorType = Literal["user", "agent"]
TaskWorkspaceMode = Literal["sandbox", "local", None]


class TaskRow(BaseModel):
    """Full Task row — used in API responses and SSE events."""

    id: str
    user_id: str = Field(alias="userId")
    title: str
    description: str = ""
    status: TaskStatus
    priority: TaskPriority
    labels: list[str] = Field(default_factory=list)
    assignee_agent_id: str | None = Field(default=None, alias="assigneeAgentId")

    creator_type: TaskCreatorType = Field(alias="creatorType")
    creator_id: str = Field(alias="creatorId")
    creator_name: str = Field(alias="creatorName")

    conversation_id: str | None = Field(default=None, alias="conversationId")

    workspace_mode: TaskWorkspaceMode = Field(default=None, alias="workspaceMode")
    workspace_path: str | None = Field(default=None, alias="workspacePath")

    version: int
    failure_count: int = Field(default=0, alias="failureCount")
    sort_order: int = Field(default=0, alias="sortOrder")
    due_date: str | None = Field(default=None, alias="dueDate")

    created_at: int = Field(alias="createdAt")
    updated_at: int = Field(alias="updatedAt")
    completed_at: int | None = Field(default=None, alias="completedAt")

    model_config = {"populate_by_name": True}


class TaskCommentRow(BaseModel):
    """Full TaskComment row."""

    id: str
    task_id: str = Field(alias="taskId")
    user_id: str = Field(alias="userId")
    body: str

    author_type: TaskCommentAuthorType = Field(alias="authorType")
    author_id: str = Field(alias="authorId")
    author_name: str = Field(alias="authorName")

    version: int
    created_at: int = Field(alias="createdAt")
    updated_at: int = Field(alias="updatedAt")

    model_config = {"populate_by_name": True}


# ─── Request models ─────────────────────────────────────


class CreateTaskRequest(BaseModel):
    title: str
    description: str = ""
    status: TaskStatus = "todo"
    priority: TaskPriority = "none"
    labels: list[str] = Field(default_factory=list)
    assignee_agent_id: str | None = Field(default=None, alias="assigneeAgentId")
    workspace_mode: TaskWorkspaceMode = Field(default=None, alias="workspaceMode")
    workspace_path: str | None = Field(default=None, alias="workspacePath")
    due_date: str | None = Field(default=None, alias="dueDate")

    model_config = {"populate_by_name": True}


class UpdateTaskRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    priority: TaskPriority | None = None
    labels: list[str] | None = None
    due_date: str | None = None
    if_version: int = Field(alias="ifVersion")

    model_config = {"populate_by_name": True}


class MoveTaskRequest(BaseModel):
    status: TaskStatus
    if_version: int = Field(alias="ifVersion")
    sort_order: int | None = Field(default=None, alias="sortOrder")

    model_config = {"populate_by_name": True}


class AssignTaskRequest(BaseModel):
    agent_id: str | None = Field(alias="agentId")
    if_version: int = Field(alias="ifVersion")

    model_config = {"populate_by_name": True}


class AddTaskCommentRequest(BaseModel):
    body: str
    author_type: TaskCommentAuthorType = Field(default="user", alias="authorType")
    author_id: str = Field(default="", alias="authorId")
    author_name: str = Field(default="", alias="authorName")

    model_config = {"populate_by_name": True}


class SchedulerStartRequest(BaseModel):
    agent_id: str | None = Field(default=None, alias="agentId")
    interval_minutes: int = Field(default=5, alias="intervalMinutes")
    max_concurrent: int = Field(default=3, alias="maxConcurrent")

    model_config = {"populate_by_name": True}
