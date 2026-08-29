"""AChat 特定评分器单测 (任务 2.5) — 合成 spans 覆盖通过/失败/空 dispatch。

不依赖 DB / Phoenix: dispatch 完成率经 ``agenthub.success`` 属性 (显式契约)
或注入的 ``db_lookup`` 驱动。
"""

from __future__ import annotations

import pytest

from eval_harness.core.types import EvalTask, GraderConfig, GraderType, TrialResult

from eval_integration.graders import AChatArtifactGrader, AChatDispatchGrader


def _task(grader_name: str, **config) -> EvalTask:
    return EvalTask(
        id="t1",
        prompt="p",
        graders=[GraderConfig(type=GraderType.CUSTOM, name=grader_name, config=config)],
    )


def _trial(outcome: dict | None = None) -> TrialResult:
    return TrialResult(trial_index=0, outcome=outcome or {})


def _dispatch(name: str, **attrs) -> dict:
    return {"name": name, "attributes": {"agenthub.dispatch_depth": 1, **attrs}}


# ─── achat_artifact ──────────────────────────────────────────────────────────


class TestAChatArtifactGrader:
    async def test_pass_with_outcome_artifacts(self):
        grader = AChatArtifactGrader()
        result = await grader.grade(
            _trial({"artifacts": [{"id": "a1", "type": "web_app"}]}),
            [],
            _task("achat_artifact"),
        )
        assert result.passed and result.score == 1.0

    async def test_fail_when_no_artifacts(self):
        grader = AChatArtifactGrader()
        result = await grader.grade(_trial({}), [], _task("achat_artifact"))
        assert not result.passed
        assert result.score == 0.0
        assert "No artifact" in result.explanation

    async def test_fail_on_type_mismatch(self):
        grader = AChatArtifactGrader()
        result = await grader.grade(
            _trial({"artifacts": [{"id": "a1", "type": "document"}]}),
            [],
            _task("achat_artifact", expected_type="web_app"),
        )
        assert not result.passed
        assert "web_app" in result.explanation

    async def test_type_mismatch_accepts_list(self):
        grader = AChatArtifactGrader()
        result = await grader.grade(
            _trial({"artifacts": [{"id": "a1", "type": "document"}]}),
            [],
            _task("achat_artifact", expected_type=["web_app", "document"]),
        )
        assert result.passed

    async def test_fallback_to_artifact_create_spans(self):
        grader = AChatArtifactGrader()
        spans = [
            {"name": "artifact.create",
             "attributes": {"agenthub.artifact_id": "a9",
                            "agenthub.artifact_type": "code_file"}},
        ]
        result = await grader.grade(_trial({}), spans, _task("achat_artifact"))
        assert result.passed
        assert result.details["types"] == ["code_file"]


# ─── achat_dispatch ──────────────────────────────────────────────────────────


class TestAChatDispatchGrader:
    async def test_pass_all_subtasks_completed(self):
        grader = AChatDispatchGrader()
        spans = [
            _dispatch("tool.dispatch", **{"agenthub.success": True,
                                          "agenthub.dispatch_depth": 1}),
            _dispatch("tool.dispatch", **{"agenthub.success": True,
                                          "agenthub.dispatch_depth": 2}),
        ]
        result = await grader.grade(_trial({}), spans, _task("achat_dispatch"))
        assert result.passed and result.score == 1.0
        assert result.details["n_subtasks"] == 2
        assert result.details["max_depth"] == 2
        assert result.details["completion_source"] == "span:agenthub.success"

    async def test_fail_below_threshold(self):
        grader = AChatDispatchGrader()
        spans = [
            _dispatch("tool.dispatch", **{"agenthub.success": True}),
            _dispatch("tool.dispatch", **{"agenthub.success": False}),
            _dispatch("tool.dispatch", **{"agenthub.success": False}),
        ]
        result = await grader.grade(_trial({}), spans, _task("achat_dispatch"))
        assert not result.passed
        assert result.score == pytest.approx(1 / 3)

    async def test_custom_threshold(self):
        grader = AChatDispatchGrader()
        spans = [
            _dispatch("tool.dispatch", **{"agenthub.success": True}),
            _dispatch("tool.dispatch", **{"agenthub.success": False}),
        ]
        result = await grader.grade(
            _trial({}), spans, _task("achat_dispatch", threshold=0.5)
        )
        assert result.passed

    async def test_empty_dispatch_fails(self):
        grader = AChatDispatchGrader()
        result = await grader.grade(_trial({}), [], _task("achat_dispatch"))
        assert not result.passed and result.score == 0.0
        assert result.details["n_subtasks"] == 0

    async def test_completion_from_db_lookup(self):
        async def db_lookup(parent_run_ids):
            assert parent_run_ids == ["run_parent"]
            return {"c1": "complete", "c2": "failed", "c3": "complete"}

        grader = AChatDispatchGrader(db_lookup=db_lookup)
        spans = [_dispatch("tool.dispatch"), _dispatch("tool.dispatch"),
                 _dispatch("tool.dispatch")]
        trial = _trial({"run_ids": ["run_parent"]})
        result = await grader.grade(trial, spans, _task("achat_dispatch"))
        assert result.score == pytest.approx(2 / 3)
        assert result.details["completion_source"] == "db:agent_runs"

    async def test_completion_from_finalize_spans(self):
        grader = AChatDispatchGrader()
        spans = [
            _dispatch("tool.dispatch"),
            _dispatch("tool.dispatch"),
            {"name": "agent.finalize",
             "attributes": {"agenthub.run_id": "child_1",
                            "agenthub.stop_reason": "complete"}},
            {"name": "agent.finalize",
             "attributes": {"agenthub.run_id": "child_2",
                            "agenthub.stop_reason": "budget_exhausted"}},
            # 顶层 run 自身的 finalize 必须被排除
            {"name": "agent.finalize",
             "attributes": {"agenthub.run_id": "run_parent",
                            "agenthub.stop_reason": "complete"}},
        ]
        trial = _trial({"run_ids": ["run_parent"]})
        result = await grader.grade(trial, spans, _task("achat_dispatch"))
        assert result.score == pytest.approx(0.5)
        assert result.details["completion_source"] == "span:agent.finalize"

    async def test_unknown_completion_is_zero(self):
        async def empty_lookup(parent_run_ids):
            return {}

        grader = AChatDispatchGrader(db_lookup=empty_lookup)
        spans = [_dispatch("tool.dispatch")]
        result = await grader.grade(_trial({}), spans, _task("achat_dispatch"))
        assert result.score == 0.0
        assert not result.passed
        assert result.details["completion_source"] == "unknown"
