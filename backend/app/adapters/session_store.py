"""Adapter session caches.

Port of src/server/adapters/session-store.ts.

Agent platform SDKs (Claude Code, Codex) keep their own server-side session so
follow-up turns reuse context. We cache the session id keyed by conversation +
agent:
  - claude-code: keyed by ``conversation_id:agent_id`` (DB-backed cache layer)
  - codex:       keyed by ``conversation_id:agent_id``

conversation_service clears these whenever the DB history diverges from what the
SDK remembers (delete / clear / withdraw / regenerate), otherwise the SDK would
replay a now-deleted "user msg → agent reply" pair.

The claude_code_sessions dict is a hot-path cache layer on top of the DB
``agent_runs.cli_session_id`` column. On cache miss, ``get_claude_code_session``
queries the DB for the latest run's session ID and populates the cache.
"""

import logging

from sqlalchemy import select

from app.db.engine import get_local_db
from app.db.models import AgentRun

logger = logging.getLogger(__name__)

# In-process singletons (single-user, local-first). Module globals mirror the
# TypeScript ``globalThis`` singletons.
claude_code_sessions: dict[str, str] = {}
codex_sessions: dict[str, str] = {}


def adapter_session_key(conversation_id: str, agent_id: str) -> str:
    """Build the composite key used by per-agent session stores."""
    return f"{conversation_id}:{agent_id}"


async def get_claude_code_session(conversation_id: str, agent_id: str) -> str | None:
    """Return the cached or DB-persisted Claude Code session ID.

    Hot path: check the in-memory dict first. On miss, query the latest
    ``AgentRun`` with a non-NULL ``cli_session_id`` for this conversation +
    agent, populate the cache, and return the value.
    """
    key = adapter_session_key(conversation_id, agent_id)
    cached = claude_code_sessions.get(key)
    if cached:
        return cached
    try:
        async with get_local_db() as db:
            row = (
                await db.execute(
                    select(AgentRun.cli_session_id)
                    .where(
                        AgentRun.conversation_id == conversation_id,
                        AgentRun.agent_id == agent_id,
                        AgentRun.cli_session_id.is_not(None),
                    )
                    .order_by(AgentRun.started_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if row:
            claude_code_sessions[key] = row
            return row
    except Exception:
        logger.debug(
            "[session_store] DB query for cli_session_id failed (conversation=%s)",
            conversation_id,
            exc_info=True,
        )
    return None


def set_claude_code_session(conversation_id: str, agent_id: str, session_id: str) -> None:
    """Populate the in-memory cache after a run captures a session ID."""
    key = adapter_session_key(conversation_id, agent_id)
    claude_code_sessions[key] = session_id


def clear_claude_code_session(conversation_id: str) -> None:
    """Drop all cached claude-code sessions for a conversation.

    Clears every ``conversation_id:*`` entry (all agents in group chats).
    The DB column is not cleared — old runs retain their ``cli_session_id``,
    and the next run's DB query picks the latest surviving run.
    """
    prefix = f"{conversation_id}:"
    for key in [k for k in claude_code_sessions if k.startswith(prefix)]:
        del claude_code_sessions[key]


def clear_codex_session(conversation_id: str) -> None:
    """Drop every cached codex session belonging to a conversation."""
    prefix = f"{conversation_id}:"
    for key in [k for k in codex_sessions if k.startswith(prefix)]:
        del codex_sessions[key]
