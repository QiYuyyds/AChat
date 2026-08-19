"""Add RAG configuration columns to user_settings table — idempotent.

Adds 4 nullable columns: rag_chunk_preset, rag_chunk_size,
rag_chunk_overlap, ocr_engine. Safe to run multiple times.

PG syntax uses ADD COLUMN IF NOT EXISTS; SQLite uses try/except per
statement (SQLite doesn't support IF NOT EXISTS in ALTER TABLE).
"""

import contextlib
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)

_PG_STATEMENTS = [
    "ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS rag_chunk_preset VARCHAR(32)",
    "ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS rag_chunk_size INTEGER",
    "ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS rag_chunk_overlap INTEGER",
    "ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS ocr_engine VARCHAR(64)",
]

_SQLITE_STATEMENTS = [
    "ALTER TABLE user_settings ADD COLUMN rag_chunk_preset VARCHAR(32)",
    "ALTER TABLE user_settings ADD COLUMN rag_chunk_size INTEGER",
    "ALTER TABLE user_settings ADD COLUMN rag_chunk_overlap INTEGER",
    "ALTER TABLE user_settings ADD COLUMN ocr_engine VARCHAR(64)",
]


async def migrate_user_settings_rag_config() -> None:
    """Add RAG config columns to user_settings (idempotent)."""
    from app.db.engine import _is_sqlite_url, _remote_engine

    if _remote_engine is None:
        logger.warning("user_settings RAG migration: remote engine not initialized, skipping")
        return

    is_sqlite = _is_sqlite_url(str(_remote_engine.url))
    statements = _SQLITE_STATEMENTS if is_sqlite else _PG_STATEMENTS

    async with _remote_engine.begin() as conn:
        for stmt in statements:
            with contextlib.suppress(Exception):
                await conn.execute(text(stmt))

    logger.info("user_settings RAG config migration completed (is_sqlite=%s)", is_sqlite)
