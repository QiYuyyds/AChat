"""Pending dispatch-plan store.

Port of src/server/pending-dispatch-plans.ts.

When an Orchestrator run produces a plan (``plan_tasks`` tool call) it parks the
plan here and emits ``dispatch.plan.pending``; the run then awaits the user's
decision. The user approves / revises / rejects through the API, which routes
into :meth:`PendingDispatchPlansStore.approve` / ``revise`` / ``reject``; those
hand the outcome back to the waiting run via its registered ``resolver`` and emit
``dispatch.plan.resolved``.

This is an in-memory, single-process store (mirrors the TS ``globalThis``
singleton). The ``resolver`` is whatever callback phase 5's Orchestrator
attaches — typically one that resolves an :class:`asyncio.Future`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Optional

from app.schemas.dispatch import DispatchPlanItem, PendingDispatchPlan
from app.schemas.events import DispatchPlanPendingEvent, DispatchPlanResolvedEvent
from app.services.event_bus import event_bus
from app.utils.clock import now_ms
from app.utils.ids import new_pending_dispatch_plan_id

# A validator re-checks (and may recompile) the plan at approval time.
PlanValidator = Callable[[list[DispatchPlanItem]], list[DispatchPlanItem]]


@dataclass
class PlanReviewOutcome:
    """The user's decision, delivered back to the awaiting Orchestrator run."""

    kind: Literal["approve", "reject", "revise"]
    plan: list[DispatchPlanItem] | None = None
    feedback: str | None = None


PlanResolver = Callable[[PlanReviewOutcome], None]


@dataclass
class PendingDispatchPlanResult:
    ok: bool
    error: str | None = None


@dataclass
class _PendingEntry:
    pending_plan: PendingDispatchPlan
    validator: PlanValidator
    resolver: PlanResolver | None = field(default=None)


class PendingDispatchPlansStore:
    """In-memory registry of dispatch plans awaiting user review."""

    def __init__(self) -> None:
        self._map: dict[str, _PendingEntry] = {}

    def register(
        self,
        *,
        conversation_id: str,
        agent_id: str,
        run_id: str,
        plan: list[DispatchPlanItem],
        validator: PlanValidator,
    ) -> PendingDispatchPlan:
        """Park a plan, emit ``dispatch.plan.pending`` and return the record."""
        pending_id = new_pending_dispatch_plan_id()
        created_at = now_ms()
        pending_plan = PendingDispatchPlan(
            id=pending_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            run_id=run_id,
            plan=plan,
            created_at=created_at,
        )
        self._map[pending_id] = _PendingEntry(
            pending_plan=pending_plan, validator=validator, resolver=None
        )

        event_bus.publish(
            DispatchPlanPendingEvent(
                conversation_id=conversation_id,
                timestamp=created_at,
                pending_plan=pending_plan,
            )
        )
        return pending_plan

    def attach_resolver(self, pending_id: str, resolver: PlanResolver) -> None:
        """Bind the awaiting run's resolver to a parked plan."""
        entry = self._map.get(pending_id)
        if entry is not None:
            entry.resolver = resolver

    def get(self, pending_id: str) -> PendingDispatchPlan | None:
        entry = self._map.get(pending_id)
        return entry.pending_plan if entry else None

    def list_by_conversation(self, conversation_id: str) -> list[PendingDispatchPlan]:
        plans = [
            entry.pending_plan
            for entry in self._map.values()
            if entry.pending_plan.conversation_id == conversation_id
        ]
        plans.sort(key=lambda p: p.created_at)
        return plans

    def approve(self, pending_id: str) -> PendingDispatchPlanResult:
        """Approve: run the registered (read-only) plan through the validator first."""
        entry = self._map.get(pending_id)
        if entry is None:
            return PendingDispatchPlanResult(ok=False, error="Pending dispatch plan not found")

        try:
            compiled_plan = entry.validator(entry.pending_plan.plan)
        except Exception as err:  # noqa: BLE001 - surface validator message to caller
            return PendingDispatchPlanResult(ok=False, error=str(err))

        if entry.resolver is not None:
            entry.resolver(PlanReviewOutcome(kind="approve", plan=compiled_plan))
        self._finalize(pending_id, approved=True)
        return PendingDispatchPlanResult(ok=True)

    def revise(self, pending_id: str, feedback: str) -> bool:
        """Revise: hand natural-language feedback back to the Orchestrator to re-plan."""
        entry = self._map.get(pending_id)
        if entry is None:
            return False
        if entry.resolver is not None:
            entry.resolver(PlanReviewOutcome(kind="revise", feedback=feedback))
        self._finalize(pending_id, approved=False, revising=True)
        return True

    def reject(self, pending_id: str) -> bool:
        entry = self._map.get(pending_id)
        if entry is None:
            return False
        if entry.resolver is not None:
            entry.resolver(PlanReviewOutcome(kind="reject"))
        self._finalize(pending_id, approved=False)
        return True

    def cancel(self, pending_id: str) -> None:
        entry = self._map.get(pending_id)
        if entry is None:
            return
        if entry.resolver is not None:
            entry.resolver(PlanReviewOutcome(kind="reject"))
        self._finalize(pending_id, approved=False)

    def _finalize(self, pending_id: str, *, approved: bool, revising: bool = False) -> None:
        entry = self._map.pop(pending_id, None)
        if entry is None:
            return
        event_bus.publish(
            DispatchPlanResolvedEvent(
                conversation_id=entry.pending_plan.conversation_id,
                timestamp=now_ms(),
                pending_id=pending_id,
                run_id=entry.pending_plan.run_id,
                approved=approved,
                revising=True if revising else None,
            )
        )


# Module-level singleton (mirrors the TS globalThis singleton).
pending_dispatch_plans = PendingDispatchPlansStore()


def get_planner_snapshot() -> Optional["PlannerSnapshot"]:
    """Build a PlannerSnapshot from the most recent pending dispatch plan.

    Returns ``None`` when no plan is pending. Used as the default
    ``PlannerProvider`` callback for ``PlannerSource``.
    """
    entries = list(pending_dispatch_plans._map.values())
    if not entries:
        return None
    # Most recent first
    entries.sort(key=lambda e: e.pending_plan.created_at, reverse=True)
    pp = entries[0].pending_plan
    # Lazy import to avoid circular dependency (prompt_assembler ← pending_dispatch_plans)
    from app.services.prompt_assembler import PlannerSnapshot

    total = len(pp.plan)
    return PlannerSnapshot(
        task_id=pp.id,
        query="",
        status="running",
        phase="planning",
        total_steps=total,
        current_step=0,
    )
