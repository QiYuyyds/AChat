"""RagTask Pydantic schemas — camelCase aliases for frontend compatibility."""

from typing import Any, Literal

from pydantic import BaseModel, Field

RagTaskType = Literal["parse", "ingest", "graph_build", "delete_cleanup"]
RagTaskStatus = Literal["pending", "running", "completed", "failed", "failed_permanent"]


class RagTaskResponse(BaseModel):
    """Full RagTask row — used in API responses."""

    id: str
    user_id: str = Field(alias="userId")
    task_type: RagTaskType = Field(alias="taskType")
    document_id: str | None = Field(default=None, alias="documentId")
    version_id: str | None = Field(default=None, alias="versionId")
    status: RagTaskStatus
    payload: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    error_message: str | None = Field(default=None, alias="errorMessage")
    retry_count: int = Field(alias="retryCount")
    max_retries: int = Field(alias="maxRetries")
    created_at: float = Field(alias="createdAt")
    updated_at: float = Field(alias="updatedAt")
    started_at: float | None = Field(default=None, alias="startedAt")
    completed_at: float | None = Field(default=None, alias="completedAt")

    model_config = {"populate_by_name": True}


class RagTaskListResponse(BaseModel):
    """List of RagTask items."""

    tasks: list[RagTaskResponse]


class CreateRagTaskRequest(BaseModel):
    """Request to manually create a RAG task (optional — upload_file auto-creates)."""

    task_type: RagTaskType = Field(alias="taskType")
    document_id: str | None = Field(default=None, alias="documentId")
    version_id: str | None = Field(default=None, alias="versionId")
    payload: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}
