"""report_task_result parsing + normalisation + completion gating.

Port of src/server/task-result-report.ts: validate args and emit a normalised,
camelCase report dict (parse half), plus ``evaluate_task_result_report`` which
gates a child task's completion against its contract and the objective tool
evidence recorded during the run (evaluate half, consumed by AgentRunner in
阶段 5).
"""

from __future__ import annotations

import json
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
    # MCP tools return results as JSON strings; SDK tools pass dicts directly.
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as e:
            return None, f"Invalid JSON in task result: {e}"
    try:
        parsed = ReportTaskResultArgs.model_validate(value)
    except ValidationError as err:
        return None, f"Invalid task result report: {err}"
    if parsed.status not in ("complete", "failed", "blocked"):
        return None, f"Invalid task result report: bad status {parsed.status!r}"
    return normalize_task_result_report(parsed), None


# ─── Advisory evidence collector (replaces fail-fast gating) ──────────────────


@dataclass
class TaskResultReportEvaluation:
    """Deprecated: kept for backward compatibility. Use TaskEvidenceSummary."""
    ok: bool
    error: str | None = None


@dataclass
class TaskEvidenceSummary:
    """Advisory evidence collected from a child task's report + tool evidence.

    Hard rules (② report.status != "complete" and ④ acceptanceResults
    passed=false) are surfaced via *report_status* — the caller checks
    ``has_report`` and ``report_status`` to decide hard fails.
    Soft rules (③⑤⑥⑦⑧⑨) are collected into *advisory_issues* for the
    Orchestrator LLM to judge semantically.
    """
    advisory_issues: list[str]
    has_report: bool
    report_status: str | None
    evidence: RunToolEvidence


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


# Command families where the first two tokens form the "prefix" (e.g.
# `python -c`, `pnpm run`).  For all other commands, only the first token is
# the prefix (e.g. `pytest`, `tsc`).
_TWO_TOKEN_PREFIX_COMMANDS = frozenset({
    "python", "python3", "pnpm", "npm", "yarn", "bun", "node",
})


def _command_prefix(command: str) -> str:
    """Extract the prefix of a command for prefix-based failed-command recovery.

    For ``python``/``python3``/``pnpm``/``npm``/``yarn``/``bun``/``node``, the
    first **two** tokens form the prefix (e.g. ``python -c``, ``pnpm run``).
    For all other commands, only the first token is the prefix.
    """
    normalized = _normalize_command(command)
    parts = normalized.split()
    if len(parts) >= 2 and parts[0] in _TWO_TOKEN_PREFIX_COMMANDS:
        return " ".join(parts[:2])
    return parts[0] if parts else normalized


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
    # A command counts as a real failure only when it produced a concrete
    # failure signal: a non-zero exit code, a timeout, or a tool error that
    # carries an actual message. An "empty" tool error (is_error=True but no
    # error text AND no exit code) is environment/harness noise — e.g. a
    # subprocess spawn that failed for infra reasons — not evidence that the
    # task itself failed. Treating those as failures made the orchestrator
    # reject perfectly good work on every attempt.
    if command.timed_out:
        return True
    if command.exit_code is not None and command.exit_code != 0:
        return True
    if command.is_error and (command.error or "").strip():
        return True
    return False


