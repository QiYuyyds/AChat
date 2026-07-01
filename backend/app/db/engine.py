"""SQLAlchemy async database engine and session management."""

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

# Global engine and session factory
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


async def init_db() -> None:
    """Initialize database engine and create tables if needed."""
    global _engine, _session_factory

    settings = get_settings()

    # PostgreSQL (asyncpg) engine configuration
    _engine = create_async_engine(
        settings.database_url,
        echo=False,
        future=True,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
    )

    # SQLite needs per-connection PRAGMAs for FK cascade + WAL concurrency.
    if settings.database_url.startswith("sqlite"):

        @event.listens_for(_engine.sync_engine, "connect")
        def _init_sqlite_connection(dbapi_connection, connection_record):  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    _session_factory = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )

    # Create tables if they don't exist
    from app.db.models import Base

    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate_columns(conn)


async def _migrate_columns(conn) -> None:  # type: ignore[no-untyped-def]
    """Idempotent column additions for tables that predate a new field.

    ``create_all`` only creates missing tables; it never alters existing ones.
    Postgres supports ``ADD COLUMN IF NOT EXISTS``; SQLite (tests/dev) does not,
    so failures there are swallowed (the column is created fresh via create_all).
    """
    import contextlib

    from sqlalchemy import text

    statements = [
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS skill_names JSONB NOT NULL DEFAULT '[]'::jsonb",
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS executable_path VARCHAR",
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS protocol_family VARCHAR",
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS custom_args JSONB NOT NULL DEFAULT '[]'::jsonb",
        "ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS content_hash VARCHAR(16)",
        "CREATE INDEX IF NOT EXISTS idx_rag_content_hash ON rag_chunks (content_hash)",
        "ALTER TABLE conversations ADD COLUMN IF NOT EXISTS summary TEXT",
    ]
    for stmt in statements:
        # dialects without IF NOT EXISTS (sqlite) / pre-existing column → no-op
        with contextlib.suppress(Exception):
            await conn.execute(text(stmt))


async def close_db() -> None:
    """Close database engine."""
    global _engine, _session_factory

    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


@asynccontextmanager
async def get_db() -> AsyncIterator[AsyncSession]:
    """Get async database session as context manager."""
    if _session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")

    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency for database session."""
    async with get_db() as session:
        yield session
