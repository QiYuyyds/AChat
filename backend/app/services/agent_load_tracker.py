"""AgentLoadTracker — process-level singleton tracking agent concurrent load.

Tracks the number of in-flight tasks per agent and historical average task
duration. Used by the Orchestrator's ``_execute_dag`` for load-aware dispatch
(P2 O7). The tracker is in-memory only; on process restart it starts fresh
and degrades gracefully to static priority sorting.

See openspec/changes/p2-checkpoint-validation-routing/design.md (Decision 6).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class LoadInfo:
    """Per-agent load snapshot."""

    current_tasks: int = 0
    total_tasks: int = 0
    avg_duration_ms: float = 0.0


class AgentLoadTracker:
    """Process-level singleton tracking concurrent task counts per agent.

    Not thread-safe in the traditional sense — relies on Python's GIL for
    dict operations. In an asyncio context, acquire/release are synchronous
    (no await between read and write), so no race conditions.
    """

    def __init__(self) -> None:
        self._current: dict[str, int] = {}
        self._avg_duration_ms: dict[str, float] = {}
        self._initialized: bool = False

    def acquire(self, agent_id: str) -> int:
        """Increment the concurrent task count for an agent. Returns new count."""
        count = self._current.get(agent_id, 0) + 1
        self._current[agent_id] = count
        return count

    def release(self, agent_id: str) -> int:
        """Decrement the concurrent task count for an agent. Returns new count (min 0)."""
        count = self._current.get(agent_id, 0) - 1
        if count <= 0:
            self._current.pop(agent_id, None)
            return 0
        self._current[agent_id] = count
        return count

    def get_load(self, agent_id: str) -> int:
        """Return the current concurrent task count for an agent (0 if untracked)."""
        return self._current.get(agent_id, 0)

    def get_avg_duration_ms(self, agent_id: str) -> float:
        """Return the historical average task duration in ms (0 if unknown)."""
        return self._avg_duration_ms.get(agent_id, 0.0)

    def record_duration(self, agent_id: str, duration_ms: float) -> None:
        """Update the rolling average duration for an agent."""
        # Simple rolling average: keep it lightweight, no history list.
        # This is best-effort; the authoritative source is init_from_db.
        current = self._avg_duration_ms.get(agent_id, 0.0)
        if current == 0.0:
            self._avg_duration_ms[agent_id] = duration_ms
        else:
            self._avg_duration_ms[agent_id] = current * 0.8 + duration_ms * 0.2

    async def init_from_db(self) -> None:
        """Load historical average task duration from AgentRun table.

        Groups completed runs by agent_id and computes average
        (finished_at - started_at) in milliseconds. Best-effort: on failure,
        leaves avg_duration_ms empty (degrades to no duration info).
        """
        if self._initialized:
            return
        try:
            from sqlalchemy import func, select

            from app.db.engine import get_db
            from app.db.models import AgentRun

            async with get_db() as db:
                rows = (
                    await db.execute(
                        select(
                            AgentRun.agent_id,
                            func.avg(AgentRun.finished_at - AgentRun.started_at),
                        )
                        .where(
                            AgentRun.finished_at.isnot(None),
                            AgentRun.status == "complete",
                        )
                        .group_by(AgentRun.agent_id)
                    )
                ).all()

            for row in rows:
                agent_id = row[0]
                avg_duration = row[1]
                if avg_duration is not None:
                    self._avg_duration_ms[agent_id] = float(avg_duration)

            self._initialized = True
            logger.info(
                "AgentLoadTracker initialized: %d agents with duration data",
                len(self._avg_duration_ms),
            )
        except Exception:
            logger.warning("AgentLoadTracker init_from_db failed", exc_info=True)
            self._initialized = True

    def reset(self) -> None:
        """Clear all state. For testing."""
        self._current.clear()
        self._avg_duration_ms.clear()
        self._initialized = False

    def snapshot(self) -> dict[str, LoadInfo]:
        """Return a point-in-time snapshot of all agent loads."""
        all_ids = set(self._current) | set(self._avg_duration_ms)
        return {
            agent_id: LoadInfo(
                current_tasks=self._current.get(agent_id, 0),
                total_tasks=0,
                avg_duration_ms=self._avg_duration_ms.get(agent_id, 0.0),
            )
            for agent_id in all_ids
        }


# Module-level singleton
agent_load_tracker = AgentLoadTracker()
