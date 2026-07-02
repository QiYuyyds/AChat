"""API request and response Pydantic schemas."""

from typing import Any, Literal

from pydantic import BaseModel, Field


# ─── Conversation Requests ─────────────────────────────────────
class CreateConversationRequest(BaseModel):
    """Request to create a new conversation."""

    title: str | None = None
    mode: Literal["single", "group"]
    agent_ids: list[str] = Field(alias="agentIds", min_length=1)
    bound_path: str | None = Field(default=None, alias="boundPath")

    model_config = {"populate_by_name": True}


class UpdateConversationRequest(BaseModel):
    """Request to update a conversation."""

    title: str | None = None
    summary: str | None = None
    add_agent_ids: list[str] | None = Field(default=None, alias="addAgentIds")
    fs_write_approval_mode: Literal["auto", "review"] | None = Field(
        default=None, alias="fsWriteApprovalMode"
    )
    toggle_pin: bool | None = Field(default=None, alias="togglePin")
    toggle_archive: bool | None = Field(default=None, alias="toggleArchive")

    model_config = {"populate_by_name": True}


class ConversationResponse(BaseModel):
    """Response containing a conversation."""

    id: str
    title: str
    mode: Literal["single", "group"]
    agent_ids: list[str] = Field(alias="agentIds")
    pinned_message_ids: list[str] = Field(alias="pinnedMessageIds")
    bookmarked_message_ids: list[str] = Field(alias="bookmarkedMessageIds")
    archived: bool
    pinned_at: int | None = Field(alias="pinnedAt")
    fs_write_approval_mode: Literal["auto", "review"] = Field(alias="fsWriteApprovalMode")
    rag_enabled: bool = Field(alias="ragEnabled")
    summary: str | None = None
    created_at: int = Field(alias="createdAt")
    updated_at: int = Field(alias="updatedAt")
    workspace_mode: Literal["sandbox", "local"] = Field(alias="workspaceMode")
    workspace_bound_path: str | None = Field(alias="workspaceBoundPath")

    model_config = {"populate_by_name": True}


# ─── Message Requests ─────────────────────────────────────
class SendMessageRequest(BaseModel):
    """Request to send a message."""

    content: str
    mentioned_agent_ids: list[str] | None = Field(default=None, alias="mentionedAgentIds")
    parent_message_id: str | None = Field(default=None, alias="parentMessageId")
    attachment_ids: list[str] | None = Field(default=None, alias="attachmentIds")

    model_config = {"populate_by_name": True}


class EditMessageRequest(BaseModel):
    """Request to edit a message."""

    content: str


class SendMessageResponse(BaseModel):
    """Response after sending a message."""

    message_id: str = Field(alias="messageId")
    run_ids: list[str] = Field(alias="runIds")

    model_config = {"populate_by_name": True}


class SetRagModeRequest(BaseModel):
    """Request to set conversation RAG mode."""

    rag_enabled: bool = Field(alias="ragEnabled")

    model_config = {"populate_by_name": True}


# ─── Agent Requests ─────────────────────────────────────
class CreateAgentRequest(BaseModel):
    """Request to create a new agent."""

    name: str = Field(min_length=1, max_length=64)
    avatar: str | None = Field(default=None, max_length=8)
    description: str = Field(min_length=1, max_length=280)
    capabilities: list[str] | None = None
    system_prompt: str = Field(alias="systemPrompt")
    adapter_name: Literal["custom", "claude-code", "codex"] = Field(alias="adapterName")

    model_provider: Literal[
        "anthropic", "openai", "deepseek", "volcano-ark", "openai-compatible"
    ] | None = Field(default=None, alias="modelProvider")
    model_id: str | None = Field(default=None, alias="modelId")
    api_key: str | None = Field(default=None, alias="apiKey")
    api_base_url: str | None = Field(default=None, alias="apiBaseUrl")

    tool_names: list[str] | None = Field(default=None, alias="toolNames")
    skill_names: list[str] | None = Field(default=None, alias="skillNames")
    supports_vision: bool | None = Field(default=None, alias="supportsVision")
    is_orchestrator: bool | None = Field(default=False, alias="isOrchestrator")

    # ── CLI agent fields ──────────────────────────────────────
    executable_path: str | None = Field(default=None, alias="executablePath")
    protocol_family: str | None = Field(default=None, alias="protocolFamily")
    custom_args: list[str] | None = Field(default=None, alias="customArgs")

    model_config = {"populate_by_name": True}