def _has_later_successful_command(
    failed: RunCommandEvidence, failed_index: int, commands: list[RunCommandEvidence]
) -> bool:
    """Check if a failed command is recovered by a later successful command.

    Recovery requires either:
    - Exact command-string match (original behavior), OR
    - Same command prefix (first one or two tokens) with a successful result.
    """
    failed_prefix = _command_prefix(failed.command)
    return any(
        (
            _commands_match(failed.command, c.command)
            or _command_prefix(c.command) == failed_prefix
        )
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
    required: str, report: dict[str, Any], evidence: RunToolEvidence,
    has_build_toolchain: bool = True,
) -> bool:
    if required.strip() == CODE_TASK_RUNNABLE_REQUIRED_EVIDENCE:
        if not has_build_toolchain:
            return True
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
    has_build_toolchain: bool = True,
) -> TaskEvidenceSummary:
    """Collect advisory evidence from a child task's report + tool evidence.

    Returns a :class:`TaskEvidenceSummary` with:
    - *has_report* / *report_status*: hard-rule signals for the caller.
      ② ``report.status != "complete"`` and ④ ``acceptanceResults`` with
      ``passed=false`` are surfaced here — the caller checks these for
      hard fails.
    - *advisory_issues*: soft-rule findings (③⑤⑥⑦⑧⑨) collected for the
      Orchestrator LLM to judge semantically. These do NOT block.
    """
    if evidence is None:
        evidence = RunToolEvidence()

    # ① No report — hard fail signal.
    if not report:
        return TaskEvidenceSummary(
            advisory_issues=[],
            has_report=False,
            report_status=None,
            evidence=evidence,
        )

    # ② report.status != "complete" — hard fail signal (caller checks report_status).
    report_status = report.get("status")
    if report_status != "complete":
        return TaskEvidenceSummary(
            advisory_issues=[],
            has_report=True,
            report_status=report_status,
            evidence=evidence,
        )

    # ④ acceptanceResults with passed=false — hard fail signal.
    # Override report_status to "failed" so the caller's check catches it.
    failed_acceptance = [r for r in report.get("acceptanceResults") or [] if not r.get("passed")]
    if failed_acceptance:
        return TaskEvidenceSummary(
            advisory_issues=[],
            has_report=True,
            report_status="failed",
            evidence=evidence,
        )

    # ─── Advisory checks (③⑤⑥⑦⑧⑨) — collected, NOT blocking ─────────
    advisory_issues: list[str] = []

    # ③ Failed bash commands (not recovered by a later same-prefix success).
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
        advisory_issues.append(f"Failed command evidence: {details}")

    # ⑤ Criteria coverage — declared criteria not exact-matched in report.
    criteria = task.acceptance_criteria or []
    if criteria:
        reported = {r.get("criterion", "").strip() for r in report.get("acceptanceResults") or []}
        missing = [c for c in criteria if c.strip() not in reported]
        if missing:
            advisory_issues.append(
                f"Acceptance criteria not exact-matched in report: {'; '.join(missing)}"
            )

    # ⑥ Target paths — declared paths not found in file-write evidence.
    missing_paths = [
        p for p in (task.target_paths or []) if not _has_path_evidence(p, report, evidence)
    ]
    if missing_paths:
        advisory_issues.append(
            f"Target path evidence not found: {'; '.join(missing_paths)}"
        )

    # ⑦ Required commands — declared commands without successful evidence.
    missing_commands = [
        required
        for required in (task.required_commands or [])
        if not _has_successful_command_evidence(required.command, report, evidence)
    ]
    if missing_commands:
        details = "; ".join(required.command for required in missing_commands)
        advisory_issues.append(
            f"Required command evidence not found: {details}"
        )

    # ⑧ Verification command gate — code task with project output but no
    # whitelisted build/test/lint command succeeded. Advisory only.
    produces_project = any(
        (o.type == "project") for o in (task.expected_outputs or [])
    )
    declared_required_commands = bool(task.required_commands)
    if (
        has_build_toolchain
        and produces_project
        and not declared_required_commands
        and is_code_implementation_task(task)
        and not has_successful_verification_command_evidence(evidence)
    ):
        advisory_issues.append(
            "No whitelisted verification command (build/compile/test/typecheck/lint) "
            "found with exitCode=0"
        )

    # ⑨ Required evidence — declared evidence strings not found in report.
    missing_evidence = [
        required
        for required in (task.required_evidence or [])
        if not _required_evidence_satisfied(
            required, report, evidence, has_build_toolchain
        )
    ]
    if missing_evidence:
        advisory_issues.append(
            f"Required evidence not found: {'; '.join(missing_evidence)}"
        )

    return TaskEvidenceSummary(
        advisory_issues=advisory_issues,
        has_report=True,
        report_status="complete",
        evidence=evidence,
    )
