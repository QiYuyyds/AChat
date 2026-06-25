"""report_task_result parsing + normalisation + completion gating.

Port of src/server/task-result-report.ts: validate args and emit a normalised,
camelCase report dict (parse half), plus ``evaluate_task_result_report`` which
gates a child task's completion against its contract and the objective tool
evidence recorded during the run (evaluate half, consumed by AgentRunner in
阶段 5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.schemas.dispatch import DispatchPlanItem
from app.services.dispatch_plan import (
    CODE_TASK_RUNNABLE_REQUIRED_EVIDENCE,
    is_code_implementation_task,
)
from app.utils.dispatch_run_evidence import RunCommandEvidence, RunToolEvidence

REPORT_TASK_RESULT_TOOL_NAME = "report_task_result"


class _AcceptanceResult(BaseModel):
    criterion: str = Field(min_length=1)
    passed: bool
    evidence: str = Field(min_length=1)


class _FileChanged(BaseModel):
    path: str = Field(min_length=1)
    action: str | None = None  # created | modified | deleted | verified


class _CommandRun(BaseModel):
    command: str = Field(min_length=1)
    exit_code: int | None = Field(alias="exitCode")
    cwd: str | None = None
    timed_out: bool | None = Field(default=None, alias="timedOut")
    summary: str | None = None
    model_config = ConfigDict(populate_by_name=True)


class _Test(BaseModel):
    command: str = Field(min_length=1)
    passed: bool
    summary: str | None = None


class ReportTaskResultArgs(BaseModel):
    status: str  # complete | failed | blocked
    summary: str = Field(min_length=1)
    acceptance_results: list[_AcceptanceResult] | None = Field(
        default=None, alias="acceptanceResults"
    )
    files_changed: list[_FileChanged] | None = Field(default=None, alias="filesChanged")
    commands_run: list[_CommandRun] | None = Field(default=None, alias="commandsRun")
    tests: list[_Test] | None = None
    blockers: list[str] | None = None
    model_config = ConfigDict(populate_by_name=True)


def _action_valid(action: str | None) -> bool:
    return action in ("created", "modified", "deleted", "verified")


def normalize_task_result_report(data: ReportTaskResultArgs) -> dict[str, Any]:
    report: dict[str, Any] = {"status": data.status, "summary": data.summary.strip()}

    if data.acceptance_results:
        acceptance = [
            {
                "criterion": r.criterion.strip(),
                "passed": r.passed,
                "evidence": r.evidence.strip(),
            }
            for r in data.acceptance_results
            if r.criterion.strip() and r.evidence.strip()
        ]
        if acceptance:
            report["acceptanceResults"] = acceptance

    if data.files_changed:
        files = []
        for f in data.files_changed:
            path = f.path.strip()
            if not path:
                continue
            entry: dict[str, Any] = {"path": path}
            if _action_valid(f.action):
                entry["action"] = f.action
            files.append(entry)
        if files:
            report["filesChanged"] = files

    if data.commands_run:
        commands = []
        for c in data.commands_run:
            command = c.command.strip()
            if not command:
                continue
            entry = {"command": command, "exitCode": c.exit_code}
            if c.cwd and c.cwd.strip():
                entry["cwd"] = c.cwd.strip()
            if c.timed_out is not None:
                entry["timedOut"] = c.timed_out
            if c.summary and c.summary.strip():
                entry["summary"] = c.summary.strip()
            commands.append(entry)
        if commands:
            report["commandsRun"] = commands

    if data.tests:
        tests = []
        for t in data.tests:
            command = t.command.strip()
            if not command:
                continue
            entry = {"command": command, "passed": t.passed}
            if t.summary and t.summary.strip():
                entry["summary"] = t.summary.strip()
            tests.append(entry)
        if tests:
            report["tests"] = tests

    if data.blockers:
        blockers = [b.strip() for b in data.blockers if b.strip()]
        if blockers:
            report["blockers"] = blockers

    return report


def parse_and_normalize(value: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Validate raw tool args → (normalized report, None) or (None, error)."""
    try:
        parsed = ReportTaskResultArgs.model_validate(value)
    except ValidationError as err:
        return None, f"Invalid task result report: {err}"
    if parsed.status not in ("complete", "failed", "blocked"):
        return None, f"Invalid task result report: bad status {parsed.status!r}"
    return normalize_task_result_report(parsed), None


# ─── Completion gating (port of evaluateTaskResultReport) ─────────────────────


@dataclass
class TaskResultReportEvaluation:
    ok: bool
    error: str | None = None