class UpdateAgentRequest(BaseModel):
    """Request to update an agent."""

    name: str | None = Field(default=None, min_length=1, max_length=64)
    avatar: str | None = Field(default=None, max_length=8)
    description: str | None = Field(default=None, min_length=1, max_length=280)
    capabilities: list[str] | None = None
    system_prompt: str | None = Field(default=None, alias="systemPrompt")

    model_provider: Literal[
        "anthropic", "openai", "deepseek", "volcano-ark", "openai-compatible"
    ] | None = Field(default=None, alias="modelProvider")
    model_id: str | None = Field(default=None, alias="modelId")
    api_key: str | None = Field(default=None, alias="apiKey")
    api_base_url: str | None = Field(default=None, alias="apiBaseUrl")

    tool_names: list[str] | None = Field(default=None, alias="toolNames")
    skill_names: list[str] | None = Field(default=None, alias="skillNames")
    supports_vision: bool | None = Field(default=None, alias="supportsVision")
    is_orchestrator: bool | None = Field(default=None, alias="isOrchestrator")

    # ── CLI agent fields ──────────────────────────────────────
    executable_path: str | None = Field(default=None, alias="executablePath")
    protocol_family: str | None = Field(default=None, alias="protocolFamily")
    custom_args: list[str] | None = Field(default=None, alias="customArgs")

    model_config = {"populate_by_name": True}


class AgentResponse(BaseModel):
    """Response containing an agent."""

    id: str
    name: str
    avatar: str
    description: str
    capabilities: list[str]
    system_prompt: str = Field(alias="systemPrompt")
    adapter_name: str = Field(alias="adapterName")
    model_provider: str | None = Field(alias="modelProvider")
    model_id: str | None = Field(alias="modelId")
    api_base_url: str | None = Field(alias="apiBaseUrl")
    tool_names: list[str] = Field(alias="toolNames")
    is_builtin: bool = Field(alias="isBuiltin")
    is_orchestrator: bool = Field(alias="isOrchestrator")
    supports_vision: bool = Field(alias="supportsVision")
    created_at: int = Field(alias="createdAt")
    # CLI fields
    executable_path: str | None = Field(default=None, alias="executablePath")
    protocol_family: str | None = Field(default=None, alias="protocolFamily")
    custom_args: list[str] = Field(default=[], alias="customArgs")

    model_config = {"populate_by_name": True}


# ─── Pending Approval Requests ─────────────────────────────────────
class PendingWriteAction(BaseModel):
    """Action on a pending write."""

    action: Literal["approve", "reject"]


class PendingBashAction(BaseModel):
    """Action on a pending bash command."""

    action: Literal["approve", "reject"]


class PendingQuestionAnswer(BaseModel):
    """Answer to pending questions."""

    answers: dict[str, dict]  # {question_text: {"selectedLabels": [...], "freeformNote": "..."}}


class PendingDispatchPlanAction(BaseModel):
    """Action on a pending dispatch plan."""

    action: Literal["approve", "reject", "revise"]
    feedback: str | None = None


# ─── Settings Requests ─────────────────────────────────────
class UpdateSettingsRequest(BaseModel):
    """Request to update app settings."""

    anthropic_api_key: str | None = Field(default=None, alias="anthropicApiKey")
    anthropic_base_url: str | None = Field(default=None, alias="anthropicBaseUrl")
    openai_api_key: str | None = Field(default=None, alias="openaiApiKey")
    deepseek_api_key: str | None = Field(default=None, alias="deepseekApiKey")
    ark_api_key: str | None = Field(default=None, alias="arkApiKey")
    companion_mode: Literal["off", "lan", "tailnet"] | None = Field(
        default=None, alias="companionMode"
    )
    mobile_device_token: str | None = Field(default=None, alias="mobileDeviceToken")
    deployment_publish_enabled: bool | None = Field(
        default=None, alias="deploymentPublishEnabled"
    )
    deployment_publish_dir: str | None = Field(default=None, alias="deploymentPublishDir")
    deployment_public_base_url: str | None = Field(
        default=None, alias="deploymentPublicBaseUrl"
    )

    model_config = {"populate_by_name": True}


