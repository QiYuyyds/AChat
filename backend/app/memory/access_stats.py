"""Access statistics sidecar — SQLite tracking of memory file access frequency.

Stores `(path TEXT PRIMARY KEY, last_accessed REAL, access_count INTEGER)` in
`<metadata>/access_stats.db`. HybridSearch calls `record(path)` fire-and-forget
on each recall hit to feed the rerank formula and curator effective-score
calculation.

IMPORTANT: This sidecar MUST NEVER write to memory card files or frontmatter.
Frontmatter changes would pollute FileCatalog's mtime change detection and
create a "recall → dream re-process" LLM load feedback loop.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS access_stats (
    path TEXT PRIMARY KEY,
    last_accessed REAL NOT NULL DEFAULT 0,
    access_count INTEGER NOT NULL DEFAULT 0
);
"""

# Watermark table for curator's "consecutive N days below threshold" tracking
_CREATE_WATERMARK_SQL = """
CREATE TABLE IF NOT EXISTS access_watermark (
    path TEXT PRIMARY KEY,
    first_below_since REAL
);
"""


class AccessStats:
    """SQLite-backed access statistics for memory files."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute(_CREATE_TABLE_SQL)
        self._conn.execute(_CREATE_WATERMARK_SQL)
        self._conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def record(self, path: str) -> None:
        """Upsert access record: increment count and update last_accessed.

        Called fire-and-forget from HybridSearch. Failures are logged but
        must never propagate to the caller.
        """
        if not self._conn or not path:
            return
        try:
            now = time.time()
            existing = self._conn.execute(
                "SELECT access_count FROM access_stats WHERE path = ?", (path,)
            ).fetchone()
            if existing:
                self._conn.execute(
                    "UPDATE access_stats SET last_accessed = ?, access_count = ? WHERE path = ?",
                    (now, existing[0] + 1, path),
                )
            else:
                self._conn.execute(
                    "INSERT INTO access_stats (path, last_accessed, access_count) VALUES (?, ?, 1)",
                    (path, now),
                )
            self._conn.commit()
        except Exception as e:
            logger.warning("AccessStats.record failed for %s: %s", path, e)

    def get(self, path: str) -> dict[str, float] | None:
        """Get access stats for a path. Returns None if no record exists."""
        if not self._conn or not path:
            return None
        try:
            cursor = self._conn.execute(
                "SELECT last_accessed, access_count FROM access_stats WHERE path = ?",
                (path,),
            )
            row = cursor.fetchone()
            if row:
                return {"last_accessed": row[0], "access_count": row[1]}
            return None
        except Exception as e:
            logger.warning("AccessStats.get failed for %s: %s", path, e)
            return None

    def get_all(self) -> dict[str, dict[str, float]]:
        """Return all access stats as {path: {last_accessed, access_count}}."""
        if not self._conn:
            return {}
        try:
            cursor = self._conn.execute(
                "SELECT path, last_accessed, access_count FROM access_stats"
            )
            return {row[0]: {"last_accessed": row[1], "access_count": row[2]} for row in cursor.fetchall()}
        except Exception as e:
            logger.warning("AccessStats.get_all failed: %s", e)
            return {}

    # ─── Watermark helpers for curator effective-score grace tracking ───

    def get_watermark(self, path: str) -> float | None:
        """Get the first_below_since timestamp for a path (None if not set)."""
        if not self._conn or not path:
            return None
        try:
            cursor = self._conn.execute(
                "SELECT first_below_since FROM access_watermark WHERE path = ?",
                (path,),
            )
            row = cursor.fetchone()
            return row[0] if row else None
        except Exception as e:
            logger.warning("AccessStats.get_watermark failed for %s: %s", path, e)
            return None

    def set_watermark(self, path: str, timestamp: float | None) -> None:
        """Set or clear the first_below_since watermark for a path."""
        if not self._conn or not path:
            return
        try:
            if timestamp is None:
                self._conn.execute(
                    "DELETE FROM access_watermark WHERE path = ?", (path,)
                )
            else:
                self._conn.execute(
                    "INSERT OR REPLACE INTO access_watermark (path, first_below_since) VALUES (?, ?)",
                    (path, timestamp),
                )
            self._conn.commit()
        except Exception as e:
            logger.warning("AccessStats.set_watermark failed for %s: %s", path, e)
