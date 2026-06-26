#!/usr/bin/env python3
"""SQLite → PostgreSQL Data Migration Script.

Migrates all existing data from SQLite database to PostgreSQL.
This script is OPTIONAL - new users can start directly with PostgreSQL.

Usage:
    python scripts/migrate_sqlite_to_pg.py [--sqlite-db PATH] [--pg-url URL] [--dry-run]

Example:
    python scripts/migrate_sqlite_to_pg.py \\
        --sqlite-db .agenthub-data/agenthub.db \\
        --pg-url postgresql://agenthub:agenthub@localhost:5432/agenthub
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

# Tables to migrate (in order to respect FK constraints)
TABLES = [
    "agents",
    "conversations",
    "messages",
    "message_parts",
    "artifacts",
    "artifact_versions",
    "agent_runs",
    "deployments",
    "workspace_settings",
]

# JSON columns that need special handling
JSON_COLUMNS = {
    "agents": ["config", "metadata"],
    "conversations": ["metadata", "pinned_message_ids"],
    "messages": ["metadata"],
    "agent_runs": ["input", "output", "metadata"],
    "deployments": ["metadata", "preview"],
    "workspace_settings": ["value"],
}


async def get_sqlite_tables(sqlite_path: str) -> list[str]:
    """Get list of tables in SQLite database."""
    async with aiosqlite.connect(sqlite_path) as db:
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        rows = await cursor.fetchall()
        return [row[0] for row in rows]


async def migrate_table(
    sqlite_path: str,
    pg_conn: asyncpg.Connection,
    table: str,
    dry_run: bool = False,
) -> int:
    """Migrate a single table from SQLite to PostgreSQL."""
    logger.info("Migrating table: %s", table)

    async with aiosqlite.connect(sqlite_path) as sqlite_db:
        sqlite_db.row_factory = aiosqlite.Row
        cursor = await sqlite_db.execute(f"SELECT * FROM {table}")
        rows = await cursor.fetchall()

    if not rows:
        logger.info("  Table %s is empty, skipping", table)
        return 0

    columns = [desc[0] for desc in cursor.description]
    json_cols = JSON_COLUMNS.get(table, [])

    migrated = 0
    for row in rows:
        data = dict(zip(columns, row))

        # Convert JSON text columns to proper JSON
        for col in json_cols:
            if col in data and data[col] is not None:
                try:
                    # SQLite stores JSON as text, parse and re-serialize
                    data[col] = json.loads(data[col])
                except (json.JSONDecodeError, TypeError):
                    logger.warning(
                        "  Invalid JSON in %s.%s: %s", table, col, data[col]
                    )
                    data[col] = None

        # Remove SQLite internal columns
        data.pop("rowid", None)

        if dry_run:
            migrated += 1
            continue

        # Build INSERT query
        cols = list(data.keys())
        placeholders = [f"${i+1}" for i in range(len(cols))]
        values = list(data.values())

        # Convert bool to int for PostgreSQL (SQLite stores bool as 0/1)
        for i, val in enumerate(values):
            if isinstance(val, bool):
                values[i] = int(val)

        query = f"""
            INSERT INTO {table} ({', '.join(cols)})
            VALUES ({', '.join(placeholders)})
            ON CONFLICT DO NOTHING
        """

        try:
            await pg_conn.execute(query, *values)
            migrated += 1
        except Exception as e:
            logger.error("  Failed to insert row in %s: %s", table, e)

    logger.info("  Migrated %d rows from %s", migrated, table)
    return migrated


async def verify_migration(
    sqlite_path: str,
    pg_conn: asyncpg.Connection,
) -> dict[str, dict[str, int]]:
    """Verify row counts match between SQLite and PostgreSQL."""
    logger.info("Verifying migration...")
    results = {}

    async with aiosqlite.connect(sqlite_path) as sqlite_db:
        for table in TABLES:
            cursor = await sqlite_db.execute(f"SELECT COUNT(*) FROM {table}")
            sqlite_count = (await cursor.fetchone())[0]

            pg_count = await pg_conn.fetchval(f"SELECT COUNT(*) FROM {table}")

            results[table] = {"sqlite": sqlite_count, "postgresql": pg_count}

            status = "✓" if sqlite_count == pg_count else "✗"
            logger.info(
                "  %s %s: SQLite=%d, PostgreSQL=%d",
                status,
                table,
                sqlite_count,
                pg_count,
            )

    return results


async def main():
    parser = argparse.ArgumentParser(description="Migrate SQLite → PostgreSQL")
    parser.add_argument(
        "--sqlite-db",
        default="../.agenthub-data/agenthub.db",
        help="Path to SQLite database",
    )
    parser.add_argument(
        "--pg-url",
        default="postgresql://agenthub:agenthub@localhost:5432/agenthub",
        help="PostgreSQL connection URL",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate migration without writing to PostgreSQL",
    )
    args = parser.parse_args()

    sqlite_path = Path(args.sqlite_db).resolve()
    if not sqlite_path.exists():
        logger.error("SQLite database not found: %s", sqlite_path)
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("SQLite → PostgreSQL Migration")
    logger.info("=" * 60)
    logger.info("SQLite: %s", sqlite_path)
    logger.info("PostgreSQL: %s", args.pg_url)
    logger.info("Dry Run: %s", args.dry_run)
    logger.info("=" * 60)

    # Verify SQLite tables exist
    sqlite_tables = await get_sqlite_tables(str(sqlite_path))
    logger.info("SQLite tables: %s", ", ".join(sqlite_tables))

    # Connect to PostgreSQL
    if not args.dry_run:
        pg_conn = await asyncpg.connect(args.pg_url)
        try:
            total_migrated = 0
            for table in TABLES:
                if table in sqlite_tables:
                    count = await migrate_table(
                        str(sqlite_path), pg_conn, table, dry_run=False
                    )
                    total_migrated += count
                else:
                    logger.info("Table %s not found in SQLite, skipping", table)

            logger.info("=" * 60)
            logger.info("Migration complete! Total rows migrated: %d", total_migrated)

            # Verify
            await verify_migration(str(sqlite_path), pg_conn)

        finally:
            await pg_conn.close()
    else:
        logger.info("Dry run mode - no data will be written")
        # Create a dummy connection for counting
        pg_conn = await asyncpg.connect(args.pg_url)
        try:
            for table in TABLES:
                if table in sqlite_tables:
                    await migrate_table(
                        str(sqlite_path), pg_conn, table, dry_run=True
                    )
        finally:
            await pg_conn.close()

    logger.info("=" * 60)
    logger.info("Migration script finished successfully!")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
