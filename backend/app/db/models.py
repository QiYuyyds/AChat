"""SQLAlchemy ORM models matching TypeScript Drizzle schema.

Corresponds to src/db/schema.ts in the original TypeScript codebase.
Extended with AGI-memory tables (LongTermMemory, UserPreference, RagChunk,
ChatHistory, MemoryNode, MemoryEdge).
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
AdapterName = Literal["codex", "custom", "mock"]
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
    """Serialize Python object to json string (kept for backward-compat helpers)."""
    return json.dumps(obj, ensure_ascii=False)


def _json_deserializer(s: str | None) -> Any:
    """Deserialize JSON string to Python object (kept for backward-compat helpers)."""
    if s is None:
        return None
    return json.loads(s)


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

    # Codex CLI adapter: optional override for the codex binary path.
    # When None, the adapter falls back to CODEX_EXECUTABLE env or PATH search.
    executable_path: Mapped[str | None] = mapped_column(
        String, name="executable_path", nullable=True
    )

    tool_names: Mapped[list] = mapped_column(JSONB, name="tool_names", nullable=False, default=list)

    # Skills the agent has equipped (slugs under <data_dir>/skills/). custom adapter only.
    skill_names: Mapped[list] = mapped_column(JSONB, name="skill_names", nullable=False, default=list)

    is_builtin: Mapped[bool] = mapped_column(
        Boolean, name="is_builtin", nullable=False, default=False
    )
    is_orchestrator: Mapped[bool] = mapped_column(
        Boolean, name="is_orchestrator", nullable=False, default=False
    )
    supports_vision: Mapped[bool] = mapped_column(
        Boolean, name="supports_vision", nullable=False, default=False
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

    rag_enabled: Mapped[bool] = mapped_column(
        Boolean, name="rag_enabled", nullable=False, default=False
    )

    summary: Mapped[str | None] = mapped_column(Text, name="summary", nullable=True)

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


# ---------------------------------------------------------------------------
# AGI-memory new models (6 new tables)
# ---------------------------------------------------------------------------


class LongTermMemory(Base):
    """Long-term memory items with embedding vectors for semantic recall."""

    __tablename__ = "long_term_memory"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    embedding: Mapped[Any] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)
    last_accessed: Mapped[float] = mapped_column(Float, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    tags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    slot_hint: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    __table_args__ = (
        Index("idx_ltm_category", "category"),
        Index("idx_ltm_created", "created_at"),
    )


class UserPreference(Base):
    """User preference key-value pairs extracted from conversation."""

    __tablename__ = "user_preferences"

    user_id: Mapped[str] = mapped_column(String, primary_key=True, default="default_user")
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
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
    )


class ChatHistory(Base):
    """Chat history rows for ShortTerm Memory persistence."""

    __tablename__ = "chat_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)


class MemoryNode(Base):
    """Memory graph nodes (Neo4j mirror table in PG)."""

    __tablename__ = "memory_nodes"

    mem_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)


class MemoryEdge(Base):
    """Memory graph edges (Neo4j mirror table in PG)."""

    __tablename__ = "memory_edges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    from_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("memory_nodes.mem_id"), nullable=False
    )
    to_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("memory_nodes.mem_id"), nullable=False
    )
    rel_type: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # FOLLOWS / SIMILAR_TO / CAUSES / BELONGS_TO
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    __table_args__ = (
        Index("idx_memory_edges_from", "from_id"),
        Index("idx_memory_edges_to", "to_id"),
    )


# ---------------------------------------------------------------------------
# Document + Version models (global knowledge base)
# ---------------------------------------------------------------------------


class Document(Base):
    """Global knowledge-base document — independent of conversations."""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String, primary_key=True)
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

    # Relationships
    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("idx_documents_updated", "updated_at"),
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
