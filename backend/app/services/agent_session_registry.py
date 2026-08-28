"""AgentSessionRegistry — process-level singleton tracking DAG sessions.

Maintains task_id → AgentSession mappings so that tools (e.g. ask_peer) can
look up sibling sessions within the same DAG. Also provides a mailbox for
the parent Agent to receive messages when peers are unavailable.

Lifecycle: register at DAG node start, update_status on completion, mark_dag_completed
after DAG finishes, cleanup_expired removes stale entries (default 300s TTL).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

SessionStatus = Literal["running", "completed", "failed", "expired"]

_EXPIRY_SECONDS = 300


@dataclass
class AgentSession:
    """Metadata for a single DAG node's execution session."""

    task_id: str
    run_id: str
    agent_id: str
    conversation_id: str
    parent_run_id: str
    dispatch_depth: int
    status: SessionStatus = "running"
    ask_count: int = 0
    created_at: int = 0
    system_prompt: str | None = None


class AgentSessionRegistry:
    """Process-level registry of DAG agent sessions.

    Thread-safety: the asyncio event loop serialises access in practice;
    no lock is needed because all callers run in the same event loop.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, AgentSession] = {}
        self._dag_tasks: dict[str, set[str]] = {}
        self._mailbox: dict[str, list[str]] = {}

    def register(self, task_id: str, session: AgentSession) -> None:
        """Register a session for a DAG node."""
        self._sessions[task_id] = session
        dag_id = self._find_dag_id_for_task(task_id)
        if dag_id:
            self._dag_tasks.setdefault(dag_id, set()).add(task_id)
        logger.debug(
            "[session_registry] registered task=%s run=%s agent=%s",
            task_id,
            session.run_id,
            session.agent_id,
        )

    def register_with_dag(
        self, dag_id: str, task_id: str, session: AgentSession
    ) -> None:
        """Register a session and associate it with a DAG ID."""
        self._sessions[task_id] = session
        self._dag_tasks.setdefault(dag_id, set()).add(task_id)
        logger.debug(
            "[session_registry] registered task=%s run=%s agent=%s dag=%s",
            task_id,
            session.run_id,
            session.agent_id,
            dag_id,
        )

    def get(self, task_id: str) -> AgentSession | None:
        return self._sessions.get(task_id)

    def get_by_dag(self, dag_id: str) -> set[str]:
        """Return all task_ids registered under a DAG."""
        return set(self._dag_tasks.get(dag_id, set()))

    def update_status(self, task_id: str, status: SessionStatus) -> None:
        session = self._sessions.get(task_id)
        if session is not None:
            session.status = status
            logger.debug(
                "[session_registry] updated task=%s status=%s",
                task_id,
                status,
            )

    def set_system_prompt(self, task_id: str, prompt: str) -> None:
        session = self._sessions.get(task_id)
        if session is not None:
            session.system_prompt = prompt

    def get_system_prompt(self, task_id: str) -> str | None:
        session = self._sessions.get(task_id)
        if session is not None:
            return session.system_prompt
        return None

    def add_to_mailbox(self, parent_run_id: str, msg: str) -> None:
        self._mailbox.setdefault(parent_run_id, []).append(msg)

    def drain_mailbox(self, parent_run_id: str) -> list[str]:
        return self._mailbox.pop(parent_run_id, [])

    def mark_dag_completed(self, dag_id: str) -> None:
        """Mark all sessions in a DAG as completed (or failed if still running)."""
        task_ids = self._dag_tasks.get(dag_id, set())
        for tid in task_ids:
            session = self._sessions.get(tid)
            if session is not None and session.status == "running":
                session.status = "completed"
        logger.debug(
            "[session_registry] marked DAG=%s completed (%d tasks)",
            dag_id,
            len(task_ids),
        )

    def cleanup_expired(self, expiry_seconds: int = _EXPIRY_SECONDS) -> None:
        """Remove sessions older than expiry_seconds."""
        from app.utils.clock import now_ms

        now = now_ms()
        cutoff = now - expiry_seconds * 1000
        expired_task_ids = [
            tid
            for tid, s in self._sessions.items()
            if s.created_at < cutoff and s.status in ("completed", "failed")
        ]
        for tid in expired_task_ids:
            session = self._sessions.pop(tid, None)
            if session is not None:
                session.status = "expired"
        # Clean up empty dag sets
        empty_dags = [dag for dag, tasks in self._dag_tasks.items() if not tasks]
        for dag in empty_dags:
            del self._dag_tasks[dag]
        if expired_task_ids:
            logger.debug(
                "[session_registry] cleaned up %d expired sessions",
                len(expired_task_ids),
            )

    def _find_dag_id_for_task(self, task_id: str) -> str | None:
        """Find which DAG a task belongs to (linear scan)."""
        for dag_id, tasks in self._dag_tasks.items():
            if task_id in tasks:
                return dag_id
        return None

    def clear(self) -> None:
        """Clear all state — for testing only."""
        self._sessions.clear()
        self._dag_tasks.clear()
        self._mailbox.clear()


agent_session_registry = AgentSessionRegistry()
