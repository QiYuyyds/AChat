"""Tests for evaluate_task_result_report (port of evaluateTaskResultReport)."""

from __future__ import annotations

from app.schemas.dispatch import DispatchPlanItem
from app.services.task_result_report import (
    evaluate_task_result_report,
    is_verification_command,
)
from app.utils.dispatch_run_evidence import (
    RunCommandEvidence,
    RunFileEvidence,
    RunToolEvidence,
)


def _task(**kwargs) -> DispatchPlanItem:
    base = {"id": "t1", "agentId": "a1", "task": "do something", "taskKind": "review"}
    base.update(kwargs)
    return DispatchPlanItem.model_validate(base)


def _ok_command(command: str) -> RunCommandEvidence:
    return RunCommandEvidence(
        command=command, cwd="/ws", exit_code=0, timed_out=False, is_error=False
    )


def test_missing_report_fails():
    result = evaluate_task_result_report(_task(), None)
    assert not result.ok
    assert "without report_task_result" in result.error


def test_non_complete_status_fails():
    report = {"status": "failed", "summary": "broke", "blockers": ["db down"]}
    result = evaluate_task_result_report(_task(), report)
    assert not result.ok
    assert "reported failed" in result.error
    assert "db down" in result.error


def test_failed_command_evidence_fails():
    report = {"status": "complete", "summary": "ok"}
    evidence = RunToolEvidence(
        commands=[
            RunCommandEvidence(
                command="pytest", cwd="/ws", exit_code=1, timed_out=False, is_error=False
            )
        ]
    )
    result = evaluate_task_result_report(_task(), report, evidence)
    assert not result.ok
    assert "failed command evidence" in result.error


def test_failed_command_excused_by_later_success():
    report = {"status": "complete", "summary": "ok"}
    evidence = RunToolEvidence(
        commands=[
            RunCommandEvidence(
                command="pytest", cwd="/ws", exit_code=1, timed_out=False, is_error=False
            ),
            _ok_command("pytest"),
        ]
    )
    result = evaluate_task_result_report(_task(), report, evidence)
    assert result.ok


def test_failed_acceptance_result_fails():
    report = {
        "status": "complete",
        "summary": "ok",
        "acceptanceResults": [
            {"criterion": "tests pass", "passed": False, "evidence": "they did not"}
        ],
    }
    result = evaluate_task_result_report(_task(), report)
    assert not result.ok
    assert "did not satisfy acceptance criteria" in result.error


def test_missing_acceptance_criteria_result_fails():
    task = _task(acceptanceCriteria=["build passes", "lint clean"])
    report = {
        "status": "complete",
        "summary": "ok",
        "acceptanceResults": [
            {"criterion": "build passes", "passed": True, "evidence": "exit 0"}
        ],
    }
    result = evaluate_task_result_report(task, report)
    assert not result.ok
    assert "missing acceptance criteria" in result.error
    assert "lint clean" in result.error


def test_missing_target_path_evidence_fails():
    task = _task(targetPaths=["src/foo.py"])
    report = {"status": "complete", "summary": "ok"}
    result = evaluate_task_result_report(task, report)
    assert not result.ok
    assert "missing target path evidence" in result.error


def test_target_path_satisfied_by_file_write():
    task = _task(targetPaths=["src/foo.py"])
    report = {"status": "complete", "summary": "ok"}
    evidence = RunToolEvidence(
        file_writes=[RunFileEvidence(path="src/foo.py", absolute_path="/ws/src/foo.py")]
    )
    result = evaluate_task_result_report(task, report, evidence)
    assert result.ok


def test_missing_required_command_evidence_fails():
    task = _task(requiredCommands=[{"command": "pytest"}])
    report = {"status": "complete", "summary": "ok"}
    result = evaluate_task_result_report(task, report)
    assert not result.ok
    assert "missing successful command evidence" in result.error


def test_required_command_satisfied_by_report():
    task = _task(requiredCommands=[{"command": "pytest"}])
    report = {
        "status": "complete",
        "summary": "ok",
        "commandsRun": [{"command": "pytest -q", "exitCode": 0}],
    }
    result = evaluate_task_result_report(task, report)
    assert result.ok


def test_code_task_verification_gate_fails_without_verification():
    task = _task(taskKind="code")
    report = {"status": "complete", "summary": "ok"}
    result = evaluate_task_result_report(task, report)
    assert not result.ok
    assert "runnable verification command evidence" in result.error


def test_code_task_verification_gate_passes_with_evidence():
    task = _task(taskKind="code")
    report = {"status": "complete", "summary": "ok"}
    evidence = RunToolEvidence(commands=[_ok_command("pnpm run build")])
    result = evaluate_task_result_report(task, report, evidence)
    assert result.ok


def test_install_only_does_not_satisfy_verification():
    task = _task(taskKind="code")
    report = {"status": "complete", "summary": "ok"}
    evidence = RunToolEvidence(commands=[_ok_command("pnpm install")])
    result = evaluate_task_result_report(task, report, evidence)
    assert not result.ok


def test_required_evidence_missing_fails():
    task = _task(requiredEvidence=["screenshot attached"])
    report = {"status": "complete", "summary": "ok"}
    result = evaluate_task_result_report(task, report)
    assert not result.ok
    assert "missing required evidence" in result.error


def test_required_evidence_satisfied_by_mention():
    task = _task(requiredEvidence=["screenshot attached"])
    report = {"status": "complete", "summary": "screenshot attached to PR"}
    result = evaluate_task_result_report(task, report)
    assert result.ok


def test_full_success():
    task = _task(
        taskKind="code",
        acceptanceCriteria=["builds"],
        targetPaths=["src/foo.py"],
        requiredCommands=[{"command": "pytest"}],
    )
    report = {
        "status": "complete",
        "summary": "done",
        "acceptanceResults": [{"criterion": "builds", "passed": True, "evidence": "exit 0"}],
        "filesChanged": [{"path": "src/foo.py", "action": "modified"}],
        "commandsRun": [{"command": "pytest", "exitCode": 0}],
    }
    evidence = RunToolEvidence(commands=[_ok_command("pytest")])
    result = evaluate_task_result_report(task, report, evidence)
    assert result.ok
    assert result.error is None


def test_is_verification_command_detection():
    assert is_verification_command("pnpm run test")
    assert is_verification_command("tsc")
    assert is_verification_command("cargo build")
    assert is_verification_command("python -m pytest")
    assert not is_verification_command("pnpm install")
    assert not is_verification_command("echo hello")
