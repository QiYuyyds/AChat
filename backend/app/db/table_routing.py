"""Table routing — classifies ORM models to local SQLite or remote PostgreSQL.

LOCAL_TABLES (10): conversation hot data + personal configuration.
REMOTE_TABLES (12): user system + knowledge/RAG data.

When DATABASE_LOCAL_URL is set (dual-DB mode), local tables are created on
the SQLite engine and remote tables on the PostgreSQL engine. When not set
(server mode), all tables go to PostgreSQL via the remote engine.
"""

from __future__ import annotations

from sqlalchemy import Table

# ─── Local SQLite tables (12) — conversation hot data + personal config + tasks ───
LOCAL_TABLES: frozenset[str] = frozenset({
    "messages",
    "conversations",
    "agent_runs",
    "agent_run_checkpoints",
    "artifacts",
    "workspaces",
    "attachments",
    "conversation_context_summaries",
    "agents",
    "mcp_servers",
    "model_profiles",
    "tasks",
    "task_comments",
})

# ─── Remote PostgreSQL tables (9) — user system + knowledge/RAG ───
REMOTE_TABLES: frozenset[str] = frozenset({
    "users",
    "user_settings",
    "user_preferences",
    "global_settings",
    "app_settings",
    "rag_chunks",
    "chat_history",
    "documents",
    "document_versions",
})

# Tables that don't match either set are created on the local engine in
# single-DB mode but silently ignored in dual-DB mode (they must be added to
# the appropriate set above to participate in dual-DB routing).


def get_local_table_objects() -> list[Table]:
    """Return SQLAlchemy Table objects for local tables."""
    from app.db.models import Base

    return [
        tbl for name, tbl in Base.metadata.tables.items()
        if name in LOCAL_TABLES
    ]


def get_remote_table_objects() -> list[Table]:
    """Return SQLAlchemy Table objects for remote tables."""
    from app.db.models import Base

    return [
        tbl for name, tbl in Base.metadata.tables.items()
        if name in REMOTE_TABLES
    ]