# build/compile/test/typecheck/lint command shapes that count as verification
_VERIFICATION_COMMAND_PATTERNS = [
    re.compile(
        r"\b(?:pnpm|npm|yarn|bun)(?:\.cmd)?\b(?=.*\b(?:run\s+)?"
        r"(?:build|test|lint|typecheck|check|compile)(?:\b|:))",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:tsc|tsc\.cmd)\b", re.IGNORECASE),
    re.compile(r"\bnext(?:\.cmd)?\s+build\b", re.IGNORECASE),
    re.compile(r"\bvite(?:\.cmd)?\s+build\b", re.IGNORECASE),
    re.compile(r"\bmvn(?:\.cmd)?\b(?=.*\b(?:compile|test|package|verify)\b)", re.IGNORECASE),
    re.compile(
        r"\b(?:gradle|gradlew|gradlew\.bat|\./gradlew)\b(?=.*\b(?:build|test|check)\b)",
        re.IGNORECASE,
    ),
    re.compile(r"\bgo\s+(?:test|build)\b", re.IGNORECASE),
    re.compile(r"\bcargo\s+(?:test|build|check)\b", re.IGNORECASE),
    re.compile(r"\b(?:pytest|py\.test)\b", re.IGNORECASE),
    re.compile(r"\bpython(?:3)?(?:\.exe)?\s+-m\s+pytest\b", re.IGNORECASE),
    re.compile(r"\bruff\s+check\b", re.IGNORECASE),
    re.compile(r"\bmypy\b", re.IGNORECASE),
    re.compile(r"\bdotnet\s+(?:build|test)\b", re.IGNORECASE),
]

# `install`/`add` without a build verb is preparation, not verification
_PREPARE_COMMAND_RE = re.compile(
    r"^\s*(?:pnpm|npm|yarn|bun)(?:\.cmd)?\s+(?:install|i|ci|add)\b", re.IGNORECASE
)
_BUILD_VERB_RE = re.compile(r"\b(?:build|test|lint|typecheck|check|compile)(?:\b|:)", re.IGNORECASE)


def _normalize_path(value: str) -> str:
    # strip a single leading "./" prefix only (TS: .replace(/^\.\/+/, '')); NOT a
    # char-set strip — lstrip("./") would also eat dotfiles like ".gitignore".
    cleaned = re.sub(r"^\./+", "", value.strip().replace("\\", "/"))
    return cleaned.rstrip("/").lower()


