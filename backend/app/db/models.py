"""SQLAlchemy ORM models matching TypeScript Drizzle schema.

Corresponds to src/db/schema.ts in the original TypeScript codebase.
Extended with UserPreference, RagChunk, ChatHistory (file-native memory migration).
"""

import json
from typing import Any, Literal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.types import JSON as _BaseJSON

# SQLAlchemy JSON type auto-uses JSONB on PostgreSQL dialect; plain JSON on SQLite.
JSONB = _BaseJSON

from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


class Base(DeclarativeBase):
    """Base class for all models."""

    pass


# Type aliases matching TypeScript types
AdapterName = Literal["claude-code", "codex", "custom", "mock"]
ModelProvider = Literal["anthropic", "openai", "deepseek", "volcano-ark", "openai-compatible"]
ConversationMode = Literal["single", "group", "guide"]
MessageRole = Literal["user", "agent", "system"]
MessageStatus = Literal["streaming", "complete", "error", "aborted"]
RunStatus = Literal["queued", "running", "complete", "failed", "aborted"]
WorkspaceMode = Literal["sandbox", "local"]
AttachmentKind = Literal["image", "file"]
FsWriteApprovalMode = Literal["auto", "review"]
CompanionMode = Literal["off", "lan", "tailnet"]


def _json_serializer(obj: Any) -> str:
    """Serialize Python object to json string (kept for backward-compat helpers)."""
    return json.dumps(obj, ensure_ascii=False)


def _json_deserializer(s: str | None) -> Any:
    """Deserialize JSON string to Python object (kept for backward-compat helpers)."""
    if s is None:
        return None
    return json.loads(s)


# ---------------------------------------------------------------------------
# User model (authentication & ownership root)
# ---------------------------------------------------------------------------


