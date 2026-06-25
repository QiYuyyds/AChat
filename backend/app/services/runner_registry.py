"""Late-bound AgentRunner registry.

``conversation_service`` needs to start and abort agent runs, but the concrete
:class:`AgentRunner` lands in a later phase (阶段 5). This registry breaks that
forward dependency the same way the TypeScript original did with a lazy
``import('./agent-runner')``: phase 5 calls :func:`set_agent_runner`, and until
then a no-op runner keeps ``send_message`` / ``abort_run`` working end-to-end
instead of crashing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.utils.ids import new_run_id

logger = logging.getLogger(__name__)


@dataclass
class RunHandle:
    """Returned by :meth:`AgentRunner.run` — the id of the spawned run."""

    run_id: str


@runtime_checkable
class AgentRunner(Protocol):
    """The slice of AgentRunner that conversation_service depends on.

    ``run`` is synchronous: it spawns the run (as an asyncio task in the real
    implementation) and returns its handle immediately, matching the TS
    ``AgentRunner.run(...) -> { runId }`` contract.
    """

    def run(
        self,
        *,
        agent_id: str,
        conversation_id: str,
        trigger_message_id: str,
        parent_run_id: str | None = None,
    ) -> RunHandle: ...

    def abort(self, run_id: str) -> bool: ...


class _NoopAgentRunner:
    """Default runner used before phase 5 wires the real one in.

    ``run`` mints a run id and logs loudly but does no LLM work, so a user
    message still gets a consistent ``runIds`` response. Because it never writes
    an ``agent_runs`` row, the withdraw / regenerate time-window queries simply
    find nothing to abort or delete — no inconsistency.
    """

    def run(
        self,
        *,
        agent_id: str,
        conversation_id: str,
        trigger_message_id: str,
        parent_run_id: str | None = None,
    ) -> RunHandle:
        run_id = new_run_id()
        logger.warning(
            "AgentRunner not registered (phase 5 pending); no-op run %s for "
            "agent=%s conversation=%s trigger=%s",
            run_id,
            agent_id,
            conversation_id,
            trigger_message_id,
        )
        return RunHandle(run_id=run_id)

    def abort(self, run_id: str) -> bool:
        return False


_runner: AgentRunner = _NoopAgentRunner()


def set_agent_runner(runner: AgentRunner) -> None:
    """Install the concrete AgentRunner (called from phase 5 wiring)."""
    global _runner
    _runner = runner


def get_agent_runner() -> AgentRunner:
    """Return the currently-registered runner (no-op until phase 5)."""
    return _runner
