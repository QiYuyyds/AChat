"""Message-related Pydantic schemas.

Corresponds to MessagePart, PartDelta types from src/shared/types.ts
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field


# ─── DeployStatusRecord ─────────────────────────────────────
class DeployStatusRecord(BaseModel):
    """Deployment status record."""

    id: str
    artifact_id: str = Field(alias="artifactId")
    title: str
    version: int
    preview_path: str = Field(alias="previewPath")
    status: Literal["ready", "failed"]
    source_type: Literal["artifact", "workspace"] | None = Field(
        default=None, alias="sourceType"
    )
    workspace_path: str | None = Field(default=None, alias="workspacePath")
    deployment_type: Literal["local_static", "external_static"] | None = Field(
        default=None, alias="deploymentType"
    )
    deployment_path: str | None = Field(default=None, alias="deploymentPath")
    local_preview_path: str | None = Field(default=None, alias="localPreviewPath")
    public_url: str | None = Field(default=None, alias="publicUrl")
    publish_path: str | None = Field(default=None, alias="publishPath")
    publish_target_type: Literal["static_directory"] | None = Field(
        default=None, alias="publishTargetType"
    )
    source_download_path: str | None = Field(default=None, alias="sourceDownloadPath")
    container_download_path: str | None = Field(
        default=None, alias="containerDownloadPath"
    )
    summary_instruction: str | None = Field(default=None, alias="summaryInstruction")
    error: str | None = None
    created_at: int = Field(alias="createdAt")

    model_config = {"populate_by_name": True}


class DeployCandidateRecord(BaseModel):
    """Deploy candidate record."""

    artifact_id: str = Field(alias="artifactId")
    title: str
    version: int
    created_by_agent_id: str = Field(alias="createdByAgentId")
    created_at: int = Field(alias="createdAt")

    model_config = {"populate_by_name": True}


# ─── MessagePart Union Types ─────────────────────────────────
class TextPart(BaseModel):
    """Text message part."""

    type: Literal["text"] = "text"
    content: str


class CodePart(BaseModel):
    """Code message part."""

    type: Literal["code"] = "code"
    language: str
    content: str


class ThinkingPart(BaseModel):
    """Thinking/reasoning message part."""

    type: Literal["thinking"] = "thinking"
    content: str


class ToolUsePart(BaseModel):
    """Tool use/call message part."""

    type: Literal["tool_use"] = "tool_use"
    call_id: str = Field(alias="callId")
    tool_name: str = Field(alias="toolName")
    args: dict | list | str | None = None

    model_config = {"populate_by_name": True}


class ToolResultPart(BaseModel):
    """Tool result message part."""

    type: Literal["tool_result"] = "tool_result"
    call_id: str = Field(alias="callId")
    result: dict | list | str | None = None
    is_error: bool = Field(alias="isError")

    model_config = {"populate_by_name": True}


class ArtifactRefPart(BaseModel):
    """Artifact reference message part."""

    type: Literal["artifact_ref"] = "artifact_ref"
    artifact_id: str = Field(alias="artifactId")

    model_config = {"populate_by_name": True}


class DeployStatusPart(BaseModel):
    """Deploy status message part."""

    type: Literal["deploy_status"] = "deploy_status"
    deployment: DeployStatusRecord


class DeployCandidatesPart(BaseModel):
    """Deploy candidates message part."""

    type: Literal["deploy_candidates"] = "deploy_candidates"
    candidates: list[DeployCandidateRecord]


class ImageAttachmentPart(BaseModel):
    """Image attachment message part."""

    type: Literal["image_attachment"] = "image_attachment"
    attachment_id: str = Field(alias="attachmentId")
    file_name: str = Field(alias="fileName")
    size: int
    mime_type: str = Field(alias="mimeType")

    model_config = {"populate_by_name": True}


class FileAttachmentPart(BaseModel):
    """File attachment message part."""

    type: Literal["file_attachment"] = "file_attachment"
    attachment_id: str = Field(alias="attachmentId")
    file_name: str = Field(alias="fileName")
    size: int
    mime_type: str = Field(alias="mimeType")

    model_config = {"populate_by_name": True}


# Union type for all message parts
MessagePart = Annotated[
    TextPart | CodePart | ThinkingPart | ToolUsePart | ToolResultPart | ArtifactRefPart | DeployStatusPart | DeployCandidatesPart | ImageAttachmentPart | FileAttachmentPart,
    Field(discriminator="type"),
]


# ─── PartDelta Types ─────────────────────────────────────
class TextAppendDelta(BaseModel):
    """Text append delta."""

    type: Literal["text.append"] = "text.append"
    text: str


class CodeAppendDelta(BaseModel):
    """Code append delta."""

    type: Literal["code.append"] = "code.append"
    text: str


class ThinkingAppendDelta(BaseModel):
    """Thinking append delta."""

    type: Literal["thinking.append"] = "thinking.append"
    text: str


# Union type for all part deltas
PartDelta = Annotated[
    TextAppendDelta | CodeAppendDelta | ThinkingAppendDelta,
    Field(discriminator="type"),
]


# ─── Message Usage ─────────────────────────────────────
class MessageUsage(BaseModel):
    """Per-message token usage."""

    input_tokens: int = Field(alias="inputTokens")
    output_tokens: int = Field(alias="outputTokens")
    cache_read_tokens: int = Field(alias="cacheReadTokens")

    model_config = {"populate_by_name": True}


class RunUsage(BaseModel):
    """Per-run token usage."""

    input_tokens: int = Field(alias="inputTokens")
    output_tokens: int = Field(alias="outputTokens")
    cache_creation_tokens: int = Field(alias="cacheCreationTokens")
    cache_read_tokens: int = Field(alias="cacheReadTokens")
    last_input_tokens: int | None = Field(default=None, alias="lastInputTokens")
    model: str | None = None

    model_config = {"populate_by_name": True}