def _normalize_command(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def _paths_match(expected: str, actual: str) -> bool:
    e = _normalize_path(expected)
    a = _normalize_path(actual)
    return a == e or a.endswith(f"/{e}") or a.startswith(f"{e}/")


def _commands_match(expected: str, actual: str) -> bool:
    e = _normalize_command(expected)
    a = _normalize_command(actual)
    return a == e or e in a


def _is_prepare_command(command: str) -> bool:
    return bool(_PREPARE_COMMAND_RE.search(command)) and not bool(_BUILD_VERB_RE.search(command))


def is_verification_command(command: str) -> bool:
    normalized = _normalize_command(command)
    return not _is_prepare_command(normalized) and any(
        pattern.search(normalized) for pattern in _VERIFICATION_COMMAND_PATTERNS
    )


def _is_successful_verification_command(command: RunCommandEvidence) -> bool:
    return (
        not command.prepare
        and not command.is_error
        and not command.timed_out
        and command.exit_code == 0
        and is_verification_command(command.command)
    )


def has_successful_verification_command_evidence(evidence: RunToolEvidence) -> bool:
    return any(_is_successful_verification_command(c) for c in evidence.commands)


def _is_failed_command(command: RunCommandEvidence) -> bool:
    return command.is_error or command.timed_out or command.exit_code != 0


def _has_later_successful_command(
    failed: RunCommandEvidence, failed_index: int, commands: list[RunCommandEvidence]
) -> bool:
    return any(
        _commands_match(failed.command, c.command)
        and not c.is_error
        and not c.timed_out
        and c.exit_code == 0
        for c in commands[failed_index + 1 :]
    )


def _has_path_evidence(target_path: str, report: dict[str, Any], evidence: RunToolEvidence) -> bool:
    candidates: list[str] = [f.get("path", "") for f in report.get("filesChanged") or []]
    for file in evidence.file_writes:
        candidates.extend([file.path, file.absolute_path])
    return any(candidate and _paths_match(target_path, candidate) for candidate in candidates)


def _has_successful_command_evidence(
    required_command: str, report: dict[str, Any], evidence: RunToolEvidence
) -> bool:
    reported = any(
        _commands_match(required_command, c.get("command", "")) and c.get("exitCode") == 0
        for c in report.get("commandsRun") or []
    )
    tested = any(
        _commands_match(required_command, t.get("command", "")) and t.get("passed")
        for t in report.get("tests") or []
    )
    recorded = any(
        _commands_match(required_command, c.command)
        and not c.is_error
        and not c.timed_out
        and c.exit_code == 0
        for c in evidence.commands
    )
    return bool(reported or tested or recorded)


def _evidence_mentions(required: str, report: dict[str, Any], evidence: RunToolEvidence) -> bool:
    parts: list[str] = [report.get("summary", "")]
    for result in report.get("acceptanceResults") or []:
        parts.extend([result.get("criterion", ""), result.get("evidence", "")])
    for file in report.get("filesChanged") or []:
        parts.extend([file.get("path", ""), file.get("action") or ""])
    for command in report.get("commandsRun") or []:
        parts.extend([command.get("command", ""), command.get("summary") or ""])
    for test in report.get("tests") or []:
        parts.extend([test.get("command", ""), test.get("summary") or ""])
    for file in evidence.file_writes:
        parts.extend([file.path, file.absolute_path, str(file.bytes or "")])
    for command in evidence.commands:
        parts.extend(
            [
                command.command,
                command.cwd,
                str(command.exit_code if command.exit_code is not None else ""),
                "timedOut" if command.timed_out else "",
                "isError" if command.is_error else "",
                command.error or "",
                "exitCode=0"
                if command.exit_code == 0 and not command.timed_out and not command.is_error
                else "",
            ]
        )
    return required.lower() in "\n".join(parts).lower()


def _required_evidence_satisfied(
    required: str, report: dict[str, Any], evidence: RunToolEvidence
) -> bool:
    if required.strip() == CODE_TASK_RUNNABLE_REQUIRED_EVIDENCE:
        return has_successful_verification_command_evidence(evidence)
    return _evidence_mentions(required, report, evidence)


def _format_reported_non_completion(task_id: str, report: dict[str, Any]) -> str:
    blockers = report.get("blockers") or []
    suffix = f" Blockers: {'; '.join(blockers)}" if blockers else ""
    return f'Task "{task_id}" reported {report.get("status")}: {report.get("summary")}{suffix}'


def evaluate_task_result_report(
    task: DispatchPlanItem,
    report: dict[str, Any] | None,
    evidence: RunToolEvidence | None = None,
) -> TaskResultReportEvaluation:
    """Gate a child task's completion against its contract + objective evidence."""
    if evidence is None:
        evidence = RunToolEvidence()

    if not report:
        return TaskResultReportEvaluation(
            ok=False, error=f'Task "{task.id}" completed without report_task_result'
        )

    if report.get("status") != "complete":
        return TaskResultReportEvaluation(
            ok=False, error=_format_reported_non_completion(task.id, report)
        )

    failed_commands = [
        command
        for index, command in enumerate(evidence.commands)
        if not command.prepare
        and _is_failed_command(command)
        and not _has_later_successful_command(command, index, evidence.commands)
    ]
    if failed_commands:
        details = "; ".join(
            f"{c.command} ("
            + (
                (c.error or "tool error")
                if c.is_error
                else "timed out"
                if c.timed_out
                else f"exit {c.exit_code}"
            )
            + ")"
            for c in failed_commands
        )
        return TaskResultReportEvaluation(
            ok=False, error=f'Task "{task.id}" has failed command evidence: {details}'
        )

    failed_acceptance = [r for r in report.get("acceptanceResults") or [] if not r.get("passed")]
    if failed_acceptance:
        details = "; ".join(
            f"{r.get('criterion')} ({r.get('evidence')})" for r in failed_acceptance
        )
        return TaskResultReportEvaluation(
            ok=False, error=f'Task "{task.id}" did not satisfy acceptance criteria: {details}'
        )

    criteria = task.acceptance_criteria or []
    if criteria:
        reported = {r.get("criterion", "").strip() for r in report.get("acceptanceResults") or []}
        missing = [c for c in criteria if c.strip() not in reported]
        if missing:
            return TaskResultReportEvaluation(
                ok=False,
                error=(
                    f'Task "{task.id}" report is missing acceptance criteria '
                    f"result(s): {'; '.join(missing)}"
                ),
            )

    missing_paths = [
        p for p in (task.target_paths or []) if not _has_path_evidence(p, report, evidence)
    ]
    if missing_paths:
        return TaskResultReportEvaluation(
            ok=False,
            error=f'Task "{task.id}" report is missing target path evidence: {"; ".join(missing_paths)}',
        )

    missing_commands = [
        required
        for required in (task.required_commands or [])
        if not _has_successful_command_evidence(required.command, report, evidence)
    ]
    if missing_commands:
        details = "; ".join(required.command for required in missing_commands)
        return TaskResultReportEvaluation(
            ok=False,
            error=f'Task "{task.id}" report is missing successful command evidence: {details}',
        )

    if is_code_implementation_task(task) and not has_successful_verification_command_evidence(
        evidence
    ):
        return TaskResultReportEvaluation(
            ok=False,
            error=(
                f'Task "{task.id}" is missing successful runnable verification command '
                "evidence: build/compile/test/typecheck/lint command exitCode=0"
            ),
        )

    missing_evidence = [
        required
        for required in (task.required_evidence or [])
        if not _required_evidence_satisfied(required, report, evidence)
    ]
    if missing_evidence:
        return TaskResultReportEvaluation(
            ok=False,
            error=f'Task "{task.id}" report is missing required evidence: {"; ".join(missing_evidence)}',
        )

    return TaskResultReportEvaluation(ok=True)
