"""Unit tests for verify_stage: each task_kind passed/failed scenarios."""

from app.schemas.dispatch import (
    DispatchExpectedOutput,
    DispatchPlanItem,
)
from app.services.verify_stage import verify_task_result


def _make_task(
    task_id: str = "task_1",
    task_kind: str | None = None,
    expected_outputs: list[DispatchExpectedOutput] | None = None,
    required_evidence: list[str] | None = None,
    depends_on: list[str] | None = None,
) -> DispatchPlanItem:
    return DispatchPlanItem(
        id=task_id,
        agent_id="ag_test",
        task="test task",
        task_kind=task_kind,
        expected_outputs=expected_outputs,
        required_evidence=required_evidence,
        depends_on=depends_on,
    )


class _FakeResult:
    """Minimal stand-in for DispatchTaskResult."""

    def __init__(
        self,
        status: str = "complete",
        artifact_ids: list[str] | None = None,
        output_artifacts: dict[str, str] | None = None,
        task_report: dict | None = None,
    ):
        self.status = status
        self.artifact_ids = artifact_ids or []
        self.output_artifacts = output_artifacts or {}
        self.task_report = task_report


# ─── code task ────────────────────────────────────────────────────────────────


def test_code_task_passes_with_project_artifact_and_evidence():
    task = _make_task(
        task_kind="code",
        expected_outputs=[DispatchExpectedOutput(id="project", type="project")],
        required_evidence=["pnpm build"],
    )
    result = _FakeResult(
        output_artifacts={"project": "art_123"},
        task_report={"commandsRun": [{"command": "pnpm build", "exitCode": 0}]},
    )
    vr = verify_task_result(task, result)
    assert vr.passed


def test_code_task_fails_without_project_artifact():
    task = _make_task(
        task_kind="code",
        expected_outputs=[DispatchExpectedOutput(id="project", type="project")],
    )
    result = _FakeResult(artifact_ids=[], output_artifacts={})
    vr = verify_task_result(task, result)
    assert not vr.passed
    assert "no project artifact" in vr.reason


def test_code_task_fails_with_missing_evidence():
    task = _make_task(
        task_kind="code",
        expected_outputs=[DispatchExpectedOutput(id="project", type="project")],
        required_evidence=["pnpm test"],
    )
    result = _FakeResult(
        output_artifacts={"project": "art_123"},
        task_report={"commandsRun": []},
    )
    vr = verify_task_result(task, result)
    assert not vr.passed
    assert "required evidence" in vr.reason


def test_code_task_passes_without_required_evidence():
    task = _make_task(
        task_kind="code",
        expected_outputs=[DispatchExpectedOutput(id="project", type="project")],
    )
    result = _FakeResult(output_artifacts={"project": "art_123"})
    vr = verify_task_result(task, result)
    assert vr.passed


# ─── document task ───────────────────────────────────────────────────────────


def test_document_task_passes_with_all_outputs():
    task = _make_task(
        task_kind="doc",
        expected_outputs=[DispatchExpectedOutput(id="doc_out", type="document")],
    )
    result = _FakeResult(output_artifacts={"doc_out": "art_456"})
    vr = verify_task_result(task, result)
    assert vr.passed


def test_document_task_fails_with_missing_output():
    task = _make_task(
        task_kind="doc",
        expected_outputs=[DispatchExpectedOutput(id="doc_out", type="document")],
    )
    result = _FakeResult(output_artifacts={})
    vr = verify_task_result(task, result)
    assert not vr.passed
    assert "missing expected outputs" in vr.reason


def test_document_task_passes_without_expected_outputs():
    task = _make_task(task_kind="doc")
    result = _FakeResult()
    vr = verify_task_result(task, result)
    assert vr.passed


# ─── review task ─────────────────────────────────────────────────────────────


def test_review_task_passes_referencing_upstream():
    task = _make_task(task_kind="review", depends_on=["task_A"])
    result = _FakeResult(task_report={"summary": "Reviewed task_A output, looks good"})
    vr = verify_task_result(task, result)
    assert vr.passed


def test_review_task_fails_not_referencing_upstream():
    task = _make_task(task_kind="review", depends_on=["task_A"])
    result = _FakeResult(task_report={"summary": "Looks good, no issues"})
    vr = verify_task_result(task, result)
    assert not vr.passed
    assert "does not reference upstream" in vr.reason


def test_review_task_passes_without_depends_on():
    task = _make_task(task_kind="review")
    result = _FakeResult()
    vr = verify_task_result(task, result)
    assert vr.passed


# ─── default / unknown task ──────────────────────────────────────────────────


def test_unknown_task_kind_passes():
    task = _make_task(task_kind="analysis")
    result = _FakeResult()
    vr = verify_task_result(task, result)
    assert vr.passed


def test_none_task_kind_passes():
    task = _make_task(task_kind=None)
    result = _FakeResult()
    vr = verify_task_result(task, result)
    assert vr.passed
