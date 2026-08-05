"""Wikilink adjacency graph — SQLite persistence + 1-hop BFS expansion.

Stores source_path → target_path edges with optional predicate in a SQLite
table. Supports adding edges, removing edges for a source, BFS expansion
from seed files, and predicate-filtered queries.
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
    predicate TEXT,
    PRIMARY KEY (source_path, target_path, predicate)
);
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_wl_source ON wikilinks(source_path);
CREATE INDEX IF NOT EXISTS idx_wl_target ON wikilinks(target_path);
CREATE INDEX IF NOT EXISTS idx_wl_predicate ON wikilinks(predicate);
"""

_MIGRATE_ADD_PREDICATE_SQL = """
ALTER TABLE wikilinks ADD COLUMN predicate TEXT;
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
        self._ensure_predicate_column()
        self._conn.executescript(_CREATE_INDEX_SQL)
        self._conn.commit()

    def _ensure_predicate_column(self) -> None:
        """Migrate old schema: add predicate column if missing."""
        if not self._conn:
            return
        cursor = self._conn.execute("PRAGMA table_info(wikilinks)")
        columns = {row[1] for row in cursor.fetchall()}
        if "predicate" not in columns:
            try:
                self._conn.execute(_MIGRATE_ADD_PREDICATE_SQL)
                self._conn.execute("CREATE INDEX IF NOT EXISTS idx_wl_predicate ON wikilinks(predicate)")
                self._conn.commit()
                logger.info("wikilinks: migrated schema — added predicate column")
            except sqlite3.OperationalError:
                pass  # column may already exist from concurrent init

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def add_edges(self, source_path: str, targets: list[str], predicate: str | None = None) -> None:
        """Add wikilink edges from source_path to each target.

        If predicate is provided, edges are tagged with the predicate.
        If predicate is None, edges are stored with predicate = NULL.
        """
        if not self._conn or not targets:
            return
        rows = [(source_path, t, predicate) for t in targets]
        self._conn.executemany(
            "INSERT OR IGNORE INTO wikilinks (source_path, target_path, predicate) VALUES (?, ?, ?)",
            rows,
        )
        self._conn.commit()

    def add_edges_detailed(self, source_path: str, links: list[tuple[str, str | None]]) -> None:
        """Add wikilink edges with per-link predicate.

        Args:
            source_path: The source file path.
            links: List of (target, predicate) tuples.
        """
        if not self._conn or not links:
            return
        rows = [(source_path, t, p) for t, p in links]
        self._conn.executemany(
            "INSERT OR IGNORE INTO wikilinks (source_path, target_path, predicate) VALUES (?, ?, ?)",
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

    def expand(
        self,
        seed_paths: list[str],
        max_hops: int = 1,
        *,
        exclude_predicates: set[str] | None = None,
    ) -> list[str]:
        """BFS expansion from seed paths.

        Returns paths found via wikilink traversal (excluding the seeds themselves).
        Depth is limited to max_hops (default 1).

        exclude_predicates: skip edges whose predicate is in this set.
        Provenance edges like ``derived_from`` are useful for lineage display
        but must not inflate keyword search with unrelated sources.
        """
        if not self._conn or not seed_paths:
            return []

        visited = set(seed_paths)
        frontier = list(seed_paths)
        results: list[str] = []
        excluded = exclude_predicates or set()

        for _hop in range(max_hops):
            if not frontier:
                break
            placeholders = ",".join("?" * len(frontier))
            params: list[str] = list(frontier)
            sql = (
                f"SELECT DISTINCT target_path, predicate FROM wikilinks "
                f"WHERE source_path IN ({placeholders})"
            )
            if excluded:
                # Keep NULL / empty predicate edges; only drop named exclusions.
                pred_ph = ",".join("?" * len(excluded))
                sql += (
                    f" AND (predicate IS NULL OR predicate = '' "
                    f"OR predicate NOT IN ({pred_ph}))"
                )
                params.extend(sorted(excluded))
            cursor = self._conn.execute(sql, params)
            next_frontier: list[str] = []
            for row in cursor.fetchall():
                target = row[0]
                if target not in visited:
                    visited.add(target)
                    results.append(target)
                    next_frontier.append(target)
            frontier = next_frontier

        return results

    def get_outlinks(self, path: str, predicate: str | None = None) -> list[dict[str, str | None]]:
        """Get outgoing wikilinks from a path, optionally filtered by predicate.

        Returns list of {target, predicate} dicts.
        """
        if not self._conn:
            return []
        if predicate:
            cursor = self._conn.execute(
                "SELECT target_path, predicate FROM wikilinks WHERE source_path = ? AND predicate = ?",
                (path, predicate),
            )
        else:
            cursor = self._conn.execute(
                "SELECT target_path, predicate FROM wikilinks WHERE source_path = ?",
                (path,),
            )
        return [{"target": row[0], "predicate": row[1]} for row in cursor.fetchall()]

    def get_inlinks(self, path: str, predicate: str | None = None) -> list[dict[str, str | None]]:
        """Get incoming wikilinks to a path, optionally filtered by predicate.

        Returns list of {source, predicate} dicts.
        """
        if not self._conn:
            return []
        if predicate:
            cursor = self._conn.execute(
                "SELECT source_path, predicate FROM wikilinks WHERE target_path = ? AND predicate = ?",
                (path, predicate),
            )
        else:
            cursor = self._conn.execute(
                "SELECT source_path, predicate FROM wikilinks WHERE target_path = ?",
                (path,),
            )
        return [{"source": row[0], "predicate": row[1]} for row in cursor.fetchall()]

    def remove_broken_links(self, existing_paths: set[str]) -> int:
        """Remove adjacency entries where target_path doesn't exist on disk.

        Returns the number of removed entries.
        """
        if not self._conn:
            return 0
        cursor = self._conn.execute("SELECT DISTINCT target_path FROM wikilinks")
        all_targets = {row[0] for row in cursor.fetchall()}
        broken = all_targets - existing_paths
        if broken:
            placeholders = ",".join("?" * len(broken))
            self._conn.execute(
                f"DELETE FROM wikilinks WHERE target_path IN ({placeholders})",
                list(broken),
            )
            self._conn.commit()
            logger.info("Removed %d broken wikilink entries", len(broken))
        return len(broken)

    def clear(self) -> None:
        """Drop all edges (full reindex)."""
        if not self._conn:
            return
        self._conn.execute("DELETE FROM wikilinks")
        self._conn.commit()
