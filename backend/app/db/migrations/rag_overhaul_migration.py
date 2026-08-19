"""RAG overhaul foundation migration — idempotent schema extension.

Adds new columns to ``documents`` and ``rag_chunks`` tables and backfills
existing rows with default values. Safe to run multiple times.

PG syntax uses ``ADD COLUMN IF NOT EXISTS``; SQLite uses try/except per
statement (SQLite doesn't support IF NOT EXISTS in ALTER TABLE).
"""

import contextlib
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)

_PG_STATEMENTS = [
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS chunk_preset VARCHAR(32) NOT NULL DEFAULT 'general'",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS graph_status VARCHAR(16)",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS parent_id VARCHAR(64)",
    "ALTER TABLE documents ADD COLUMN IF NOT EXISTS is_folder BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS chunk_token_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS start_char_pos INTEGER",
    "ALTER TABLE rag_chunks ADD COLUMN IF NOT EXISTS end_char_pos INTEGER",
]

_PG_BACKFILL = [
    "UPDATE documents SET chunk_preset = 'general' WHERE chunk_preset IS NULL",
    "UPDATE documents SET graph_status = 'graph_indexed' WHERE status = 'active' AND graph_status IS NULL",
    "UPDATE documents SET is_folder = FALSE WHERE is_folder IS NULL",
]

_SQLITE_STATEMENTS = [
    "ALTER TABLE documents ADD COLUMN chunk_preset VARCHAR(32) NOT NULL DEFAULT 'general'",
    "ALTER TABLE documents ADD COLUMN graph_status VARCHAR(16)",
    "ALTER TABLE documents ADD COLUMN parent_id VARCHAR(64)",
    "ALTER TABLE documents ADD COLUMN is_folder BOOLEAN NOT NULL DEFAULT FALSE",
    "ALTER TABLE rag_chunks ADD COLUMN chunk_token_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE rag_chunks ADD COLUMN start_char_pos INTEGER",
    "ALTER TABLE rag_chunks ADD COLUMN end_char_pos INTEGER",
]


async def migrate_rag_overhaul() -> None:
    """Run RAG overhaul schema migration on the remote (PG) engine.

    Idempotent: safe to run multiple times. On fresh databases, columns
    already exist from ``create_all`` so all statements are no-ops.

    Also clears existing rag_chunks data (Milvus Collection schema changed,
    old embeddings are incompatible with new dense+BM25 schema).
    """
    from app.db.engine import _is_sqlite_url, _remote_engine

    if _remote_engine is None:
        logger.warning("RAG overhaul migration: remote engine not initialized, skipping")
        return

    is_sqlite = _is_sqlite_url(str(_remote_engine.url))
    statements = _SQLITE_STATEMENTS if is_sqlite else _PG_STATEMENTS

    logger.warning(
        "WARNING: RAG data will be deleted. Existing documents need to be re-uploaded."
    )

    async with _remote_engine.begin() as conn:
        with contextlib.suppress(Exception):
            await conn.execute(text("DELETE FROM rag_chunks"))

        for stmt in statements:
            with contextlib.suppress(Exception):
                await conn.execute(text(stmt))

        if not is_sqlite:
            for stmt in _PG_BACKFILL:
                with contextlib.suppress(Exception):
                    await conn.execute(text(stmt))

    logger.info("RAG overhaul migration completed (is_sqlite=%s)", is_sqlite)
