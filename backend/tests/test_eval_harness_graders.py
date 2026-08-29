"""Unit tests for eval_harness built-in graders: human / step_level / registry."""

import pytest
from eval_harness.core.types import EvalTask, GraderConfig, GraderType
from eval_harness.graders import (
    DEFAULT_GRADERS,
    get_grader_catalog,
)
from eval_harness.graders.human import HumanGrader
from eval_harness.graders.step_level import StepLevelGrader
from eval_harness.storage.memory import MemoryStorage


def make_task(**overrides) -> EvalTask:
    defaults = dict(
        id="t1",
        prompt="do it",
        graders=[GraderConfig(type=GraderType.CUSTOM, name="human")],
    )
    defaults.update(overrides)
    return EvalTask(**defaults)


# ─── HumanGrader (pending 语义) ───────────────────────────────────────────────


class TestHumanGrader:
    async def test_pending_result_semantics(self):
        grader = HumanGrader()
        task = make_task()
        trial = _trial()
        result = await grader.grade(trial, [], task)

        assert result.grader_name == "human"
        assert result.score == 0.0
        assert result.passed is False
        assert result.confidence == 0.0
        assert result.details["status"] == "pending"

    async def test_score_request_written_to_storage(self):
        storage = MemoryStorage()
        grader = HumanGrader(storage=storage)
        task = make_task()
        result = await grader.grade(_trial(), [], task, context=_context())

        requests = await storage.list_human_score_requests()
        assert len(requests) == 1
        req = requests[0]
        assert req["run_id"] == "run_x"
        assert req["task_id"] == "t1"
        assert req["trial_index"] == 0
        assert req["grader_name"] == "human"
        assert result.details["request"]["task_id"] == "t1"

    async def test_works_without_storage(self):
        grader = HumanGrader(storage=None)
        result = await grader.grade(_trial(), [], make_task(), context=_context())
        assert result.details["status"] == "pending"

    async def test_storage_without_optional_method_is_tolerated(self):
        class MinimalStorage:
            pass

        grader = HumanGrader(storage=MinimalStorage())
        result = await grader.grade(_trial(), [], make_task(), context=_context())
        assert result.details["status"] == "pending"


# ─── StepLevelGrader ──────────────────────────────────────────────────────────


def _tool_span(name: str) -> dict:
    return {"name": "tool.call", "attributes": {"agenthub.tool_name": name}}


class TestStepLevelGrader:
    async def test_all_steps_correct(self):
        grader = StepLevelGrader()
        task = make_task(
            graders=[GraderConfig(
                type=GraderType.CUSTOM,
                name="step_level",
                config={"expected_trace": ["fs_read", "fs_write"]},
            )],
        )
        spans = [_tool_span("fs_read"), _tool_span("fs_write")]
        result = await grader.grade(_trial(), spans, task)

        assert result.score == 1.0
        assert result.passed is True
        assert result.details["first_error_step"] is None

    async def test_partial_mismatch_reports_first_error(self):
        grader = StepLevelGrader()
        task = make_task(
            graders=[GraderConfig(
                type=GraderType.CUSTOM,
                name="step_level",
                config={"expected_trace": ["fs_read", "fs_write", "bash"]},
            )],
        )
        spans = [_tool_span("fs_read"), _tool_span("grep"), _tool_span("bash")]
        result = await grader.grade(_trial(), spans, task)

        assert result.score == pytest.approx(2 / 3)
        assert result.details["first_error_step"] == 1
        assert "first error at step 1" in result.explanation
        assert "expected 'fs_write'" in result.explanation

    async def test_missing_steps_count_as_wrong(self):
        grader = StepLevelGrader()
        task = make_task(
            graders=[GraderConfig(
                type=GraderType.CUSTOM,
                name="step_level",
                config={"expected_trace": ["fs_read", "fs_write", "bash"]},
            )],
        )
        spans = [_tool_span("fs_read")]
        result = await grader.grade(_trial(), spans, task)

        assert result.score == pytest.approx(1 / 3)
        assert result.details["first_error_step"] == 1
        assert result.details["steps"][1]["actual"] is None

    async def test_no_expected_trace_auto_passes(self):
        grader = StepLevelGrader()
        task = make_task(
            graders=[GraderConfig(type=GraderType.CUSTOM, name="step_level")],
        )
        spans = [_tool_span("fs_read")]
        result = await grader.grade(_trial(), spans, task)

        assert result.score == 1.0
        assert result.passed is True
        assert "auto-pass" in result.explanation

    async def test_extra_steps_recorded(self):
        grader = StepLevelGrader()
        task = make_task(
            graders=[GraderConfig(
                type=GraderType.CUSTOM,
                name="step_level",
                config={"expected_trace": ["fs_read"]},
            )],
        )
        spans = [_tool_span("fs_read"), _tool_span("extra_tool")]
        result = await grader.grade(_trial(), spans, task)

        assert result.score == 1.0
        assert result.details["extra_steps"] == ["extra_tool"]


# ─── Registry ─────────────────────────────────────────────────────────────────


class TestRegistry:
    def test_nine_builtin_graders_registered(self):
        names = {entry["name"] for entry in get_grader_catalog()}
        assert names == {
            "code_based",
            "model_based",
            "state_check",
            "tool_calls",
            "transcript",
            "artifact_check",
            "human",
            "step_level",
            "metric",
        }

    def test_catalog_entries_have_metadata(self):
        catalog = get_grader_catalog()
        for entry in catalog:
            assert set(entry) == {"name", "type", "description"}
            assert entry["type"]
            assert entry["description"]

    def test_default_graders_matches_registry(self):
        assert len(DEFAULT_GRADERS) == len(get_grader_catalog())
        assert [g.name for g in DEFAULT_GRADERS] == [
            e["name"] for e in get_grader_catalog()
        ]


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _trial() -> "EvalTask":  # noqa: F821
    from eval_harness.core.types import TrialResult

    return TrialResult(
        trial_index=0,
        trace_id="trace_t1",
        transcript=[{"role": "user", "content": "do it"}],
        outcome={"success": True},
    )


def _context() -> "object":
    from eval_harness.core.contract import EvalContext

    return EvalContext(run_id="run_x", task=make_task(), trial=_trial())