class SettingsResponse(BaseModel):
    """Response containing app settings."""

    anthropic_api_key: str | None = Field(alias="anthropicApiKey")
    anthropic_base_url: str | None = Field(alias="anthropicBaseUrl")
    openai_api_key: str | None = Field(alias="openaiApiKey")
    deepseek_api_key: str | None = Field(alias="deepseekApiKey")
    ark_api_key: str | None = Field(alias="arkApiKey")
    companion_mode: str = Field(alias="companionMode")
    mobile_device_token: str | None = Field(alias="mobileDeviceToken")
    deployment_publish_enabled: bool = Field(alias="deploymentPublishEnabled")
    deployment_publish_dir: str | None = Field(alias="deploymentPublishDir")
    deployment_public_base_url: str | None = Field(alias="deploymentPublicBaseUrl")

    model_config = {"populate_by_name": True}


# ─── Search Requests ─────────────────────────────────────
class SearchRequest(BaseModel):
    """Search query parameters."""

    q: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    conversation_id: str | None = Field(default=None, alias="conversationId")
    role: Literal["user", "agent"] | None = None
    fallback: Literal["like"] | None = None

    model_config = {"populate_by_name": True}


class SearchHit(BaseModel):
    """A single search result."""

    message_id: str = Field(alias="messageId")
    conversation_id: str = Field(alias="conversationId")
    conversation_title: str = Field(alias="conversationTitle")
    role: Literal["user", "agent", "system"]
    agent_id: str | None = Field(alias="agentId")
    agent_name: str | None = Field(alias="agentName")
    agent_avatar: str | None = Field(alias="agentAvatar")
    created_at: int = Field(alias="createdAt")
    snippet_html: str = Field(alias="snippetHtml")

    model_config = {"populate_by_name": True}


class SearchResponse(BaseModel):
    """Search results response."""

    hits: list[SearchHit]
    total: int
    took_ms: int = Field(alias="tookMs")

    model_config = {"populate_by_name": True}


# ─── Artifact Requests/Responses ─────────────────────────────────────
class CreateArtifactVersionRequest(BaseModel):
    """Request to submit an edited artifact as a new version.

    Mirrors POST /api/artifacts/:id/versions body { content, title? }. `content`
    is the raw artifact content payload (validated downstream by the service).
    """

    content: Any
    title: str | None = None


# ─── Attachment Responses ─────────────────────────────────────
class AttachmentResponse(BaseModel):
    """A single attachment row (wire shape AttachmentRow)."""

    id: str
    conversation_id: str = Field(alias="conversationId")
    kind: Literal["image", "file"]
    file_name: str = Field(alias="fileName")
    file_path: str = Field(alias="filePath")
    size: int
    mime_type: str = Field(alias="mimeType")
    created_at: int = Field(alias="createdAt")

    model_config = {"populate_by_name": True}


class AttachmentListResponse(BaseModel):
    """Response listing a conversation's attachments."""

    attachments: list[AttachmentResponse]


class UploadAttachmentResponse(BaseModel):
    """Response after uploading an attachment."""

    attachment: AttachmentResponse


# ─── Filesystem Requests/Responses ─────────────────────────────────────
class FsWriteRequest(BaseModel):
    """Request body for POST /api/conversations/:id/fs/write."""

    path: str = Field(min_length=1)
    content: str


class FsReadResponse(BaseModel):
    """Result of reading a file in a workspace (wire shape ReadResult)."""

    path: str
    absolute_path: str = Field(alias="absolutePath")
    cwd: str
    size: int
    content: str
    truncated: bool

    model_config = {"populate_by_name": True}


