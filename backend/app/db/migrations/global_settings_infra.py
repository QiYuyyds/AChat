"""Add infra connection columns to global_settings table — idempotent.

rag-infra-config: adds 6 nullable columns (milvus_host, milvus_port,
neo4j_uri, neo4j_user, neo4j_password, enable_graph). NULL = 未配置，
回落 env 默认值。Safe to run multiple times.

Must run BEFORE infra/factory.build_infrastructure() — the factory reads
these columns via global_settings_service. Fresh databases get the columns
from create_all; this migration covers tables created before this change.

PG syntax uses ADD COLUMN IF NOT EXISTS; SQLite uses try/except per
statement (SQLite doesn't support IF NOT EXISTS in ALTER TABLE).
"""

import contextlib
import logging

from sqlalchemy import text

logger = logging.getLogger(__name__)

_PG_STATEMENTS = [
    "ALTER TABLE global_settings ADD COLUMN IF NOT EXISTS milvus_host VARCHAR",
    "ALTER TABLE global_settings ADD COLUMN IF NOT EXISTS milvus_port INTEGER",
    "ALTER TABLE global_settings ADD COLUMN IF NOT EXISTS neo4j_uri VARCHAR",
    "ALTER TABLE global_settings ADD COLUMN IF NOT EXISTS neo4j_user VARCHAR",
    "ALTER TABLE global_settings ADD COLUMN IF NOT EXISTS neo4j_password VARCHAR",
    "ALTER TABLE global_settings ADD COLUMN IF NOT EXISTS enable_graph BOOLEAN",
]

_SQLITE_STATEMENTS = [
    "ALTER TABLE global_settings ADD COLUMN milvus_host VARCHAR",
    "ALTER TABLE global_settings ADD COLUMN milvus_port INTEGER",
    "ALTER TABLE global_settings ADD COLUMN neo4j_uri VARCHAR",
    "ALTER TABLE global_settings ADD COLUMN neo4j_user VARCHAR",
    "ALTER TABLE global_settings ADD COLUMN neo4j_password VARCHAR",
    "ALTER TABLE global_settings ADD COLUMN enable_graph BOOLEAN",
]


async def migrate_global_settings_infra() -> None:
    """Add infra connection columns to global_settings (idempotent)."""
    from app.db.engine import _is_sqlite_url, _remote_engine

    if _remote_engine is None:
        logger.warning("global_settings infra migration: remote engine not initialized, skipping")
        return

    is_sqlite = _is_sqlite_url(str(_remote_engine.url))
    statements = _SQLITE_STATEMENTS if is_sqlite else _PG_STATEMENTS

    async with _remote_engine.begin() as conn:
        for stmt in statements:
            with contextlib.suppress(Exception):
                await conn.execute(text(stmt))

    logger.info("global_settings infra config migration completed (is_sqlite=%s)", is_sqlite)
