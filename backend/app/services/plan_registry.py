"""In-memory plan registry for execution plan state.

Stores plan state keyed by planId. Populated by create_plan tool,
read/updated by plan_step and add_plan_steps tools, cleaned up on run end.
"""

from __future__ import annotations

from app.schemas.plan import PlanState


class PlanRegistry:
    """Lightweight in-memory registry for active execution plans."""

    def __init__(self) -> None:
        self._plans: dict[str, PlanState] = {}

    def register(self, plan: PlanState) -> None:
        self._plans[plan.plan_id] = plan

    def get(self, plan_id: str) -> PlanState | None:
        return self._plans.get(plan_id)

    def update(self, plan: PlanState) -> None:
        self._plans[plan.plan_id] = plan

    def remove(self, plan_id: str) -> PlanState | None:
        return self._plans.pop(plan_id, None)

    def find_plan_by_step(self, step_id: str) -> PlanState | None:
        """Find a plan containing the given step ID."""
        for plan in self._plans.values():
            if any(s.id == step_id for s in plan.steps):
                return plan
        return None

    def cleanup_run(self, run_plan_ids: list[str] | None = None) -> None:
        """Remove all plans, or specific ones if IDs provided."""
        if run_plan_ids is None:
            self._plans.clear()
        else:
            for pid in run_plan_ids:
                self._plans.pop(pid, None)


# Module-level singleton — run-scoped, cleared between runs
plan_registry = PlanRegistry()
