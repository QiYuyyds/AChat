"""Add structured memory fields (summary, keywords, content_scope) to long_term_memory.

Adds three columns:
- summary TEXT DEFAULT ''
- keywords JSONB DEFAULT '[]'
- content_scope TEXT DEFAULT ''

Also creates a partial index on content_scope for non-empty values.

Usage::

    cd backend
    python -m scripts.migrate_structured_memory_fields

Environment variables:
    DATABASE_URL — PostgreSQL connection string
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

# Ensure backend/ is on sys.path when run as a script
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text

from app.config import get_settings
from app.db.engine import init_db

logger = logging.getLogger(__name__)

# SQL statements — each is idempotent (IF NOT EXISTS)
_ADD_SUMMARY = text(
    "ALTER TABLE long_term_memory "
    "ADD COLUMN IF NOT EXISTS summary TEXT NOT NULL DEFAULT ''"
)
_ADD_KEYWORDS = text(
    "ALTER TABLE long_term_memory "
    "ADD COLUMN IF NOT EXISTS keywords JSONB NOT NULL DEFAULT '[]'::jsonb"
)
_ADD_CONTENT_SCOPE = text(
    "ALTER TABLE long_term_memory "
    "ADD COLUMN IF NOT EXISTS content_scope TEXT NOT NULL DEFAULT ''"
)
_CREATE_INDEX = text(
    "CREATE INDEX IF NOT EXISTS idx_ltm_content_scope "
    "ON long_term_memory (content_scope) "
    "WHERE content_scope IS NOT NULL AND content_scope != ''"
)


async def migrate() -> None:
    """Run the migration: add columns + index."""
    get_settings()
    await init_db()

    from app.db.engine import get_db

    async with get_db() as session:
        logger.info("Adding summary column...")
        await session.execute(_ADD_SUMMARY)

        logger.info("Adding keywords column...")
        await session.execute(_ADD_KEYWORDS)

        logger.info("Adding content_scope column...")
        await session.execute(_ADD_CONTENT_SCOPE)

        logger.info("Creating content_scope partial index...")
        await session.execute(_CREATE_INDEX)

        await session.commit()

    logger.info("Migration complete: long_term_memory now has summary, keywords, content_scope columns.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(migrate())
