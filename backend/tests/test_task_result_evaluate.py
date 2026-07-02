"""Tests for evaluate_task_result_report (advisory evidence collector)."""

from __future__ import annotations

from app.schemas.dispatch import DispatchPlanItem
from app.services.task_result_report import (
    TaskEvidenceSummary,
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


# ─── hard-rule fails (② ④) ──────────────────────
def test_missing_report_signals_has_report_false():
    summary = evaluate_task_result_report(_task(), None)
    assert not summary.has_report
    assert summary.report_status is None
    assert summary.advisory_issues == []


def test_non_complete_status_signals_report_status():
    report = {"status": "failed", "summary": "broke", "blockers": ["db down"]}
    summary = evaluate_task_result_report(_task(), report)
    assert summary.has_report
    assert summary.report_status == "failed"
    assert summary.advisory_issues == []


def test_failed_acceptance_result_signals_report_status_failed():
    report = {
        "status": "complete",
        "summary": "ok",
        "acceptanceResults": [
            {"criterion": "tests pass", "passed": False, "evidence": "they did not"}
        ],
    }
    summary = evaluate_task_result_report(_task(), report)
    assert summary.has_report
    assert summary.report_status == "failed"
    assert summary.advisory_issues == []


# ─── advisory issues (③⑤⑥⑦⑧⑨) ──────────────────────
def test_failed_command_collected_as_advisory():
    report = {"status": "complete", "summary": "ok"}
    evidence = RunToolEvidence(
        commands=[
            RunCommandEvidence(
                command="pytest", cwd="/ws", exit_code=1, timed_out=False, is_error=False
            )
        ]
    )
    summary = evaluate_task_result_report(_task(), report, evidence)
    assert summary.report_status == "complete"
    assert any("Failed command" in i for i in summary.advisory_issues)


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
    summary = evaluate_task_result_report(_task(), report, evidence)
    assert summary.report_status == "complete"
    assert not any("Failed command" in i for i in summary.advisory_issues)


def test_missing_acceptance_criteria_collected_as_advisory():
    task = _task(acceptanceCriteria=["build passes", "lint clean"])
    report = {
        "status": "complete",
        "summary": "ok",
        "acceptanceResults": [
            {"criterion": "build passes", "passed": True, "evidence": "exit 0"}
        ],
    }
    summary = evaluate_task_result_report(task, report)
    assert summary.report_status == "complete"
    assert any("Acceptance criteria" in i for i in summary.advisory_issues)
    assert any("lint clean" in i for i in summary.advisory_issues)


def test_missing_target_path_collected_as_advisory():
    task = _task(targetPaths=["src/foo.py"])
    report = {"status": "complete", "summary": "ok"}
    summary = evaluate_task_result_report(task, report)
    assert summary.report_status == "complete"
    assert any("Target path" in i for i in summary.advisory_issues)


def test_target_path_satisfied_no_advisory():
    task = _task(targetPaths=["src/foo.py"])
    report = {"status": "complete", "summary": "ok"}
    evidence = RunToolEvidence(
        file_writes=[RunFileEvidence(path="src/foo.py", absolute_path="/ws/src/foo.py")]
    )
    summary = evaluate_task_result_report(task, report, evidence)
    assert summary.report_status == "complete"
    assert not any("Target path" in i for i in summary.advisory_issues)


def test_missing_required_command_collected_as_advisory():
    task = _task(requiredCommands=[{"command": "pytest"}])
    report = {"status": "complete", "summary": "ok"}
    summary = evaluate_task_result_report(task, report)
    assert summary.report_status == "complete"
    assert any("Required command" in i for i in summary.advisory_issues)


def test_required_command_satisfied_no_advisory():
    task = _task(requiredCommands=[{"command": "pytest"}])
    report = {
        "status": "complete",
        "summary": "ok",
        "commandsRun": [{"command": "pytest -q", "exitCode": 0}],
    }
    summary = evaluate_task_result_report(task, report)
    assert summary.report_status == "complete"
    assert not any("Required command" in i for i in summary.advisory_issues)


def test_code_task_verification_gate_collected_as_advisory():
    task = _task(taskKind="code", expectedOutputs=[{"id": "out1", "type": "project"}])
    report = {"status": "complete", "summary": "ok"}
    summary = evaluate_task_result_report(task, report)
    assert summary.report_status == "complete"
    assert any("verification command" in i for i in summary.advisory_issues)


def test_code_task_verification_gate_passes_with_evidence():
    task = _task(taskKind="code")
    report = {"status": "complete", "summary": "ok"}
    evidence = RunToolEvidence(commands=[_ok_command("pnpm run build")])
    summary = evaluate_task_result_report(task, report, evidence)
    assert summary.report_status == "complete"
    assert not any("verification command" in i for i in summary.advisory_issues)


def test_install_only_does_not_satisfy_verification():
    task = _task(taskKind="code", expectedOutputs=[{"id": "out1", "type": "project"}])
    report = {"status": "complete", "summary": "ok"}
    evidence = RunToolEvidence(commands=[_ok_command("pnpm install")])
    summary = evaluate_task_result_report(task, report, evidence)
    assert summary.report_status == "complete"
    assert any("verification command" in i for i in summary.advisory_issues)


def test_required_evidence_missing_collected_as_advisory():
    task = _task(requiredEvidence=["screenshot attached"])
    report = {"status": "complete", "summary": "ok"}
    summary = evaluate_task_result_report(task, report)
    assert summary.report_status == "complete"
    assert any("Required evidence" in i for i in summary.advisory_issues)


def test_required_evidence_satisfied_no_advisory():
    task = _task(requiredEvidence=["screenshot attached"])
    report = {"status": "complete", "summary": "screenshot attached to PR"}
    summary = evaluate_task_result_report(task, report)
    assert summary.report_status == "complete"
    assert not any("Required evidence" in i for i in summary.advisory_issues)


def test_full_success_no_advisory_issues():
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
    summary = evaluate_task_result_report(task, report, evidence)
    assert summary.report_status == "complete"
    assert summary.advisory_issues == []


def test_is_verification_command_detection():
    assert is_verification_command("pnpm run test")
    assert is_verification_command("tsc")
    assert is_verification_command("cargo build")
    assert is_verification_command("python -m pytest")
    assert not is_verification_command("pnpm install")
    assert not is_verification_command("echo hello")


# ─── workspace-aware grading ──────────────────────
def test_code_task_without_toolchain_skips_verification_gate():
    task = _task(taskKind="code")
    report = {"status": "complete", "summary": "ok"}
    summary = evaluate_task_result_report(task, report, has_build_toolchain=False)
    assert summary.report_status == "complete"
    assert not any("verification command" in i for i in summary.advisory_issues)


def test_code_task_without_toolchain_skips_required_evidence():
    from app.services.dispatch_plan import CODE_TASK_RUNNABLE_REQUIRED_EVIDENCE
    task = _task(taskKind="code", requiredEvidence=[CODE_TASK_RUNNABLE_REQUIRED_EVIDENCE])
    report = {"status": "complete", "summary": "ok"}
    summary = evaluate_task_result_report(task, report, has_build_toolchain=False)
    assert summary.report_status == "complete"
    assert not any("Required evidence" in i for i in summary.advisory_issues)


def test_code_task_with_toolchain_still_requires_verification():
    task = _task(taskKind="code", expectedOutputs=[{"id": "out1", "type": "project"}])
    report = {"status": "complete", "summary": "ok"}
    summary = evaluate_task_result_report(task, report, has_build_toolchain=True)
    assert summary.report_status == "complete"
    assert any("verification command" in i for i in summary.advisory_issues)


# ─── prefix recovery tests ─────────────────────────
def test_failed_python_prefix_recovered_by_later_python_success():
    """Failed `python -c "bad"` recovered by later `python -c "good"` → no advisory."""
    report = {"status": "complete", "summary": "ok"}
    evidence = RunToolEvidence(
        commands=[
            RunCommandEvidence(
                command='python -c "bad"', cwd="/ws", exit_code=1, timed_out=False, is_error=False
            ),
            _ok_command('python -c "good"'),
        ]
    )
    summary = evaluate_task_result_report(_task(), report, evidence)
    assert summary.report_status == "complete"
    assert not any("Failed command" in i for i in summary.advisory_issues)


def test_failed_pytest_not_recovered_by_later_pnpm_build():
    """Failed `pytest` not recovered by later `pnpm build` → advisory issue."""
    report = {"status": "complete", "summary": "ok"}
    evidence = RunToolEvidence(
        commands=[
            RunCommandEvidence(
                command="pytest", cwd="/ws", exit_code=1, timed_out=False, is_error=False
            ),
            _ok_command("pnpm build"),
        ]
    )
    summary = evaluate_task_result_report(_task(), report, evidence)
    assert summary.report_status == "complete"
    assert any("Failed command" in i for i in summary.advisory_issues)
