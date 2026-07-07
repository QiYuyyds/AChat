"""Verify stage — deterministic rule-based validation of task results.

Called after DAG execution and before aggregation. Does NOT call LLM;
uses rule-based checks on artifacts, evidence, and expected outputs.

See openspec/changes/p2-checkpoint-validation-routing/specs/orchestrator/spec.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.schemas.dispatch import DispatchPlanItem

logger = logging.getLogger(__name__)


@dataclass
class VerifyResult:
    """Outcome of verifying a single task result."""

    passed: bool
    reason: str | None = None


def verify_task_result(
    task: DispatchPlanItem,
    result: Any,  # DispatchTaskResult (avoid circular import)
) -> VerifyResult:
    """Dispatch to the appropriate verifier based on task_kind."""
    kind = task.task_kind or ""
    if kind == "code":
        return _verify_code_task(task, result)
    if kind == "doc":
        return _verify_document_task(task, result)
    if kind == "review":
        return _verify_review_task(task, result)
    return _verify_default_task(task, result)


def _verify_code_task(
    task: DispatchPlanItem,
    result: Any,
) -> VerifyResult:
    """Check project artifact exists + required_evidence commands succeeded."""
    has_project = _has_project_artifact(task, result)
    if not has_project:
        return VerifyResult(
            passed=False,
            reason=f'Task "{task.id}" (code) verification failed: no project artifact found',
        )

    # Check required_evidence: look for successful command records in task_report
    required_evidence = task.required_evidence or []
    if required_evidence:
        commands_run = _get_successful_commands(result)
        if not commands_run and required_evidence:
            return VerifyResult(
                passed=False,
                reason=(
                    f'Task "{task.id}" (code) verification failed: '
                    f"required evidence commands not satisfied: {', '.join(required_evidence)}"
                ),
            )

    return VerifyResult(passed=True)


def _verify_document_task(
    task: DispatchPlanItem,
    result: Any,
) -> VerifyResult:
    """Check artifact content covers expected_outputs declared items."""
    expected = [o for o in (task.expected_outputs or []) if o.required is not False]
    if not expected:
        return VerifyResult(passed=True)

    output_artifacts = getattr(result, "output_artifacts", {}) or {}
    missing_outputs = [o for o in expected if o.id not in output_artifacts]
    if missing_outputs:
        missing_ids = ", ".join(o.id for o in missing_outputs)
        return VerifyResult(
            passed=False,
            reason=(
                f'Task "{task.id}" (doc) verification failed: '
                f"missing expected outputs: {missing_ids}"
            ),
        )

    return VerifyResult(passed=True)


def _verify_review_task(
    task: DispatchPlanItem,
    result: Any,
) -> VerifyResult:
    """Check review conclusion references dependsOn upstream artifacts."""
    depends_on = task.depends_on or []
    if not depends_on:
        return VerifyResult(passed=True)

    task_report = getattr(result, "task_report", None) or {}
    summary = str(task_report.get("summary", ""))

    referenced = any(dep in summary for dep in depends_on)
    if not referenced:
        return VerifyResult(
            passed=False,
            reason=(
                f'Task "{task.id}" (review) verification failed: '
                f"review conclusion does not reference upstream task(s): {', '.join(depends_on)}"
            ),
        )

    return VerifyResult(passed=True)


def _verify_default_task(
    task: DispatchPlanItem,
    result: Any,
) -> VerifyResult:
    """Unknown task kind — pass verification (no validation)."""
    return VerifyResult(passed=True)


def _has_project_artifact(task: DispatchPlanItem, result: Any) -> bool:
    """Check if the result contains a project artifact."""
    output_artifacts = getattr(result, "output_artifacts", {}) or {}
    artifact_ids = getattr(result, "artifact_ids", []) or []

    has_project_output = any(
        o.id in output_artifacts
        for o in (task.expected_outputs or [])
        if o.type == "project"
    )
    if has_project_output:
        return True

    if artifact_ids:
        return True

    return False


def _get_successful_commands(result: Any) -> list[str]:
    """Extract successful command names from the task_report."""
    task_report = getattr(result, "task_report", None) or {}
    commands_run = task_report.get("commandsRun") or []
    return [
        str(cmd.get("command", ""))
        for cmd in commands_run
        if cmd.get("exitCode") == 0 and not cmd.get("timedOut")
    ]
