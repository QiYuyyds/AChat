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
    _engine = create_async_engine(
        settings.database_url,
        echo=settings.debug,
        future=True,
    )

    # SQLite enforces foreign keys (and thus ON DELETE CASCADE) only when the
    # PRAGMA is set per-connection. Without this, deleting a conversation would
    # NOT cascade to its messages/artifacts/runs and would instead error or
    # orphan rows. Enable it on every pooled connection.
    if settings.database_url.startswith("sqlite"):

        @event.listens_for(_engine.sync_engine, "connect")
        def _init_sqlite_connection(dbapi_connection, connection_record):  # type: ignore[no-untyped-def]
            cursor = dbapi_connection.cursor()
            # FK cascade needs the pragma per-connection (see note above).
            cursor.execute("PRAGMA foreign_keys=ON")
            # WAL = concurrent readers alongside one writer; busy_timeout makes a
            # blocked writer wait for the lock instead of erroring "database is
            # locked" — the runner spawns concurrent run-tasks that all write.
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
