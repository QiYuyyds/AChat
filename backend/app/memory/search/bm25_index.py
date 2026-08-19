"""SQLite FTS5 BM25 full-text index for memory files.

Stores indexed content in `<metadata>/bm25.db`. Uses jieba for Chinese
tokenization and simple tokenizer for English.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

# FTS5 tokenizer: use 'unicode61' for simplicity (works for both CJK and ASCII).
# jieba pre-tokenization is applied before insertion so FTS5 sees space-separated tokens.

_CREATE_TABLE_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    path,
    name,
    content,
    agent_id,
    bucket,
    tags,
    tokenize = 'unicode61'
);
"""


def _is_cjk(ch: str) -> bool:
    return 0x4E00 <= ord(ch) <= 0x9FFF


def _is_indexable_token(token: str) -> bool:
    """Keep tokens that contain letters, digits, or CJK; drop pure punctuation."""
    return any(ch.isalnum() or _is_cjk(ch) for ch in token)


def _fallback_tokenize(text: str) -> list[str]:
    """No-jieba fallback: ASCII words + CJK unigrams and bigrams."""
    tokens: list[str] = []
    word = ""
    cjk_run: list[str] = []

    def flush_word() -> None:
        nonlocal word
        if word:
            tokens.append(word.lower())
            word = ""

    def flush_cjk() -> None:
        nonlocal cjk_run
        if not cjk_run:
            return
        tokens.extend(cjk_run)
        if len(cjk_run) >= 2:
            tokens.extend(cjk_run[i] + cjk_run[i + 1] for i in range(len(cjk_run) - 1))
        cjk_run = []

    for ch in text:
        if _is_cjk(ch):
            flush_word()
            cjk_run.append(ch)
        elif ch.isalnum():
            flush_cjk()
            word += ch
        else:
            flush_word()
            flush_cjk()
    flush_word()
    flush_cjk()
    return tokens


def _tokenize(text: str) -> str:
    """Tokenize text for FTS5 insertion/query.

    Prefer jieba for Chinese word segmentation. Fall back to ASCII words +
    CJK unigram/bigram if jieba is unavailable. Output is space-separated
    so FTS5 unicode61 treats each piece as one token.
    """
    if not text:
        return ""
    try:
        import jieba

        raw = list(jieba.cut(text, cut_all=False))
    except ImportError:
        logger.warning(
            "jieba not installed; using CJK bigram fallback for BM25 tokenization"
        )
        raw = _fallback_tokenize(text)

    tokens: list[str] = []
    for t in raw:
        t = t.strip()
        if not t or not _is_indexable_token(t):
            continue
        if t.isascii():
            t = t.lower()
        tokens.append(t)
    return " ".join(tokens)


class BM25Index:
    """SQLite FTS5-based BM25 index for memory files."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def initialize(self) -> None:
        """Open the SQLite connection and create the FTS5 table."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute(_CREATE_TABLE_SQL)
        self._conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def add(
        self,
        path: str,
        name: str,
        content: str,
        agent_id: str | None,
        bucket: str,
        tags: list[str],
    ) -> None:
        """Insert or replace a document in the FTS5 index."""
        if not self._conn:
            return
        # Delete existing entry for this path
        self._conn.execute("DELETE FROM memory_fts WHERE path = ?", (path,))
        self._conn.execute(
            "INSERT INTO memory_fts (path, name, content, agent_id, bucket, tags) VALUES (?, ?, ?, ?, ?, ?)",
            (
                path,
                _tokenize(name),
                _tokenize(content),
                agent_id or "",
                bucket,
                _tokenize(" ".join(tags)),
            ),
        )
        self._conn.commit()

    def remove(self, path: str) -> None:
        """Remove a document from the index by path."""
        if not self._conn:
            return
        self._conn.execute("DELETE FROM memory_fts WHERE path = ?", (path,))
        self._conn.commit()

    def search(
        self,
        query: str,
        top_k: int = 10,
        agent_id: str | None = None,
        bucket: str | None = None,
    ) -> list[tuple[str, float]]:
        """BM25 search. Returns list of (path, score) tuples sorted by score desc."""
        if not self._conn:
            return []
        tokenized_query = _tokenize(query)
        if not tokenized_query.strip():
            return []

        # Build FTS5 query with optional filters
        fts_query = tokenized_query
        where_clauses = ["memory_fts MATCH ?"]
        params: list[str | int] = [fts_query]

        if bucket:
            where_clauses.append("bucket = ?")
            params.append(bucket)

        if agent_id:
            # agent_id filter: include global (empty) or matching agent_id
            where_clauses.append("(agent_id = ? OR agent_id = '')")
            params.append(agent_id)

        sql = (
            "SELECT path, bm25(memory_fts) as score FROM memory_fts "
            f"WHERE {' AND '.join(where_clauses)} "
            "ORDER BY score ASC LIMIT ?"
        )
        params.append(top_k)

        try:
            cursor = self._conn.execute(sql, params)
            results = [(row[0], -row[1]) for row in cursor.fetchall()]  # negate: bm25 lower = better
            return results
        except sqlite3.OperationalError as e:
            logger.debug("BM25 search error: %s", e)
            return []

    def clear(self) -> None:
        """Drop and recreate the FTS5 table (full reindex)."""
        if not self._conn:
            return
        self._conn.execute("DROP TABLE IF EXISTS memory_fts")
        self._conn.execute(_CREATE_TABLE_SQL)
        self._conn.commit()

    def count(self) -> int:
        if not self._conn:
            return 0
        cursor = self._conn.execute("SELECT COUNT(*) FROM memory_fts")
        return cursor.fetchone()[0]
