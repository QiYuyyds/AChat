"""Async DB writer — removed in dual-DB migration.

Redis Stream write-behind has been removed. All events are now written directly
to local SQLite (dual-DB mode) or remote PostgreSQL (server mode).

This module is kept as a stub for backward-compat imports. The functions
register_parts_buffer / unregister_parts_buffer / stream_key / xadd_event /
start_db_writer / stop_db_writer are no-ops.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

STREAM_KEY_PREFIX = "achat:run:"


def register_parts_buffer(run_id: str, buf: dict[str, list[dict]]) -> None:
    """No-op: Redis Stream write-behind removed."""
    pass


def unregister_parts_buffer(run_id: str) -> None:
    """No-op: Redis Stream write-behind removed."""
    pass


def stream_key(run_id: str) -> str:
    """Return the Redis Stream key for a run (kept for backward compat)."""
    return f"{STREAM_KEY_PREFIX}{run_id}"


async def xadd_event(redis_client: Any, run_id: str, event_json: str) -> None:
    """No-op: Redis Stream write-behind removed."""
    pass


async def start_db_writer(redis_client: Any) -> None:
    """No-op: DBWriterConsumer removed."""
    pass


async def stop_db_writer() -> None:
    """No-op: DBWriterConsumer removed."""
    pass
