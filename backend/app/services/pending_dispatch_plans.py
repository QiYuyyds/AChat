"""Pending dispatch-plan store.

Port of src/server/pending-dispatch-plans.ts.

Historically, when an Orchestrator run produced a plan (via the now-removed
``plan_tasks`` tool) it parked the plan here and emitted ``dispatch.plan.pending``.
The unified Agent Loop no longer creates new dispatch plans, but this module
is retained because the API layer still references it for backwards-compatible
plan approval flows on legacy conversations. Approve / reject / revise
hand the outcome back to the waiting run via its registered ``resolver`` and
emit ``dispatch.plan.resolved``.

This is an in-memory, single-process store (mirrors the TS ``globalThis``
singleton). The ``resolver`` is whatever callback phase 5's Orchestrator
attaches — typically one that resolves an :class:`asyncio.Future`. The shared
entry-map / resolver skeleton lives in
:mod:`app.services.pending_store_base`; unlike the other five stores, its
decision methods call the resolver themselves and finalize payload-less
(design D2).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from app.schemas.dispatch import DispatchPlanItem, PendingDispatchPlan
from app.schemas.events import DispatchPlanPendingEvent, DispatchPlanResolvedEvent
from app.services.pending_store_base import BasePendingEntry, PendingStoreBase
from app.utils.clock import now_ms
from app.utils.ids import new_pending_dispatch_plan_id

if TYPE_CHECKING:
    from app.services.prompt_assembler import PlannerSnapshot

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
class _PendingEntry(BasePendingEntry):
    validator: PlanValidator


class PendingDispatchPlansStore(PendingStoreBase):
    """In-memory registry of dispatch plans awaiting user review."""

    def register(
        self,
        *,
        conversation_id: str,
        agent_id: str,
        run_id: str,
        plan: list[DispatchPlanItem],
        validator: PlanValidator,
        user_id: str | None = None,
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
        self.register_entry(
            _PendingEntry(payload=pending_plan, validator=validator, user_id=user_id),
            DispatchPlanPendingEvent(
                conversation_id=conversation_id,
                timestamp=created_at,
                pending_plan=pending_plan,
            ),
        )
        return pending_plan

    def approve(
        self,
        pending_id: str,
        modified_plan: list[DispatchPlanItem] | None = None,
    ) -> PendingDispatchPlanResult:
        """Approve: run the plan through the validator first.

        When ``modified_plan`` is provided, it replaces the stored plan before
        validation, allowing the user to edit the DAG in the frontend and
        submit the edited version.
        """
        entry = self._map.get(pending_id)
        if entry is None:
            return PendingDispatchPlanResult(ok=False, error="Pending dispatch plan not found")

        plan = modified_plan if modified_plan is not None else entry.payload.plan

        try:
            compiled_plan = entry.validator(plan)
        except Exception as err:  # noqa: BLE001 - surface validator message to caller
            return PendingDispatchPlanResult(ok=False, error=str(err))

        if entry.resolver is not None:
            entry.resolver(PlanReviewOutcome(kind="approve", plan=compiled_plan))
        self._finalize(
            pending_id,
            resolved_event=self._resolved_event(pending_id, approved=True),
        )
        return PendingDispatchPlanResult(ok=True)

    def revise(self, pending_id: str, feedback: str) -> bool:
        """Revise: hand natural-language feedback back to the Orchestrator to re-plan."""
        entry = self._map.get(pending_id)
        if entry is None:
            return False
        if entry.resolver is not None:
            entry.resolver(PlanReviewOutcome(kind="revise", feedback=feedback))
        self._finalize(
            pending_id,
            resolved_event=self._resolved_event(pending_id, approved=False, revising=True),
        )
        return True

    def reject(self, pending_id: str) -> bool:
        entry = self._map.get(pending_id)
        if entry is None:
            return False
        if entry.resolver is not None:
            entry.resolver(PlanReviewOutcome(kind="reject"))
        self._finalize(
            pending_id,
            resolved_event=self._resolved_event(pending_id, approved=False),
        )
        return True

    def cancel(self, pending_id: str) -> None:
        # 分歧登记（design D3，待产品决策）：dispatch-plans 的 cancel 会发 resolved
        # SSE，与 writes / questions / mcp-calls / merge-conflicts 的静默 cancel
        # 不同。此处用 emit_event=True 保留现状，不在此顺手统一。
        entry = self._map.get(pending_id)
        if entry is None:
            return
        self._cancel(
            pending_id,
            resolver_payload=PlanReviewOutcome(kind="reject"),
            emit_event=True,
            resolved_event=self._resolved_event(pending_id, approved=False),
        )

    def _resolved_event(
        self, pending_id: str, *, approved: bool, revising: bool = False
    ) -> DispatchPlanResolvedEvent:
        entry = self._map[pending_id]
        return DispatchPlanResolvedEvent(
            conversation_id=entry.payload.conversation_id,
            timestamp=now_ms(),
            pending_id=pending_id,
            run_id=entry.payload.run_id,
            approved=approved,
            revising=True if revising else None,
        )


# Module-level singleton (mirrors the TS globalThis singleton).
pending_dispatch_plans = PendingDispatchPlansStore()


def get_planner_snapshot() -> PlannerSnapshot | None:
    """Build a PlannerSnapshot from the most recent pending dispatch plan.

    Returns ``None`` when no plan is pending. Used as the default
    ``PlannerProvider`` callback for ``PlannerSource``.
    """
    entries = list(pending_dispatch_plans._map.values())
    if not entries:
        return None
    # Most recent first
    entries.sort(key=lambda e: e.payload.created_at, reverse=True)
    pp = entries[0].payload
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
