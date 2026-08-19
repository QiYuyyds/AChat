"""SQLite BLOB-based vector storage with brute-force cosine similarity search.

Stores memory file embeddings as packed float32 BLOBs in `<metadata>/vectors.db`.
Search loads all vectors into memory and computes cosine similarity — suitable
for memory-scale datasets (<10k chunks).

Each record: (path, chunk_idx, chunk_text, embedding BLOB, agent_id, bucket).
Primary key: (path, chunk_idx).
"""

from __future__ import annotations

import logging
import math
import sqlite3
import struct
from pathlib import Path

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS memory_vectors (
    path TEXT NOT NULL,
    chunk_idx INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    embedding BLOB NOT NULL,
    agent_id TEXT DEFAULT '',
    bucket TEXT DEFAULT '',
    PRIMARY KEY (path, chunk_idx)
);
CREATE INDEX IF NOT EXISTS idx_mv_agent ON memory_vectors(agent_id);
CREATE INDEX IF NOT EXISTS idx_mv_bucket ON memory_vectors(bucket);
"""


class VectorIndex:
    """SQLite BLOB vector storage with brute-force cosine similarity search."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._dim: int | None = None

    def initialize(self) -> None:
        """Open SQLite connection and create table + indexes."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.executescript(_CREATE_TABLE_SQL)
        self._conn.commit()
        # Detect existing vector dimension from stored data
        self._dim = self._detect_dim()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def _detect_dim(self) -> int | None:
        """Detect embedding dimension from existing stored vectors."""
        if not self._conn:
            return None
        try:
            cursor = self._conn.execute("SELECT embedding FROM memory_vectors LIMIT 1")
            row = cursor.fetchone()
            if row is None:
                return None
            return len(row[0]) // 4  # float32 = 4 bytes
        except sqlite3.OperationalError:
            return None

    def add(
        self,
        path: str,
        chunk_idx: int,
        chunk_text: str,
        embedding: list[float],
        agent_id: str = "",
        bucket: str = "",
    ) -> None:
        """Insert or replace a vector. Validates dimension consistency."""
        if not self._conn:
            return

        dim = len(embedding)
        if self._dim is None:
            self._dim = dim
        elif dim != self._dim:
            logger.warning(
                "VectorIndex: dimension mismatch for %s chunk %d (expected %d, got %d)",
                path, chunk_idx, self._dim, dim,
            )
            return

        blob = struct.pack(f"{dim}f", *embedding)
        self._conn.execute(
            "INSERT OR REPLACE INTO memory_vectors "
            "(path, chunk_idx, chunk_text, embedding, agent_id, bucket) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (path, chunk_idx, chunk_text, blob, agent_id, bucket),
        )
        self._conn.commit()

    def remove(self, path: str) -> None:
        """Remove all chunks for a given path."""
        if not self._conn:
            return
        self._conn.execute("DELETE FROM memory_vectors WHERE path = ?", (path,))
        self._conn.commit()

    def search(
        self,
        query_embedding: list[float],
        top_k: int = 10,
        agent_id: str | None = None,
        bucket: str | None = None,
    ) -> list[tuple[str, int, float]]:
        """Brute-force cosine similarity search.

        Returns list of (path, chunk_idx, score) sorted by score descending.
        """
        if not self._conn:
            return []

        query_dim = len(query_embedding)
        if self._dim is not None and query_dim != self._dim:
            logger.warning(
                "VectorIndex: query dimension mismatch (expected %d, got %d)",
                self._dim, query_dim,
            )
            return []

        # Build query with optional filters
        where_clauses: list[str] = []
        params: list[str] = []
        if agent_id:
            where_clauses.append("(agent_id = ? OR agent_id = '')")
            params.append(agent_id)
        if bucket:
            where_clauses.append("bucket = ?")
            params.append(bucket)

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        sql = f"SELECT path, chunk_idx, embedding FROM memory_vectors {where_sql}"

        try:
            cursor = self._conn.execute(sql, params)
            rows = cursor.fetchall()
        except sqlite3.OperationalError as e:
            logger.debug("VectorIndex search error: %s", e)
            return []

        if not rows:
            return []

        # Compute cosine similarity for each vector
        query_norm = math.sqrt(sum(x * x for x in query_embedding))
        if query_norm == 0:
            return []

        results: list[tuple[str, int, float]] = []
        for path, chunk_idx, blob in rows:
            vec = struct.unpack(f"{len(blob) // 4}f", blob)
            vec_norm = math.sqrt(sum(x * x for x in vec))
            if vec_norm == 0:
                continue
            dot = sum(a * b for a, b in zip(query_embedding, vec, strict=True))
            score = dot / (query_norm * vec_norm)
            results.append((path, chunk_idx, score))

        results.sort(key=lambda x: x[2], reverse=True)
        return results[:top_k]

    def clear(self) -> None:
        """Clear all vectors (full reindex)."""
        if not self._conn:
            return
        self._conn.execute("DELETE FROM memory_vectors")
        self._conn.commit()
        self._dim = None

    def count(self) -> int:
        """Return total number of stored vectors."""
        if not self._conn:
            return 0
        cursor = self._conn.execute("SELECT COUNT(*) FROM memory_vectors")
        return cursor.fetchone()[0]
