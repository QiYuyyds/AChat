"""Wikilink adjacency graph — SQLite persistence + 1-hop BFS expansion.

Stores source_path → target_path edges in a SQLite table. Supports
adding edges, removing edges for a source, and BFS expansion from
seed files to discover related memory files.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS wikilinks (
    source_path TEXT NOT NULL,
    target_path TEXT NOT NULL,
    PRIMARY KEY (source_path, target_path)
);
CREATE INDEX IF NOT EXISTS idx_wl_source ON wikilinks(source_path);
CREATE INDEX IF NOT EXISTS idx_wl_target ON wikilinks(target_path);
"""


class WikilinkExpander:
    """SQLite-backed wikilink adjacency graph with BFS expansion."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.executescript(_CREATE_TABLE_SQL)
        self._conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def add_edges(self, source_path: str, targets: list[str]) -> None:
        """Add wikilink edges from source_path to each target."""
        if not self._conn or not targets:
            return
        rows = [(source_path, t) for t in targets]
        self._conn.executemany(
            "INSERT OR IGNORE INTO wikilinks (source_path, target_path) VALUES (?, ?)",
            rows,
        )
        self._conn.commit()

    def remove_edges_for(self, source_path: str) -> None:
        """Remove all outgoing edges from source_path."""
        if not self._conn:
            return
        self._conn.execute(
            "DELETE FROM wikilinks WHERE source_path = ?", (source_path,)
        )
        self._conn.commit()

    def remove_all_for(self, path: str) -> None:
        """Remove all edges where path is source or target."""
        if not self._conn:
            return
        self._conn.execute(
            "DELETE FROM wikilinks WHERE source_path = ? OR target_path = ?",
            (path, path),
        )
        self._conn.commit()

    def expand(self, seed_paths: list[str], max_hops: int = 1) -> list[str]:
        """BFS expansion from seed paths.

        Returns paths found via wikilink traversal (excluding the seeds themselves).
        Depth is limited to max_hops (default 1).
        """
        if not self._conn or not seed_paths:
            return []

        visited = set(seed_paths)
        frontier = list(seed_paths)
        results: list[str] = []

        for _hop in range(max_hops):
            if not frontier:
                break
            placeholders = ",".join("?" * len(frontier))
            cursor = self._conn.execute(
                f"SELECT DISTINCT target_path FROM wikilinks WHERE source_path IN ({placeholders})",
                frontier,
            )
            next_frontier: list[str] = []
            for row in cursor.fetchall():
                target = row[0]
                if target not in visited:
                    visited.add(target)
                    results.append(target)
                    next_frontier.append(target)
            frontier = next_frontier

        return results

    def clear(self) -> None:
        """Drop all edges (full reindex)."""
        if not self._conn:
            return
        self._conn.execute("DELETE FROM wikilinks")
        self._conn.commit()
