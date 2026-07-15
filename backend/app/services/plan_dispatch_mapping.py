"""In-memory mapping between dispatch tasks and execution plan steps.

When ``dispatch_plan`` is called with ``planStepId`` on a task item, the
handler registers a mapping here so that ``consume_stream`` can update the
corresponding plan step when the dispatch task completes.

Lifecycle: populated by ``dispatch_plan`` handler, cleaned up alongside
``plan_registry`` on run end.
"""

from __future__ import annotations

from typing import Literal

# Status a dispatch task can be in
TaskStatus = Literal["running", "complete", "failed", "aborted", "skipped", "merge_conflict"]


class PlanDispatchMapping:
    """Lightweight in-memory registry mapping dispatch tasks to plan steps."""

    def __init__(self) -> None:
        # (plan_id, step_id) -> list of dispatch task IDs
        self._forward: dict[tuple[str, str], list[str]] = {}
        # dispatch task ID -> (plan_id, step_id)
        self._reverse: dict[str, tuple[str, str]] = {}
        # dispatch task ID -> current status
        self._task_status: dict[str, TaskStatus] = {}

    def register(
        self, plan_id: str, step_id: str, dispatch_task_id: str
    ) -> None:
        """Register a mapping from a dispatch task to a plan step."""
        key = (plan_id, step_id)
        if key not in self._forward:
            self._forward[key] = []
        self._forward[key].append(dispatch_task_id)
        self._reverse[dispatch_task_id] = key
        self._task_status[dispatch_task_id] = "running"

    def update_task_status(
        self, dispatch_task_id: str, status: TaskStatus
    ) -> None:
        """Update the status of a dispatch task."""
        if dispatch_task_id in self._task_status:
            self._task_status[dispatch_task_id] = status

    def lookup_by_task(
        self, dispatch_task_id: str
    ) -> tuple[str, str] | None:
        """Look up the (plan_id, step_id) for a dispatch task ID."""
        return self._reverse.get(dispatch_task_id)

    def lookup_tasks(
        self, plan_id: str, step_id: str
    ) -> list[str]:
        """Look up all dispatch task IDs for a plan step."""
        return list(self._forward.get((plan_id, step_id), []))

    def get_task_status(self, dispatch_task_id: str) -> TaskStatus | None:
        """Get the current status of a dispatch task."""
        return self._task_status.get(dispatch_task_id)

    def aggregate_step_status(
        self, plan_id: str, step_id: str
    ) -> str | None:
        """Determine the combined status of all dispatch tasks for a plan step.

        Returns:
            'done' if all tasks complete (or complete + skipped),
            'failed' if any task failed,
            'skipped' if all tasks skipped,
            'in_progress' if some tasks still running,
            None if no tasks mapped.
        """
        task_ids = self._forward.get((plan_id, step_id))
        if not task_ids:
            return None

        statuses = [self._task_status.get(tid, "running") for tid in task_ids]

        # Any failed/aborted/merge_conflict → failed
        if any(s in ("failed", "aborted", "merge_conflict") for s in statuses):
            return "failed"
        # All complete (or complete + skipped) → done
        non_running = [s for s in statuses if s != "running"]
        if len(non_running) == len(statuses):
            if all(s in ("complete", "skipped") for s in statuses):
                return "done"
            if all(s == "skipped" for s in statuses):
                return "skipped"
            # Mixed complete + skipped → done
            return "done"
        # Some still running → in_progress
        return "in_progress"

    def cleanup_run(self, plan_ids: list[str] | None = None) -> None:
        """Remove mappings for specific plan IDs, or all mappings."""
        if plan_ids is None:
            self._forward.clear()
            self._reverse.clear()
            self._task_status.clear()
            return

        for pid in plan_ids:
            keys_to_remove = [
                k for k in self._forward if k[0] == pid
            ]
            for key in keys_to_remove:
                task_ids = self._forward.pop(key, [])
                for tid in task_ids:
                    self._reverse.pop(tid, None)
                    self._task_status.pop(tid, None)


# Module-level singleton — run-scoped, cleared alongside plan_registry
plan_dispatch_mapping = PlanDispatchMapping()
