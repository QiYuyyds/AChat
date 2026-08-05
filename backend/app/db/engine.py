"""SQLAlchemy async database engine and session management.

Dual-DB architecture:
- _local_engine / _local_session_factory: SQLite (conversation hot data)
- _remote_engine / _remote_session_factory: PostgreSQL (user system + RAG)

Local tables are always created on SQLite; remote tables on PostgreSQL.
"""

import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings

# ── Global engines and session factories ──────────────────────────────
_remote_engine: AsyncEngine | None = None
_remote_session_factory: async_sessionmaker[AsyncSession] | None = None
_local_engine: AsyncEngine | None = None
_local_session_factory: async_sessionmaker[AsyncSession] | None = None


def _is_sqlite_url(url: str) -> bool:
    return url.startswith("sqlite")


async def init_db() -> None:
    """Initialize database engines and create tables if needed."""
    global _remote_engine, _remote_session_factory
    global _local_engine, _local_session_factory

    settings = get_settings()

    # ── Remote (PostgreSQL) engine — always initialized ──────────────
    remote_is_sqlite = _is_sqlite_url(settings.database_url)
    remote_kwargs: dict = {"echo": False, "future": True}
    if not remote_is_sqlite:
        remote_kwargs.update(
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=3600,
        )
    _remote_engine = create_async_engine(settings.database_url, **remote_kwargs)

    if remote_is_sqlite:
        _attach_sqlite_pragmas(_remote_engine)

    _remote_session_factory = async_sessionmaker(
        bind=_remote_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    # ── Local (SQLite) engine — only when database_local_url is set ──
    if settings.database_local_url:
        _local_engine = create_async_engine(
            settings.database_local_url,
            echo=False,
            future=True,
        )
        _attach_sqlite_pragmas(_local_engine)
        _local_session_factory = async_sessionmaker(
            bind=_local_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

    # ── Create tables on the correct engine ──────────────────────────
    from app.db.models import Base
    from app.db.table_routing import get_local_table_objects, get_remote_table_objects

    local_tables = get_local_table_objects()
    remote_tables = get_remote_table_objects()

    # Remote engine: create remote tables
    async with _remote_engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(
                sync_conn, tables=remote_tables, checkfirst=True
            )
        )
        await _migrate_columns_pg(conn)

    # Local engine: create local tables
    if _local_engine is not None:
        async with _local_engine.begin() as conn:
            await _rebuild_cross_db_fk_tables_sqlite(conn)
            await conn.run_sync(
                lambda sync_conn: Base.metadata.create_all(
                    sync_conn, tables=local_tables, checkfirst=True
                )
            )
            await _migrate_columns_sqlite(conn)


def _attach_sqlite_pragmas(engine: AsyncEngine) -> None:
    """Attach per-connection PRAGMAs for SQLite WAL + FK + busy_timeout."""

    @event.listens_for(engine.sync_engine, "connect")
    def _init_sqlite_connection(dbapi_connection, connection_record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


# ── PG-specific column migration statements ────────────────────────────
_PG_MIGRATION_STATEMENTS = [
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS skill_names JSONB NOT NULL DEFAULT '[]'::jsonb",
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS executable_path VARCHAR",
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS protocol_family VARCHAR",
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS custom_args JSONB NOT NULL DEFAULT '[]'::jsonb",
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS hook_names JSONB NOT NULL DEFAULT '[]'::jsonb",
    "ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS content_hash VARCHAR(16)",
    "CREATE INDEX IF NOT EXISTS idx_rag_content_hash ON rag_chunks (content_hash)",
    "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS summary TEXT",
    "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS dispatch_mode VARCHAR NOT NULL DEFAULT 'solo'",
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS mcp_server_ids JSONB NOT NULL DEFAULT '[]'::jsonb",
    "ALTER TABLE messages ADD COLUMN IF NOT EXISTS hidden BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS memory_enabled BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE conversation_context_summaries ADD COLUMN IF NOT EXISTS summary_type VARCHAR(16) NOT NULL DEFAULT 'compaction'",
    "ALTER TABLE conversation_context_summaries ADD COLUMN IF NOT EXISTS covers_up_to FLOAT",
    # ─── Multi-user auth migration (remote tables only) ───
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS user_id VARCHAR",
    "ALTER TABLE chat_history ADD COLUMN IF NOT EXISTS user_id VARCHAR",
    "ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS user_id VARCHAR",
    "CREATE INDEX IF NOT EXISTS idx_docs_user ON documents (user_id)",
    "CREATE INDEX IF NOT EXISTS idx_rag_user ON rag_chunks (user_id)",
    # ─── Drop user_id from local tables (single-user mode) ───
    "ALTER TABLE agents DROP COLUMN IF EXISTS user_id",
    "ALTER TABLE conversations DROP COLUMN IF EXISTS user_id",
    "ALTER TABLE mcp_servers DROP COLUMN IF EXISTS user_id",
    "ALTER TABLE model_profiles DROP COLUMN IF EXISTS user_id",
    "DROP INDEX IF EXISTS idx_agents_user",
    "DROP INDEX IF EXISTS idx_conv_user",
    "DROP INDEX IF EXISTS idx_mcp_user",
    # ─── UserPreference source column ───
    "ALTER TABLE user_preferences ADD COLUMN IF NOT EXISTS source VARCHAR(16) NOT NULL DEFAULT 'extracted'",
    # ─── Obsidian sync columns ───
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_path VARCHAR(1024) NOT NULL DEFAULT ''",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS content_hash VARCHAR(64)",
    "CREATE INDEX IF NOT EXISTS idx_documents_source_path ON documents (source_path)",
    "ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS obsidian_vault_path VARCHAR(1024)",
    # ─── Workspace env preference ───
    "ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS env_preference VARCHAR",
    # ─── Guide agent marker ───
    "ALTER TABLE agents ADD COLUMN IF NOT EXISTS is_guide BOOLEAN NOT NULL DEFAULT FALSE",
    # ─── Conversation fork columns ───
    "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS parent_conversation_id TEXT",
    "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS fork_point_message_id TEXT",
    # ─── CLI agent session resume ───
    "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS cli_session_id VARCHAR",
    # ─── ModelProfile table ───
    "DROP INDEX IF EXISTS uq_model_profiles_default_per_user",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_model_profiles_default "
    "ON model_profiles (is_default) WHERE is_default = true",
    # ─── ModelProfile cache_style columns ───
    "ALTER TABLE model_profiles ADD COLUMN IF NOT EXISTS cache_style VARCHAR(16)",
    "ALTER TABLE model_profiles ADD COLUMN IF NOT EXISTS detected_cache_style VARCHAR(16)",
    # ─── Drop model columns from agents (migrated to model_profiles) ───
    "ALTER TABLE agents DROP COLUMN IF EXISTS model_provider",
    "ALTER TABLE agents DROP COLUMN IF EXISTS model_id",
    "ALTER TABLE agents DROP COLUMN IF EXISTS api_key",
    "ALTER TABLE agents DROP COLUMN IF EXISTS api_base_url",
    "ALTER TABLE agents DROP COLUMN IF EXISTS supports_vision",
]

# ── SQLite-compatible migration statements ────────────────────────────
# SQLite doesn't support `IF NOT EXISTS` in ALTER TABLE or `::jsonb` casts.
# Each ALTER TABLE is wrapped in try/except by the caller; failures (column
# already exists) are silently swallowed. New tables get all columns via
# create_all, but existing tables from a prior single-DB run need these
# ALTER TABLE statements to add columns that were introduced in later
# migrations (multi-user, CLI agents, hooks, guide agent, etc.).
_SQLITE_MIGRATION_STATEMENTS = [
    # ─── agents: CLI agent + hooks + MCP + guide + memory columns ───
    "ALTER TABLE agents ADD COLUMN skill_names TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE agents ADD COLUMN executable_path VARCHAR",
    "ALTER TABLE agents ADD COLUMN protocol_family VARCHAR",
    "ALTER TABLE agents ADD COLUMN custom_args TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE agents ADD COLUMN hook_names TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE agents ADD COLUMN mcp_server_ids TEXT NOT NULL DEFAULT '[]'",
    "ALTER TABLE agents ADD COLUMN is_guide BOOLEAN NOT NULL DEFAULT 0",
    "ALTER TABLE agents ADD COLUMN memory_enabled BOOLEAN NOT NULL DEFAULT 0",
    # ─── conversations: summary + dispatch_mode ───
    "ALTER TABLE conversations ADD COLUMN summary TEXT",
    "ALTER TABLE conversations ADD COLUMN dispatch_mode VARCHAR NOT NULL DEFAULT 'solo'",
    "ALTER TABLE conversations ADD COLUMN parent_conversation_id VARCHAR",
    "ALTER TABLE conversations ADD COLUMN fork_point_message_id VARCHAR",
    # ─── messages: multi-user + hidden ───
    "ALTER TABLE messages ADD COLUMN hidden BOOLEAN NOT NULL DEFAULT 0",
    # ─── workspaces: env_preference ───
    "ALTER TABLE workspaces ADD COLUMN env_preference VARCHAR",
    # ─── conversation_context_summaries: session memory columns ───
    "ALTER TABLE conversation_context_summaries ADD COLUMN summary_type VARCHAR(16) NOT NULL DEFAULT 'compaction'",
    "ALTER TABLE conversation_context_summaries ADD COLUMN covers_up_to FLOAT",
    # ─── Drop user_id from local tables (single-user mode) ───
    # Must drop indexes that reference user_id BEFORE the columns,
    # otherwise SQLite raises "error in index ... after drop column".
    "DROP INDEX IF EXISTS idx_agents_user",
    "DROP INDEX IF EXISTS idx_conv_user",
    "DROP INDEX IF EXISTS idx_mcp_user",
    "DROP INDEX IF EXISTS idx_model_profiles_user",
    "ALTER TABLE agents DROP COLUMN user_id",
    "ALTER TABLE conversations DROP COLUMN user_id",
    "ALTER TABLE mcp_servers DROP COLUMN user_id",
    "ALTER TABLE model_profiles DROP COLUMN user_id",
    # ─── agent_runs: CLI session resume ───
    "ALTER TABLE agent_runs ADD COLUMN cli_session_id VARCHAR",
    # ─── model_profiles: unique default ───
    "DROP INDEX IF EXISTS uq_model_profiles_default_per_user",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_model_profiles_default "
    "ON model_profiles (is_default) WHERE is_default = 1",
    # ─── model_profiles: cache_style columns ───
    "ALTER TABLE model_profiles ADD COLUMN cache_style VARCHAR(16)",
    "ALTER TABLE model_profiles ADD COLUMN detected_cache_style VARCHAR(16)",
    # ─── Drop model columns from agents (migrated to model_profiles) ───
    # SQLite 3.35.0+ supports ALTER TABLE DROP COLUMN; wrapped in
    # suppress(Exception) so older SQLite or missing columns are silently
    # skipped. Without this, INSERT fails with "NOT NULL constraint failed:
    # agents.supports_vision" because SQLAlchemy no longer maps these columns.
    "ALTER TABLE agents DROP COLUMN model_provider",
    "ALTER TABLE agents DROP COLUMN model_id",
    "ALTER TABLE agents DROP COLUMN api_key",
    "ALTER TABLE agents DROP COLUMN api_base_url",
    "ALTER TABLE agents DROP COLUMN supports_vision",
]


async def _migrate_columns_pg(conn) -> None:  # type: ignore[no-untyped-def]
    """PG-specific idempotent column additions (ADD COLUMN IF NOT EXISTS + ::jsonb)."""
    from sqlalchemy import text

    for stmt in _PG_MIGRATION_STATEMENTS:
        with contextlib.suppress(Exception):
            await conn.execute(text(stmt))


async def _rebuild_cross_db_fk_tables_sqlite(conn) -> None:
    """Drop local tables that have cross-DB FK constraints to remote tables.

    In dual-DB mode, local SQLite tables cannot enforce FK constraints to
    remote PostgreSQL tables (e.g. ``users``). If a table was previously
    created with such a FK, it must be dropped and recreated by
    ``create_all`` without the FK.
    """
    from sqlalchemy import text

    await conn.execute(text("PRAGMA foreign_keys=OFF"))

    for tbl in ("tasks", "task_comments"):
        result = await conn.execute(text(f"PRAGMA foreign_key_list({tbl})"))
        fks = result.fetchall()
        if any(fk[2] == "users" for fk in fks):
            await conn.execute(text(f"DROP TABLE IF EXISTS {tbl}"))

    await conn.execute(text("PRAGMA foreign_keys=ON"))


async def _migrate_columns_sqlite(conn) -> None:  # type: ignore[no-untyped-def]
    """SQLite-compatible migration: CREATE INDEX IF NOT EXISTS only.

    ALTER TABLE ADD COLUMN on SQLite doesn't support IF NOT EXISTS, so we
    only run index creation here. New columns on new tables are already
    created by create_all. For existing tables, the PG migration path
    handles them (or the column is already present from a prior PG run).
    """
    from sqlalchemy import text

    for stmt in _SQLITE_MIGRATION_STATEMENTS:
        with contextlib.suppress(Exception):
            await conn.execute(text(stmt))


async def close_db() -> None:
    """Close all database engines."""
    global _remote_engine, _remote_session_factory
    global _local_engine, _local_session_factory

    if _local_engine is not None:
        await _local_engine.dispose()
        _local_engine = None
        _local_session_factory = None

    if _remote_engine is not None:
        await _remote_engine.dispose()
        _remote_engine = None
        _remote_session_factory = None


# ── Session getters ────────────────────────────────────────────────────


@asynccontextmanager
async def get_local_db() -> AsyncIterator[AsyncSession]:
    """Get a session for local (SQLite) tables."""
    if _local_session_factory is None:
        raise RuntimeError("Local database not initialized. Call init_db() first.")

    async with _local_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def get_remote_db() -> AsyncIterator[AsyncSession]:
    """Get a session for remote (PostgreSQL) tables."""
    if _remote_session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")

    async with _remote_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# Backward-compatible alias: get_db → get_remote_db (transition period)
get_db = get_remote_db


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency for database session (remote PG).

    Must be a plain async generator (not @asynccontextmanager) so that
    FastAPI's Depends() can consume it correctly.
    """
    if _remote_session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    session = _remote_session_factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
