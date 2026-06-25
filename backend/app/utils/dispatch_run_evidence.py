"""Per-run tool evidence (file writes + commands).

Port of src/server/dispatch-run-evidence.ts. The bash / fs_write tools record
what they did here, keyed by run id; the Orchestrator's task-result evaluation
(阶段 5) reads it back to gate completion. In-memory, single-process.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RunFileEvidence:
    path: str
    absolute_path: str
    bytes: int | None = None
    applied: str | None = None  # 'auto' | 'review'


@dataclass
class RunCommandEvidence:
    command: str
    cwd: str
    exit_code: int | None
    timed_out: bool
    is_error: bool
    prepare: bool = False
    error: str | None = None


@dataclass
class RunToolEvidence:
    file_writes: list[RunFileEvidence] = field(default_factory=list)
    commands: list[RunCommandEvidence] = field(default_factory=list)


_evidence_by_run: dict[str, RunToolEvidence] = {}


def _ensure_evidence(run_id: str) -> RunToolEvidence:
    evidence = _evidence_by_run.get(run_id)
    if evidence is None:
        evidence = RunToolEvidence()
        _evidence_by_run[run_id] = evidence
    return evidence


def record_run_file_write(run_id: str, evidence: RunFileEvidence) -> None:
    _ensure_evidence(run_id).file_writes.append(evidence)


def record_run_command(run_id: str, evidence: RunCommandEvidence) -> None:
    _ensure_evidence(run_id).commands.append(evidence)


def get_run_tool_evidence(run_id: str) -> RunToolEvidence:
    return _evidence_by_run.get(run_id, RunToolEvidence())


def clear_run_tool_evidence(run_id: str) -> None:
    _evidence_by_run.pop(run_id, None)
