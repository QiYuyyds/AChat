"""SQLAlchemy ORM models matching TypeScript Drizzle schema.

Corresponds to src/db/schema.ts in the original TypeScript codebase.
"""

import json
from typing import Any, Literal

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
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
ConversationMode = Literal["single", "group"]
MessageRole = Literal["user", "agent", "system"]
MessageStatus = Literal["streaming", "complete", "error", "aborted"]
RunStatus = Literal["queued", "running", "complete", "failed", "aborted"]
WorkspaceMode = Literal["sandbox", "local"]
AttachmentKind = Literal["image", "file"]
FsWriteApprovalMode = Literal["auto", "review"]
CompanionMode = Literal["off", "lan", "tailnet"]


def _json_serializer(obj: Any) -> str:
    """Serialize Python object to JSON string."""
    return json.dumps(obj, ensure_ascii=False)


def _json_deserializer(s: str | None) -> Any:
    """Deserialize JSON string to Python object."""
    if s is None:
        return None
    return json.loads(s)


class Agent(Base):
    """Agent model - AI agents that can participate in conversations."""

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    avatar: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(String, nullable=False)
    capabilities: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    system_prompt: Mapped[str] = mapped_column(
        String, name="system_prompt", nullable=False
    )
    adapter_name: Mapped[str] = mapped_column(
        String, name="adapter_name", nullable=False
    )

    model_provider: Mapped[str | None] = mapped_column(
        String, name="model_provider", nullable=True
    )
    model_id: Mapped[str | None] = mapped_column(
        String, name="model_id", nullable=True
    )
    api_key: Mapped[str | None] = mapped_column(
        String, name="api_key", nullable=True
    )
    api_base_url: Mapped[str | None] = mapped_column(
        String, name="api_base_url", nullable=True
    )

    tool_names: Mapped[str] = mapped_column(
        Text, name="tool_names", nullable=False, default="[]"
    )

    is_builtin: Mapped[bool] = mapped_column(
        Boolean, name="is_builtin", nullable=False, default=False
    )
    is_orchestrator: Mapped[bool] = mapped_column(
        Boolean, name="is_orchestrator", nullable=False, default=False
    )
    supports_vision: Mapped[bool] = mapped_column(
        Boolean, name="supports_vision", nullable=False, default=False
    )

    created_at: Mapped[int] = mapped_column(Integer, name="created_at", nullable=False)

    # Relationships
    messages: Mapped[list["Message"]] = relationship(back_populates="agent")
    artifacts: Mapped[list["Artifact"]] = relationship(back_populates="created_by_agent")
    runs: Mapped[list["AgentRun"]] = relationship(back_populates="agent")

    @property
    def capabilities_list(self) -> list[str]:
        """Get capabilities as Python list."""
        return _json_deserializer(self.capabilities) or []

    @capabilities_list.setter
    def capabilities_list(self, value: list[str]) -> None:
        """Set capabilities from Python list."""
        self.capabilities = _json_serializer(value)

    @property
    def tool_names_list(self) -> list[str]:
        """Get tool_names as Python list."""
        return _json_deserializer(self.tool_names) or []

    @tool_names_list.setter
    def tool_names_list(self, value: list[str]) -> None:
        """Set tool_names from Python list."""
        self.tool_names = _json_serializer(value)


