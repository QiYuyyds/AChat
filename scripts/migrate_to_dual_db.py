#!/usr/bin/env python3
"""PostgreSQL → SQLite Data Migration Script (Dual-DB Setup).

Migrates the 10 local tables from PostgreSQL to the new local SQLite database.
This is a one-time migration for existing deployments switching to dual-DB mode.

Usage:
    python scripts/migrate_to_dual_db.py [--pg-url URL] [--sqlite-db PATH] [--batch-size N] [--dry-run]

Example:
    python scripts/migrate_to_dual_db.py \
        --pg-url postgresql://agenthub:agenthub@localhost:5432/agenthub \
        --sqlite-db .agenthub-data/agenthub.db \
        --batch-size 500

Local tables (10):
    agents, conversations, messages, agent_runs, agent_run_checkpoints,
    artifacts, workspaces, attachments, conversation_context_summaries, mcp_servers
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

import aiosqlite
import asyncpg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ─── Local tables in migration order (respect FK constraints) ─────────────
# Order: parent tables first, then child tables that reference them.
LOCAL_TABLES: list[str] = [
    "agents",
    "conversations",
    "workspaces",
    "messages",
    "artifacts",
    "attachments",
    "agent_runs",
    "agent_run_checkpoints",
    "conversation_context_summaries",
    "mcp_servers",
]

# ─── JSONB columns per table ──────────────────────────────────────────────
# These columns are stored as JSONB in PG and must be serialized to JSON text
# for SQLite using the canonical format: json.dumps(obj, ensure_ascii=False, separators=(',', ':'))
JSON_COLUMNS: dict[str, list[str]] = {
    "agents": [
        "capabilities",
        "tool_names",
        "custom_args",
        "skill_names",
        "hook_names",
        "mcp_server_ids",
    ],
    "conversations": [
        "agent_ids",
        "pinned_message_ids",
        "bookmarked_message_ids",
    ],
    "messages": [
        "parts",
        "mentioned_agent_ids",
        "usage",
    ],
    "artifacts": [
        "content",
    ],
    "agent_runs": [
        "usage",
        "dispatch_plan",
        "dispatch_results",
    ],
    "agent_run_checkpoints": [
        "messages_json",
    ],
    "mcp_servers": [
        "args",
        "env",
        "headers",
    ],
    # workspaces, attachments, conversation_context_summaries: no JSON columns
}

# ─── Primary key column per table (for batch pagination) ──────────────────
PK_COLUMNS: dict[str, str] = {
    "agents": "id",
    "conversations": "id",
    "workspaces": "id",
    "messages": "id",
    "artifacts": "id",
    "attachments": "id",
    "agent_runs": "id",
    "agent_run_checkpoints": "id",
    "conversation_context_summaries": "id",
    "mcp_servers": "id",
}


def _serialize_json(value: Any) -> str | None:
    """Serialize a Python value to canonical JSON text.

    Uses ensure_ascii=False (preserve UTF-8) and compact separators
    (no whitespace) for consistent storage across PG and SQLite.
    """
    if value is None:
        return None
    if isinstance(value, str):
        # Already a JSON string from PG JSONB — re-serialize for consistency
        try:
            parsed = json.loads(value)
            return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
        except (json.JSONDecodeError, TypeError):
            return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


async def get_pg_table_columns(pg_conn: asyncpg.Connection, table: str) -> list[str]:
    """Get column names for a table from PostgreSQL."""
    rows = await pg_conn.fetch(
        """
        SELECT column_name FROM information_schema.columns
        WHERE table_name = $1
        ORDER BY ordinal_position
        """,
        table,
    )
    return [r["column_name"] for r in rows]


async def get_pg_row_count(pg_conn: asyncpg.Connection, table: str) -> int:
    """Count rows in a PG table."""
    return await pg_conn.fetchval(f"SELECT COUNT(*) FROM {table}")


async def migrate_table(
    pg_conn: asyncpg.Connection,
    sqlite_db: aiosqlite.Connection,
    table: str,
    batch_size: int,
    dry_run: bool = False,
) -> int:
    """Migrate a single table from PG to SQLite.

    Reads from PG in batches (paginated by primary key), serializes JSON
    columns, and writes to SQLite using INSERT OR IGNORE.
    """
    columns = await get_pg_table_columns(pg_conn, table)
    json_cols = set(JSON_COLUMNS.get(table, []))
    pk_col = PK_COLUMNS.get(table, "id")

    total_rows = await get_pg_row_count(pg_conn, table)
    if total_rows == 0:
        logger.info("  Table %s is empty, skipping", table)
        return 0

    logger.info("  Migrating %s: %d rows, %d columns", table, total_rows, len(columns))

    # Build the INSERT OR IGNORE statement
    col_list = ", ".join(columns)
    placeholders = ", ".join(["?" for _ in columns])
    insert_sql = (
        f"INSERT OR IGNORE INTO {table} ({col_list}) "
        f"VALUES ({placeholders})"
    )

    migrated = 0
    last_pk: Any = None

    while True:
        # Paginated read from PG using keyset pagination (more efficient than OFFSET)
        if last_pk is None:
            query = f"SELECT * FROM {table} ORDER BY {pk_col} LIMIT {batch_size}"
            rows = await pg_conn.fetch(query)
        else:
            query = (
                f"SELECT * FROM {table} WHERE {pk_col} > $1 "
                f"ORDER BY {pk_col} LIMIT {batch_size}"
            )
            rows = await pg_conn.fetch(query, last_pk)

        if not rows:
            break

        batch_data: list[tuple] = []
        for row in rows:
            row_dict = dict(row)
            # Serialize JSON columns to canonical JSON text
            for col in json_cols:
                if col in row_dict:
                    row_dict[col] = _serialize_json(row_dict[col])

            # Convert asyncpg Record to tuple in column order
            batch_data.append(tuple(row_dict[c] for c in columns))

        if not dry_run:
            await sqlite_db.executemany(insert_sql, batch_data)
            await sqlite_db.commit()

        migrated += len(rows)
        last_pk = rows[-1][pk_col]

        if migrated % (batch_size * 10) == 0 or migrated >= total_rows:
            logger.info("    %s: %d / %d rows", table, migrated, total_rows)

        if len(rows) < batch_size:
            break

    logger.info("  %s: migrated %d rows", table, migrated)
    return migrated


async def verify_migration(
    pg_conn: asyncpg.Connection,
    sqlite_db: aiosqlite.Connection,
) -> dict[str, dict[str, int]]:
    """Verify row counts match between PG and SQLite."""
    logger.info("Verifying migration...")
    results: dict[str, dict[str, int]] = {}

    for table in LOCAL_TABLES:
        pg_count = await get_pg_row_count(pg_conn, table)

        cursor = await sqlite_db.execute(f"SELECT COUNT(*) FROM {table}")
        sqlite_count = (await cursor.fetchone())[0]

        results[table] = {"pg": pg_count, "sqlite": sqlite_count}

        status = "OK" if pg_count <= sqlite_count else "MISMATCH"
        # Note: INSERT OR IGNORE means sqlite_count >= pg_count if pre-existing rows
        logger.info(
            "  [%s] %s: PG=%d, SQLite=%d",
            status,
            table,
            pg_count,
            sqlite_count,
        )

    return results


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate local tables from PostgreSQL to SQLite (dual-DB setup)"
    )
    parser.add_argument(
        "--pg-url",
        default="postgresql://agenthub:agenthub@localhost:5432/agenthub",
        help="PostgreSQL connection URL (source)",
    )
    parser.add_argument(
        "--sqlite-db",
        default=".agenthub-data/agenthub.db",
        help="Path to SQLite database (destination)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Number of rows per batch (default: 500)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate migration without writing to SQLite",
    )
    args = parser.parse_args()

    sqlite_path = Path(args.sqlite_db).resolve()
    sqlite_dir = sqlite_path.parent
    sqlite_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 60)
    logger.info("PostgreSQL → SQLite Migration (Dual-DB Setup)")
    logger.info("=" * 60)
    logger.info("PostgreSQL (source): %s", args.pg_url)
    logger.info("SQLite (destination): %s", sqlite_path)
    logger.info("Batch size: %d", args.batch_size)
    logger.info("Dry run: %s", args.dry_run)
    logger.info("=" * 60)

    # Connect to PostgreSQL
    pg_conn = await asyncpg.connect(args.pg_url)
    try:
        # Connect to SQLite
        async with aiosqlite.connect(str(sqlite_path)) as sqlite_db:
            # Enable WAL + FK for the migration session
            await sqlite_db.execute("PRAGMA journal_mode=WAL")
            await sqlite_db.execute("PRAGMA foreign_keys=ON")
            await sqlite_db.execute("PRAGMA busy_timeout=5000")

            total_migrated = 0
            for table in LOCAL_TABLES:
                count = await migrate_table(
                    pg_conn, sqlite_db, table,
                    batch_size=args.batch_size,
                    dry_run=args.dry_run,
                )
                total_migrated += count

            logger.info("=" * 60)
            logger.info(
                "Migration complete! Total rows migrated: %d", total_migrated
            )
            logger.info("=" * 60)

            # Verify
            if not args.dry_run:
                await verify_migration(pg_conn, sqlite_db)

    finally:
        await pg_conn.close()

    logger.info("=" * 60)
    logger.info("Migration script finished successfully!")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
