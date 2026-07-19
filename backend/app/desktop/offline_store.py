"""Minimal SQLite offline cache + outbox for desktop mode (v1)."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  conversation_id TEXT,
  payload_json TEXT NOT NULL,
  created_at REAL NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  status TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS message_cache (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  role TEXT,
  payload_json TEXT NOT NULL,
  updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""


@dataclass
class OutboxItem:
    id: str
    kind: str
    conversation_id: str | None
    payload: dict[str, Any]
    created_at: float
    attempts: int
    last_error: str | None
    status: str


class OfflineStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def enqueue(
        self,
        kind: str,
        payload: dict[str, Any],
        conversation_id: str | None = None,
    ) -> str:
        item_id = str(uuid.uuid4())
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO outbox (id, kind, conversation_id, payload_json, created_at, status) "
                "VALUES (?, ?, ?, ?, ?, 'pending')",
                (item_id, kind, conversation_id, json.dumps(payload, ensure_ascii=False), now),
            )
        return item_id

    def list_pending(self, limit: int = 50) -> list[OutboxItem]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM outbox WHERE status = 'pending' ORDER BY created_at ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_item(r) for r in rows]

    def mark_done(self, item_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE outbox SET status = 'done', last_error = NULL WHERE id = ?",
                (item_id,),
            )

    def mark_conflict(self, item_id: str, error: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE outbox SET status = 'conflict', last_error = ?, attempts = attempts + 1 WHERE id = ?",
                (error, item_id),
            )

    def mark_failed(self, item_id: str, error: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE outbox SET status = 'failed', last_error = ?, attempts = attempts + 1 WHERE id = ?",
                (error, item_id),
            )

    def cache_message(self, message_id: str, conversation_id: str, role: str, payload: dict[str, Any]) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO message_cache (id, conversation_id, role, payload_json, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (message_id, conversation_id, role, json.dumps(payload, ensure_ascii=False), now),
            )

    def list_conflicts(self, limit: int = 50) -> list[OutboxItem]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM outbox WHERE status = 'conflict' ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_item(r) for r in rows]

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> OutboxItem:
        return OutboxItem(
            id=row["id"],
            kind=row["kind"],
            conversation_id=row["conversation_id"],
            payload=json.loads(row["payload_json"]),
            created_at=row["created_at"],
            attempts=row["attempts"],
            last_error=row["last_error"],
            status=row["status"],
        )