class Conversation(Base):
    """Conversation model - chat sessions with one or more agents."""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    mode: Mapped[str] = mapped_column(String, nullable=False)  # 'single' | 'group'
    agent_ids: Mapped[str] = mapped_column(
        Text, name="agent_ids", nullable=False, default="[]"
    )
    pinned_message_ids: Mapped[str] = mapped_column(
        Text, name="pinned_message_ids", nullable=False, default="[]"
    )
    bookmarked_message_ids: Mapped[str] = mapped_column(
        Text, name="bookmarked_message_ids", nullable=False, default="[]"
    )
    archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pinned_at: Mapped[int | None] = mapped_column(
        Integer, name="pinned_at", nullable=True
    )

    fs_write_approval_mode: Mapped[str] = mapped_column(
        String, name="fs_write_approval_mode", nullable=False, default="review"
    )

    created_at: Mapped[int] = mapped_column(Integer, name="created_at", nullable=False)
    updated_at: Mapped[int] = mapped_column(Integer, name="updated_at", nullable=False)

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
        """Get agent_ids as Python list."""
        return _json_deserializer(self.agent_ids) or []

    @agent_ids_list.setter
    def agent_ids_list(self, value: list[str]) -> None:
        """Set agent_ids from Python list."""
        self.agent_ids = _json_serializer(value)

    @property
    def pinned_message_ids_list(self) -> list[str]:
        """Get pinned_message_ids as Python list."""
        return _json_deserializer(self.pinned_message_ids) or []

    @pinned_message_ids_list.setter
    def pinned_message_ids_list(self, value: list[str]) -> None:
        """Set pinned_message_ids from Python list."""
        self.pinned_message_ids = _json_serializer(value)

    @property
    def bookmarked_message_ids_list(self) -> list[str]:
        """Get bookmarked_message_ids as Python list."""
        return _json_deserializer(self.bookmarked_message_ids) or []

    @bookmarked_message_ids_list.setter
    def bookmarked_message_ids_list(self, value: list[str]) -> None:
        """Set bookmarked_message_ids from Python list."""
        self.bookmarked_message_ids = _json_serializer(value)


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

    parts: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    status: Mapped[str] = mapped_column(String, nullable=False)
    parent_message_id: Mapped[str | None] = mapped_column(
        String, name="parent_message_id", nullable=True
    )
    mentioned_agent_ids: Mapped[str] = mapped_column(
        Text, name="mentioned_agent_ids", nullable=False, default="[]"
    )

    run_id: Mapped[str | None] = mapped_column(
        String, name="run_id", nullable=True
    )

    usage: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[int] = mapped_column(Integer, name="created_at", nullable=False)

    # Relationships
    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
    agent: Mapped["Agent | None"] = relationship(back_populates="messages")

    __table_args__ = (
        Index("idx_messages_conv_created", "conversation_id", "created_at"),
    )

    @property
    def parts_list(self) -> list[dict]:
        """Get parts as Python list."""
        return _json_deserializer(self.parts) or []

    @parts_list.setter
    def parts_list(self, value: list[dict]) -> None:
        """Set parts from Python list."""
        self.parts = _json_serializer(value)

    @property
    def mentioned_agent_ids_list(self) -> list[str]:
        """Get mentioned_agent_ids as Python list."""
        return _json_deserializer(self.mentioned_agent_ids) or []

    @mentioned_agent_ids_list.setter
    def mentioned_agent_ids_list(self, value: list[str]) -> None:
        """Set mentioned_agent_ids from Python list."""
        self.mentioned_agent_ids = _json_serializer(value)

    @property
    def usage_dict(self) -> dict | None:
        """Get usage as Python dict."""
        return _json_deserializer(self.usage)

    @usage_dict.setter
    def usage_dict(self, value: dict | None) -> None:
        """Set usage from Python dict."""
        self.usage = _json_serializer(value) if value else None


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
    content: Mapped[str] = mapped_column(Text, nullable=False)

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
    created_at: Mapped[int] = mapped_column(Integer, name="created_at", nullable=False)

    # Relationships
    conversation: Mapped["Conversation"] = relationship(back_populates="artifacts")
    created_by_agent: Mapped["Agent"] = relationship(back_populates="artifacts")

    __table_args__ = (
        Index("idx_artifacts_conv", "conversation_id"),
    )

    @property
    def content_dict(self) -> dict:
        """Get content as Python dict."""
        return _json_deserializer(self.content) or {}

    @content_dict.setter
    def content_dict(self, value: dict) -> None:
        """Set content from Python dict."""
        self.content = _json_serializer(value)


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
    created_at: Mapped[int] = mapped_column(Integer, name="created_at", nullable=False)

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

    created_at: Mapped[int] = mapped_column(Integer, name="created_at", nullable=False)

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

    usage: Mapped[str | None] = mapped_column(Text, nullable=True)

    started_at: Mapped[int] = mapped_column(Integer, name="started_at", nullable=False)
    finished_at: Mapped[int | None] = mapped_column(
        Integer, name="finished_at", nullable=True
    )

    # Relationships
    conversation: Mapped["Conversation"] = relationship(back_populates="runs")
    agent: Mapped["Agent"] = relationship(back_populates="runs")

    __table_args__ = (
        Index("idx_runs_parent", "parent_run_id"),
    )

    @property
    def usage_dict(self) -> dict | None:
        """Get usage as Python dict."""
        return _json_deserializer(self.usage)

    @usage_dict.setter
    def usage_dict(self, value: dict | None) -> None:
        """Set usage from Python dict."""
        self.usage = _json_serializer(value) if value else None


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
        Integer, name="covered_until_created_at", nullable=False
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
    created_at: Mapped[int] = mapped_column(Integer, name="created_at", nullable=False)

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
    updated_at: Mapped[int] = mapped_column(Integer, name="updated_at", nullable=False)