class User(Base):
    """User model — authenticated person who owns agents, conversations, etc."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    password_hash: Mapped[str] = mapped_column(
        String, name="password_hash", nullable=False
    )
    avatar_url: Mapped[str | None] = mapped_column(
        String, name="avatar_url", nullable=True
    )
    token_version: Mapped[int] = mapped_column(
        Integer, name="token_version", nullable=False, default=0
    )
    created_at: Mapped[int] = mapped_column(BigInteger, name="created_at", nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, name="updated_at", nullable=False)


# ---------------------------------------------------------------------------
# Core domain models (existing 9 tables, JSON columns upgraded to JSONB)
# ---------------------------------------------------------------------------


class Agent(Base):
    """Agent model - AI agents who can participate in conversations."""

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    avatar: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False)

    # JSONB columns (upgraded from Text)
    capabilities: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    system_prompt: Mapped[str] = mapped_column(
        String, name="system_prompt", nullable=False
    )
    adapter_name: Mapped[str] = mapped_column(
        String, name="adapter_name", nullable=False
    )

    tool_names: Mapped[list] = mapped_column(JSONB, name="tool_names", nullable=False, default=list)

    # ── CLI agent fields ──────────────────────────────────────
    # Path to the CLI binary; None → adapter looks it up on PATH.
    executable_path: Mapped[str | None] = mapped_column(
        String, name="executable_path", nullable=True
    )
    # Protocol family for CLI agents: 'claude' | 'codex'. None for SDK agents.
    protocol_family: Mapped[str | None] = mapped_column(
        String, name="protocol_family", nullable=True
    )
    # Per-agent CLI custom args. Blocked protocol flags are stripped at runtime.
    custom_args: Mapped[list] = mapped_column(
        JSONB, name="custom_args", nullable=False, default=list
    )

    # Skills the agent has equipped (slugs under <data_dir>/skills/). custom adapter only.
    skill_names: Mapped[list] = mapped_column(JSONB, name="skill_names", nullable=False, default=list)

    # Hook groups enabled for this agent (e.g. ["checkpoint", "auto_compact"]).
    hook_names: Mapped[list] = mapped_column(JSONB, name="hook_names", nullable=False, default=list)

    # MCP server IDs this agent has enabled (Custom adapter only).
    mcp_server_ids: Mapped[list] = mapped_column(
        JSONB, name="mcp_server_ids", nullable=False, default=list
    )

    is_builtin: Mapped[bool] = mapped_column(
        Boolean, name="is_builtin", nullable=False, default=False
    )
    is_orchestrator: Mapped[bool] = mapped_column(
        Boolean, name="is_orchestrator", nullable=False, default=False
    )
    is_guide: Mapped[bool] = mapped_column(
        Boolean, name="is_guide", nullable=False, default=False
    )
    memory_enabled: Mapped[bool] = mapped_column(
        Boolean, name="memory_enabled", nullable=False, default=False
    )

    created_at: Mapped[int] = mapped_column(BigInteger, name="created_at", nullable=False)

    # Relationships
    messages: Mapped[list["Message"]] = relationship(back_populates="agent")
    artifacts: Mapped[list["Artifact"]] = relationship(back_populates="created_by_agent")
    runs: Mapped[list["AgentRun"]] = relationship(back_populates="agent")

    @property
    def capabilities_list(self) -> list[str]:
        """Get capabilities as Python list (JSONB already returns list)."""
        return list(self.capabilities) if self.capabilities else []

    @capabilities_list.setter
    def capabilities_list(self, value: list[str]) -> None:
        self.capabilities = value

    @property
    def tool_names_list(self) -> list[str]:
        """Get tool_names as Python list (JSONB already returns list)."""
        return list(self.tool_names) if self.tool_names else []

    @tool_names_list.setter
    def tool_names_list(self, value: list[str]) -> None:
        self.tool_names = value

    @property
    def skill_names_list(self) -> list[str]:
        """Get skill_names as Python list (JSONB already returns list)."""
        return list(self.skill_names) if self.skill_names else []

    @skill_names_list.setter
    def skill_names_list(self, value: list[str]) -> None:
        self.skill_names = value

    @property
    def hook_names_list(self) -> list[str]:
        """Get hook_names as Python list (JSONB already returns list)."""
        return list(self.hook_names) if self.hook_names else []

    @hook_names_list.setter
    def hook_names_list(self, value: list[str]) -> None:
        self.hook_names = value

    @property
    def mcp_server_ids_list(self) -> list[str]:
        """Get mcp_server_ids as Python list (JSONB already returns list)."""
        return list(self.mcp_server_ids) if self.mcp_server_ids else []

    @mcp_server_ids_list.setter
    def mcp_server_ids_list(self, value: list[str]) -> None:
        self.mcp_server_ids = value

    @property
    def checkpoint_enabled(self) -> bool:
        """Whether checkpoint saving is enabled for this agent."""
        return "checkpoint" in self.hook_names_list

    @property
    def custom_args_list(self) -> list[str]:
        """Get custom_args as Python list (JSONB already returns list)."""
        return list(self.custom_args) if self.custom_args else []

    @custom_args_list.setter
    def custom_args_list(self, value: list[str]) -> None:
        self.custom_args = value


class Conversation(Base):
    """Conversation model - chat sessions with one or more agents."""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    mode: Mapped[str] = mapped_column(String, nullable=False)  # 'single' | 'group'

    # JSONB columns (upgraded from Text)
    agent_ids: Mapped[list] = mapped_column(JSONB, name="agent_ids", nullable=False, default=list)
    pinned_message_ids: Mapped[list] = mapped_column(
        JSONB, name="pinned_message_ids", nullable=False, default=list
    )
    bookmarked_message_ids: Mapped[list] = mapped_column(
        JSONB, name="bookmarked_message_ids", nullable=False, default=list
    )

    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pinned_at: Mapped[int | None] = mapped_column(
        BigInteger, name="pinned_at", nullable=True
    )

    fs_write_approval_mode: Mapped[str] = mapped_column(
        String, name="fs_write_approval_mode", nullable=False, default="review"
    )

    # Deprecated: rag_enabled is no longer read or written by application code.
    # RAG tool availability is now determined solely by agent.toolNames containing "rag_search".
    # Column retained for backward compat to avoid DB migration risk.
    rag_enabled: Mapped[bool] = mapped_column(
        Boolean, name="rag_enabled", nullable=False, default=False
    )

    summary: Mapped[str | None] = mapped_column(Text, name="summary", nullable=True)

    dispatch_mode: Mapped[str] = mapped_column(
        String, name="dispatch_mode", nullable=False, default="solo"
    )

    # Fork origin tracking (nullable — normal conversations have both null).
    parent_conversation_id: Mapped[str | None] = mapped_column(
        String, name="parent_conversation_id", nullable=True
    )
    fork_point_message_id: Mapped[str | None] = mapped_column(
        String, name="fork_point_message_id", nullable=True
    )

    created_at: Mapped[int] = mapped_column(BigInteger, name="created_at", nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, name="updated_at", nullable=False)

    # Relationships
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    artifacts: Mapped[list["Artifact"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    workspace: Mapped["Workspace"] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", uselist=False
    )
    attachments: Mapped[list["Attachment"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    runs: Mapped[list["AgentRun"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    context_summaries: Mapped[list["ContextSummary"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_conv_updated", "updated_at"),
    )

    @property
    def agent_ids_list(self) -> list[str]:
        return list(self.agent_ids) if self.agent_ids else []

    @agent_ids_list.setter
    def agent_ids_list(self, value: list[str]) -> None:
        self.agent_ids = value

    @property
    def pinned_message_ids_list(self) -> list[str]:
        return list(self.pinned_message_ids) if self.pinned_message_ids else []

    @pinned_message_ids_list.setter
    def pinned_message_ids_list(self, value: list[str]) -> None:
        self.pinned_message_ids = value

    @property
    def bookmarked_message_ids_list(self) -> list[str]:
        return list(self.bookmarked_message_ids) if self.bookmarked_message_ids else []

    @bookmarked_message_ids_list.setter
    def bookmarked_message_ids_list(self, value: list[str]) -> None:
        self.bookmarked_message_ids = value


class Message(Base):
    """Message model - individual messages in a conversation."""

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        name="conversation_id",
        nullable=False,
    )

    role: Mapped[str] = mapped_column(String, nullable=False)  # 'user' | 'agent' | 'system'
    agent_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey("agents.id"),
        name="agent_id",
        nullable=True,
    )

    # JSONB columns (upgraded from Text)
    parts: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    status: Mapped[str] = mapped_column(String, nullable=False)
    parent_message_id: Mapped[str | None] = mapped_column(
        String, name="parent_message_id", nullable=True
    )
    mentioned_agent_ids: Mapped[list] = mapped_column(
        JSONB, name="mentioned_agent_ids", nullable=False, default=list
    )

    run_id: Mapped[str | None] = mapped_column(
        String, name="run_id", nullable=True
    )

    usage: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Clone-subagent messages are hidden from conversation history and frontend
    hidden: Mapped[bool] = mapped_column(
        Boolean, name="hidden", nullable=False, default=False
    )

    created_at: Mapped[int] = mapped_column(BigInteger, name="created_at", nullable=False)

    # Relationships
    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
    agent: Mapped["Agent | None"] = relationship(back_populates="messages")

    __table_args__ = (
        Index("idx_messages_conv_created", "conversation_id", "created_at"),
    )

    @property
    def parts_list(self) -> list[dict]:
        return list(self.parts) if self.parts else []

    @parts_list.setter
    def parts_list(self, value: list[dict]) -> None:
        self.parts = value

    @property
    def mentioned_agent_ids_list(self) -> list[str]:
        return list(self.mentioned_agent_ids) if self.mentioned_agent_ids else []

    @mentioned_agent_ids_list.setter
    def mentioned_agent_ids_list(self, value: list[str]) -> None:
        self.mentioned_agent_ids = value

    @property
    def usage_dict(self) -> dict | None:
        return dict(self.usage) if self.usage else None

    @usage_dict.setter
    def usage_dict(self, value: dict | None) -> None:
        self.usage = value


class Artifact(Base):
    """Artifact model - created content like web apps, documents, images."""

    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        name="conversation_id",
        nullable=False,
    )

    type: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)

    # JSONB column (upgraded from Text)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    parent_artifact_id: Mapped[str | None] = mapped_column(
        String, name="parent_artifact_id", nullable=True
    )

    created_by_agent_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("agents.id"),
        name="created_by_agent_id",
        nullable=False,
    )
    created_at: Mapped[int] = mapped_column(BigInteger, name="created_at", nullable=False)

    # Relationships
    conversation: Mapped["Conversation"] = relationship(back_populates="artifacts")
    created_by_agent: Mapped["Agent"] = relationship(back_populates="artifacts")

    __table_args__ = (
        Index("idx_artifacts_conv", "conversation_id"),
    )

    @property
    def content_dict(self) -> dict:
        return dict(self.content) if self.content else {}

    @content_dict.setter
    def content_dict(self, value: dict) -> None:
        self.content = value


class Workspace(Base):
    """Workspace model - file system workspace for a conversation."""

    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        name="conversation_id",
        nullable=False,
        unique=True,
    )
    root_path: Mapped[str] = mapped_column(String, name="root_path", nullable=False)
    mode: Mapped[str] = mapped_column(String, nullable=False, default="sandbox")
    bound_path: Mapped[str | None] = mapped_column(
        String, name="bound_path", nullable=True
    )
    # User's choice for workspace env isolation: null = not yet prompted,
    # 'venv_created' / 'skip' / 'system_python'. See specs/workspace-env-isolation.
    env_preference: Mapped[str | None] = mapped_column(
        String, name="env_preference", nullable=True, default=None
    )
    created_at: Mapped[int] = mapped_column(BigInteger, name="created_at", nullable=False)

    # Relationships
    conversation: Mapped["Conversation"] = relationship(back_populates="workspace")


class Attachment(Base):
    """Attachment model - uploaded files in a conversation."""

    __tablename__ = "attachments"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        name="conversation_id",
        nullable=False,
    )

    kind: Mapped[str] = mapped_column(String, nullable=False)  # 'image' | 'file'
    file_name: Mapped[str] = mapped_column(String, name="file_name", nullable=False)
    file_path: Mapped[str] = mapped_column(String, name="file_path", nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    mime_type: Mapped[str] = mapped_column(String, name="mime_type", nullable=False)

    created_at: Mapped[int] = mapped_column(BigInteger, name="created_at", nullable=False)

    # Relationships
    conversation: Mapped["Conversation"] = relationship(back_populates="attachments")

    __table_args__ = (
        Index("idx_attachments_conv", "conversation_id"),
    )


class AgentRun(Base):
    """AgentRun model - execution records of agent runs."""

    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        name="conversation_id",
        nullable=False,
    )
    agent_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("agents.id"),
        name="agent_id",
        nullable=False,
    )
    trigger_message_id: Mapped[str | None] = mapped_column(
        String, name="trigger_message_id", nullable=True
    )

    status: Mapped[str] = mapped_column(String, nullable=False)
    error: Mapped[str | None] = mapped_column(String, nullable=True)

    parent_run_id: Mapped[str | None] = mapped_column(
        String, name="parent_run_id", nullable=True
    )

    # JSONB column (upgraded from Text)
    usage: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # JSONB columns for dispatch plan/results (orchestrator)
    dispatch_plan: Mapped[dict | None] = mapped_column(JSONB, name="dispatch_plan", nullable=True)
    dispatch_results: Mapped[dict | None] = mapped_column(JSONB, name="dispatch_results", nullable=True)

    started_at: Mapped[int] = mapped_column(BigInteger, name="started_at", nullable=False)
    finished_at: Mapped[int | None] = mapped_column(
        BigInteger, name="finished_at", nullable=True
    )

    # CLI agent session ID for cross-run resume (e.g. claude --resume <session_id>).
    # NULL for SDK agent runs or CLI runs that failed before capturing a session ID.
    cli_session_id: Mapped[str | None] = mapped_column(
        String, name="cli_session_id", nullable=True
    )

    # Relationships
    conversation: Mapped["Conversation"] = relationship(back_populates="runs")
    agent: Mapped["Agent"] = relationship(back_populates="runs")

    __table_args__ = (
        Index("idx_runs_parent", "parent_run_id"),
    )

    @property
    def usage_dict(self) -> dict | None:
        return dict(self.usage) if self.usage else None

    @usage_dict.setter
    def usage_dict(self, value: dict | None) -> None:
        self.usage = value


class AgentRunCheckpoint(Base):
    """Turn-level checkpoint for SDK agent runs (save/resume)."""

    __tablename__ = "agent_run_checkpoints"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        name="run_id",
        nullable=False,
    )
    turn_number: Mapped[int] = mapped_column(Integer, name="turn_number", nullable=False)
    messages_json: Mapped[list] = mapped_column(JSONB, name="messages_json", nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, name="created_at", nullable=False)

    __table_args__ = (
        Index("idx_checkpoints_run_turn", "run_id", "turn_number"),
    )


class ContextSummary(Base):
    """ContextSummary model - compressed conversation history summaries."""

    __tablename__ = "conversation_context_summaries"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("conversations.id", ondelete="CASCADE"),
        name="conversation_id",
        nullable=False,
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    covered_until_message_id: Mapped[str] = mapped_column(
        String, name="covered_until_message_id", nullable=False
    )
    covered_until_created_at: Mapped[int] = mapped_column(
        BigInteger, name="covered_until_created_at", nullable=False
    )
    source_message_count: Mapped[int] = mapped_column(
        Integer, name="source_message_count", nullable=False
    )
    token_estimate: Mapped[int] = mapped_column(
        Integer, name="token_estimate", nullable=False
    )
    model_provider: Mapped[str | None] = mapped_column(
        String, name="model_provider", nullable=True
    )
    model_id: Mapped[str | None] = mapped_column(
        String, name="model_id", nullable=True
    )
    # 'compaction' = Tier 2/3 LLM summary; 'session' = incremental Session Memory
    summary_type: Mapped[str] = mapped_column(
        String(16), name="summary_type", nullable=False, default="compaction"
    )
    # Session Memory: timestamp of the last message covered by the summary
    covers_up_to: Mapped[float | None] = mapped_column(
        Float, name="covers_up_to", nullable=True
    )
    created_at: Mapped[int] = mapped_column(BigInteger, name="created_at", nullable=False)

    # Relationships
    conversation: Mapped["Conversation"] = relationship(back_populates="context_summaries")

    __table_args__ = (
        Index("idx_context_summaries_conv_created", "conversation_id", "created_at"),
    )


class AppSettings(Base):
    """AppSettings model - global application settings (single row table)."""

    __tablename__ = "app_settings"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # Always 'singleton'
    anthropic_api_key: Mapped[str | None] = mapped_column(
        String, name="anthropic_api_key", nullable=True
    )
    anthropic_base_url: Mapped[str | None] = mapped_column(
        String, name="anthropic_base_url", nullable=True
    )
    openai_api_key: Mapped[str | None] = mapped_column(
        String, name="openai_api_key", nullable=True
    )
    deepseek_api_key: Mapped[str | None] = mapped_column(
        String, name="deepseek_api_key", nullable=True
    )
    ark_api_key: Mapped[str | None] = mapped_column(
        String, name="ark_api_key", nullable=True
    )
    companion_mode: Mapped[str] = mapped_column(
        String, name="companion_mode", nullable=False, default="off"
    )
    mobile_device_token: Mapped[str | None] = mapped_column(
        String, name="mobile_device_token", nullable=True
    )
    deployment_publish_enabled: Mapped[bool] = mapped_column(
        Boolean, name="deployment_publish_enabled", nullable=False, default=False
    )
    deployment_publish_dir: Mapped[str | None] = mapped_column(
        String, name="deployment_publish_dir", nullable=True
    )
    deployment_public_base_url: Mapped[str | None] = mapped_column(
        String, name="deployment_public_base_url", nullable=True
    )

    # JSONB column (upgraded from Text)
    settings: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    updated_at: Mapped[int] = mapped_column(BigInteger, name="updated_at", nullable=False)


class GlobalSettings(Base):
    """GlobalSettings model — server-level config shared across all users."""

    __tablename__ = "global_settings"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # Always 'singleton'
    deployment_publish_enabled: Mapped[bool] = mapped_column(
        Boolean, name="deployment_publish_enabled", nullable=False, default=False
    )
    deployment_publish_dir: Mapped[str | None] = mapped_column(
        String, name="deployment_publish_dir", nullable=True
    )
    deployment_public_base_url: Mapped[str | None] = mapped_column(
        String, name="deployment_public_base_url", nullable=True
    )
    updated_at: Mapped[int] = mapped_column(BigInteger, name="updated_at", nullable=False)


class UserSettings(Base):
    """UserSettings model — per-user API keys and companion config (PK = user_id)."""

    __tablename__ = "user_settings"

    user_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    anthropic_api_key: Mapped[str | None] = mapped_column(
        String, name="anthropic_api_key", nullable=True
    )
    anthropic_base_url: Mapped[str | None] = mapped_column(
        String, name="anthropic_base_url", nullable=True
    )
    openai_api_key: Mapped[str | None] = mapped_column(
        String, name="openai_api_key", nullable=True
    )
    deepseek_api_key: Mapped[str | None] = mapped_column(
        String, name="deepseek_api_key", nullable=True
    )
    ark_api_key: Mapped[str | None] = mapped_column(
        String, name="ark_api_key", nullable=True
    )
    companion_mode: Mapped[str] = mapped_column(
        String, name="companion_mode", nullable=False, default="off"
    )
    mobile_device_token: Mapped[str | None] = mapped_column(
        String, name="mobile_device_token", nullable=True
    )
    obsidian_vault_path: Mapped[str | None] = mapped_column(
        String(1024), name="obsidian_vault_path", nullable=True
    )
    settings: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[int] = mapped_column(BigInteger, name="updated_at", nullable=False)


class ModelProfile(Base):
    """Reusable model configuration (provider + model_id + key + url).

    Replaces the per-Agent model fields (model_provider / model_id / api_key /
    api_base_url / supports_vision) that were removed. Each profile is a named,
    testable model configuration that can be selected per-message in the input bar.
    Single-user local table — no user_id column.
    """

    __tablename__ = "model_profiles"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    model_id: Mapped[str] = mapped_column(String, name="model_id", nullable=False)
    api_key: Mapped[str | None] = mapped_column(
        String, name="api_key", nullable=True
    )
    api_base_url: Mapped[str | None] = mapped_column(
        String, name="api_base_url", nullable=True
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, name="is_default", nullable=False, default=False
    )
    supports_vision: Mapped[bool] = mapped_column(
        Boolean, name="supports_vision", nullable=False, default=False
    )
    # 'untested' | 'ok' | 'fail'
    last_test_status: Mapped[str] = mapped_column(
        String(16), name="last_test_status", nullable=False, default="untested"
    )
    last_tested_at: Mapped[int | None] = mapped_column(
        BigInteger, name="last_tested_at", nullable=True
    )
    cache_style: Mapped[str | None] = mapped_column(
        String(16), name="cache_style", nullable=True
    )
    detected_cache_style: Mapped[str | None] = mapped_column(
        String(16), name="detected_cache_style", nullable=True
    )
    created_at: Mapped[int] = mapped_column(BigInteger, name="created_at", nullable=False)
    updated_at: Mapped[int] = mapped_column(BigInteger, name="updated_at", nullable=False)

    __table_args__ = (
        # Partial unique index: at most one is_default=true.
        # On SQLite this is handled at application level; on PG the index is
        # created via the migration statements in engine.py.
    )


class McpServer(Base):
    """MCP server configuration — globally defined, per-agent opted-in."""

    __tablename__ = "mcp_servers"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    transport: Mapped[str] = mapped_column(String, nullable=False)  # 'stdio' | 'sse' | 'streamable_http'
    command: Mapped[str | None] = mapped_column(String, nullable=True)
    args: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    env: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    headers: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    trust: Mapped[str] = mapped_column(String, nullable=False, default="ask")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[int] = mapped_column(BigInteger, name="created_at", nullable=False)


# ---------------------------------------------------------------------------
# Memory models (UserPreference only — LongTermMemory/MemoryNode/MemoryEdge removed in file-native migration)
# ---------------------------------------------------------------------------


class UserPreference(Base):
    """User preference key-value pairs — manually entered or LLM-extracted.

    The ``source`` column distinguishes values entered by the user via the
    profile UI (``source='manual'``) from values auto-extracted by the LLM
    or rule-based system (``source='extracted'``). Manual values take
    priority: LLM extraction cannot overwrite a manual row for the same key.
    """

    __tablename__ = "user_preferences"

    user_id: Mapped[str] = mapped_column(String, primary_key=True, default="default_user")
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(
        String(16), name="source", nullable=False, default="extracted"
    )
    updated_at: Mapped[float] = mapped_column(Float, nullable=False)


class RagChunk(Base):
    """RAG document chunks stored with embeddings for hybrid retrieval."""

    __tablename__ = "rag_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_hash: Mapped[str] = mapped_column(String, nullable=False)
    chunk_idx: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    parent_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding: Mapped[Any] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    user_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    # Document traceability fields (nullable for bare-ingest chunks without a Document)
    document_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("documents.id", ondelete="SET NULL"), nullable=True
    )
    version_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("document_versions.id", ondelete="SET NULL"), nullable=True
    )
    # Chunk-level content hash (sha256[:16]) for embedding cache reuse
    content_hash: Mapped[str | None] = mapped_column(
        String(16), name="content_hash", nullable=True, index=True
    )

    __table_args__ = (
        Index("idx_rag_doc_hash", "doc_hash"),
        Index("idx_rag_content_hash", "content_hash"),
        Index("idx_rag_user", "user_id"),
    )


class ChatHistory(Base):
    """Chat history rows for memory pipeline dual-write (PG + session/ jsonl)."""

    __tablename__ = "chat_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    user_id: Mapped[str | None] = mapped_column(
        String, ForeignKey("users.id"), name="user_id", nullable=True
    )


# ---------------------------------------------------------------------------
# Document + Version models (global knowledge base)
# ---------------------------------------------------------------------------


class Document(Base):
    """Global knowledge-base document — independent of conversations."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id"), name="user_id", nullable=False
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(64), nullable=False, default="note")
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="agent_generated"
    )  # agent_generated | user_upload
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active"
    )  # active | deleted
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, default="agent")
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[float] = mapped_column(Float, nullable=False)
    latest_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latest_version_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    source_path: Mapped[str] = mapped_column(
        String(1024), name="source_path", nullable=False, default=""
    )
    content_hash: Mapped[str | None] = mapped_column(
        String(64), name="content_hash", nullable=True
    )

    # Relationships
    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_documents_updated", "updated_at"),
        Index("idx_documents_source_path", "source_path"),
    )


class DocumentVersion(Base):
    """Versioned content of a Document — each update creates a new version row."""

    __tablename__ = "document_versions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    document_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "metadata" is reserved on DeclarativeBase; use "meta" in Python, "metadata" in DB
    meta: Mapped[dict] = mapped_column(JSONB, name="metadata", nullable=False, default=dict)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)

    # Relationships
    document: Mapped["Document"] = relationship(back_populates="versions")

    __table_args__ = (
        Index("idx_doc_versions_doc_id", "document_id", "version"),
        UniqueConstraint("document_id", "version"),
    )