class FsWriteResponse(BaseModel):
    """Result of writing a file in a workspace (wire shape WriteResult)."""

    path: str
    absolute_path: str = Field(alias="absolutePath")
    cwd: str
    bytes: int

    model_config = {"populate_by_name": True}


class FsListEntry(BaseModel):
    """A single directory entry (wire shape ListEntry)."""

    name: str
    is_directory: bool = Field(alias="isDirectory")
    size: int | None = None

    model_config = {"populate_by_name": True}


class FsListResponse(BaseModel):
    """Result of listing a workspace directory (wire shape ListResult)."""

    rel_path: str = Field(alias="relPath")
    absolute_path: str = Field(alias="absolutePath")
    parent: str | None
    entries: list[FsListEntry]

    model_config = {"populate_by_name": True}


# ─── Usage Summary Response ─────────────────────────────────────
class UsageBucket(BaseModel):
    """Aggregated token usage for a time window or grouping."""

    input_tokens: int = Field(alias="inputTokens")
    output_tokens: int = Field(alias="outputTokens")
    cache_read_tokens: int = Field(alias="cacheReadTokens")
    cache_creation_tokens: int = Field(alias="cacheCreationTokens")
    total_tokens: int = Field(alias="totalTokens")
    runs: int

    model_config = {"populate_by_name": True}


class UsageTopConversation(BaseModel):
    """A conversation in the usage top-N list."""

    id: str
    title: str
    total_tokens: int = Field(alias="totalTokens")
    runs: int
    updated_at: int = Field(alias="updatedAt")

    model_config = {"populate_by_name": True}


class UsageByAgent(BaseModel):
    """Per-agent usage aggregate."""

    agent_id: str = Field(alias="agentId")
    name: str
    total_tokens: int = Field(alias="totalTokens")
    runs: int

    model_config = {"populate_by_name": True}


class UsageByModel(BaseModel):
    """Per-model usage aggregate."""

    model: str
    total_tokens: int = Field(alias="totalTokens")
    runs: int

    model_config = {"populate_by_name": True}


class UsageSummaryResponse(BaseModel):
    """Global token usage summary (GET /api/usage/summary)."""

    today: UsageBucket
    week: UsageBucket
    all_time: UsageBucket = Field(alias="allTime")
    top_conversations: list[UsageTopConversation] = Field(alias="topConversations")
    by_agent: list[UsageByAgent] = Field(alias="byAgent")
    by_model: list[UsageByModel] = Field(alias="byModel")

    model_config = {"populate_by_name": True}


# ─── Platform Response ─────────────────────────────────────
class PlatformResponse(BaseModel):
    """Server host platform (GET /api/platform)."""

    platform: str


# ─── Connection Hints Response ─────────────────────────────────────
class ConnectionHintsResponse(BaseModel):
    """Network connection hints for the companion app (GET /api/connection-hints)."""

    hints: list[Any]
    companion_mode: str = Field(alias="companionMode")
    mobile_device_token_configured: bool = Field(alias="mobileDeviceTokenConfigured")

    model_config = {"populate_by_name": True}


# ─── Mobile Responses ─────────────────────────────────────
class MobileTokenResponse(BaseModel):
    """Response after regenerating the mobile device token (wraps settings)."""

    settings: SettingsResponse


class MobileSnapshotResponse(BaseModel):
    """Mobile snapshot (GET /api/mobile/snapshot).

    The shape is produced entirely by the mobile service in camelCase; this model
    passes it through faithfully without re-validating each deeply-nested leaf.
    """

    conversations: list[Any]
    agents: list[Any]
    running_runs: list[Any] = Field(alias="runningRuns")
    pending_writes: list[Any] = Field(alias="pendingWrites")
    pending_questions: list[Any] = Field(alias="pendingQuestions")
    server: dict[str, Any]

    model_config = {"populate_by_name": True}


# ─── Error Response ─────────────────────────────────────
class ErrorResponse(BaseModel):
    """Standard error response."""

    error: str
    details: dict | None = None
