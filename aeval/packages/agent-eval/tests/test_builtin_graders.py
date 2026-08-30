"""Unit tests for the six pre-existing built-in graders (coverage for task 6.4)."""

import pytest
from agent_eval.core.types import EvalTask, GraderConfig, GraderType, TrialResult
from agent_eval.graders.artifact_check import ArtifactCheckGrader
from agent_eval.graders.code_based import CodeBasedGrader
from agent_eval.graders.model_based import ModelBasedGrader
from agent_eval.graders.state_check import StateCheckGrader
from agent_eval.graders.tool_calls import ToolCallsGrader
from agent_eval.graders.transcript import TranscriptGrader


def make_task(grader_name: str, grader_type: GraderType, config: dict) -> EvalTask:
    return EvalTask(
        id="t1",
        prompt="p",
        graders=[GraderConfig(type=grader_type, name=grader_name, config=config)],
    )


def make_trial(outcome: dict | None = None, metrics: dict | None = None) -> TrialResult:
    return TrialResult(
        trial_index=0,
        transcript=[{"role": "user", "content": "hello"}, {"role": "assistant", "content": "world"}],
        outcome=outcome if outcome is not None else {},
        metrics=metrics or {},
    )


def tool_span(tool: str) -> dict:
    return {"name": "tool.call", "attributes": {"agenthub.tool_name": tool}}


# ─── code_based ───────────────────────────────────────────────────────────────


class TestCodeBasedGrader:
    async def test_no_checks_auto_pass(self):
        grader = CodeBasedGrader()
        result = await grader.grade(make_trial(), [], make_task("code_based", GraderType.CODE, {}))
        assert result.score == 1.0 and result.passed is True

    @pytest.mark.parametrize(
        "check,text_ok",
        [
            ({"type": "contains", "value": "world", "target": "transcript"}, True),
            ({"type": "contains", "value": "missing", "target": "transcript"}, False),
            ({"type": "not_contains", "value": "absent", "target": "transcript"}, True),
            ({"type": "not_contains", "value": "world", "target": "transcript"}, False),
            ({"type": "regex", "value": r"w.rld", "target": "transcript"}, True),
            ({"type": "regex", "value": r"\d+", "target": "transcript"}, False),
            ({"type": "unknown_type", "value": "x"}, False),
        ],
    )
    async def test_check_types(self, check, text_ok):
        grader = CodeBasedGrader()
        result = await grader.grade(
            make_trial(), [], make_task("code_based", GraderType.CODE, {"checks": [check]})
        )
        assert (result.score == 1.0) is text_ok

    async def test_exact_match(self):
        grader = CodeBasedGrader()
        task = make_task("code_based", GraderType.CODE, {
            "checks": [{"type": "exact", "value": "42", "target": "outcome"}]
        })
        result = await grader.grade(make_trial(outcome={"result": "42"}), [], task)
        # exact 比较的是整个序列化文本, 而非子串 → 不等于
        assert result.details["checks"][0]["passed"] is False

    async def test_partial_score_and_threshold(self):
        grader = CodeBasedGrader()
        task = make_task("code_based", GraderType.CODE, {
            "checks": [
                {"type": "contains", "value": "hello", "target": "transcript"},
                {"type": "contains", "value": "nope", "target": "transcript"},
            ],
            "threshold": 0.5,
        })
        result = await grader.grade(make_trial(), [], task)
        assert result.score == 0.5
        assert result.passed is True


# ─── model_based ──────────────────────────────────────────────────────────────


class TestModelBasedGrader:
    async def test_scores_from_llm_response(self):
        def llm(system: str, user: str) -> str:
            return '```json\n{"correctness": 0.9, "completeness": 0.7}\n```'

        grader = ModelBasedGrader(llm_fn=llm)
        task = make_task("model_based", GraderType.MODEL, {
            "rubric": "be correct",
            "dimensions": ["correctness", "completeness"],
        })
        result = await grader.grade(make_trial(), [], task)
        assert result.score == pytest.approx(0.8)
        assert result.passed is True
        assert result.details["dimensions"] == {"correctness": 0.9, "completeness": 0.7}

    async def test_llm_failure_scores_zero(self):
        def bad_llm(system: str, user: str) -> str:
            raise RuntimeError("api down")

        grader = ModelBasedGrader(llm_fn=bad_llm)
        result = await grader.grade(make_trial(), [], make_task("model_based", GraderType.MODEL, {}))
        assert result.score == 0.0
        assert result.passed is False
        assert "LLM call failed" in result.explanation

    async def test_unparseable_response_defaults_to_half(self):
        grader = ModelBasedGrader(llm_fn=lambda s, u: "no json here")
        result = await grader.grade(make_trial(), [], make_task("model_based", GraderType.MODEL, {
            "dimensions": ["quality"],
        }))
        assert result.details["dimensions"] == {"quality": 0.5}

    async def test_prompt_contains_rubric_and_transcript(self):
        grader = ModelBasedGrader()
        prompt = grader._build_prompt(make_trial(), "must be nice", ["quality"])
        assert "must be nice" in prompt
        assert "hello" in prompt


# ─── state_check ──────────────────────────────────────────────────────────────


class TestStateCheckGrader:
    async def test_no_expectations_auto_pass(self):
        grader = StateCheckGrader()
        result = await grader.grade(make_trial(), [], make_task("state_check", GraderType.STATE, {}))
        assert result.score == 1.0 and result.passed is True

    async def test_file_expectations(self):
        outcome = {"files": {"out.py": "def main():\n    pass\n"}}
        grader = StateCheckGrader()
        task = make_task("state_check", GraderType.STATE, {
            "expectations": [
                {"type": "file_exists", "path": "out.py"},
                {"type": "file_contains", "path": "out.py", "value": "def main"},
                {"type": "file_regex", "path": "out.py", "value": r"def \w+\("},
                {"type": "file_exists", "path": "missing.py"},
            ],
        })
        result = await grader.grade(make_trial(outcome=outcome), [], task)
        assert result.score == 0.75
        assert result.details["expectations"][3]["passed"] is False

    async def test_db_record_and_conflict_markers(self):
        outcome = {
            "db_records": [{"id": 1, "name": "a"}],
            "files": {"clean.py": "x = 1"},
        }
        grader = StateCheckGrader()
        task = make_task("state_check", GraderType.STATE, {
            "expectations": [
                {"type": "db_record", "match": {"id": 1}},
                {"type": "db_record", "match": {"id": 99}},
                {"type": "no_conflict_markers", "path": "clean.py"},
            ],
        })
        result = await grader.grade(make_trial(outcome=outcome), [], task)
        assert result.score == pytest.approx(2 / 3)

    async def test_unknown_expectation_type_fails(self):
        grader = StateCheckGrader()
        task = make_task("state_check", GraderType.STATE, {
            "expectations": [{"type": "mystery"}],
        })
        result = await grader.grade(make_trial(), [], task)
        assert result.score == 0.0


# ─── tool_calls ───────────────────────────────────────────────────────────────


class TestToolCallsGrader:
    async def test_required_and_forbidden(self):
        grader = ToolCallsGrader()
        task = make_task("tool_calls", GraderType.TOOL_CALLS, {
            "required_tools": ["fs_read", "fs_write"],
            "forbidden_tools": ["bash"],
        })
        spans = [tool_span("fs_read"), tool_span("fs_write")]
        result = await grader.grade(make_trial(), spans, task)
        assert result.score == 1.0 and result.passed is True

    async def test_missing_required_tools(self):
        grader = ToolCallsGrader()
        task = make_task("tool_calls", GraderType.TOOL_CALLS, {
            "required_tools": ["fs_read", "fs_write"],
        })
        result = await grader.grade(make_trial(), [tool_span("fs_read")], task)
        assert result.score == 0.5
        assert result.details["missing"] == ["fs_write"]

    async def test_forbidden_tool_violation_scores_zero(self):
        grader = ToolCallsGrader()
        task = make_task("tool_calls", GraderType.TOOL_CALLS, {
            "forbidden_tools": ["bash"],
        })
        result = await grader.grade(make_trial(), [tool_span("bash")], task)
        assert result.score == 0.0 and result.passed is False
        assert result.details["violated"] == ["bash"]

    async def test_no_config_passes(self):
        grader = ToolCallsGrader()
        result = await grader.grade(make_trial(), [], make_task("tool_calls", GraderType.TOOL_CALLS, {}))
        assert result.score == 1.0
        assert result.explanation == "No tool calls checked"

    def test_extract_tool_call_fallbacks(self):
        grader = ToolCallsGrader()
        calls = grader._extract_tool_calls([
            {"name": "tool.call", "attributes": {"tool.name": "legacy"}},
            {"name": "tool.call", "attributes": {}},
        ])
        assert calls[0]["name"] == "legacy"
        assert calls[1]["name"] == ""
        assert calls[1]["success"] is True


# ─── transcript ───────────────────────────────────────────────────────────────


class TestTranscriptGrader:
    async def test_score_with_metrics(self):
        grader = TranscriptGrader()
        task = make_task("transcript", GraderType.TRANSCRIPT, {
            "max_turns": 10, "max_tokens": 1000, "threshold": 0.9,
        })
        trial = make_trial(metrics={"n_turns": 5, "n_total_tokens": 500})
        result = await grader.grade(trial, [], task)
        # turns_score=0.5, tokens_score=0.5, redundancy=0 → (0.5+0.5+1)/3
        assert result.score == pytest.approx(2 / 3)
        assert result.passed is False

    async def test_no_tool_calls_zero_redundancy(self):
        grader = TranscriptGrader()
        assert grader._calc_redundancy([]) == 0.0

    def test_redundancy_calculation(self):
        grader = TranscriptGrader()
        spans = [tool_span("a"), tool_span("a"), tool_span("b")]
        # 2 unique / 3 total → redundancy = 1/3
        assert grader._calc_redundancy(spans) == pytest.approx(1 / 3)


# ─── artifact_check ───────────────────────────────────────────────────────────


class TestArtifactCheckGrader:
    async def test_no_artifacts_scores_zero(self):
        grader = ArtifactCheckGrader()
        result = await grader.grade(make_trial(), [], make_task("artifact_check", GraderType.ARTIFACT, {}))
        assert result.score == 0.0 and result.passed is False

    async def test_type_and_content_match(self):
        grader = ArtifactCheckGrader()
        outcome = {"artifacts": [{"type": "code_file", "content": "def hello(): pass"}]}
        task = make_task("artifact_check", GraderType.ARTIFACT, {
            "expected_type": "code_file",
            "content_regex": r"def \w+\(",
        })
        result = await grader.grade(make_trial(outcome=outcome), [], task)
        assert result.score == 1.0 and result.passed is True

    async def test_type_mismatch_scores_zero(self):
        grader = ArtifactCheckGrader()
        outcome = {"artifacts": [{"type": "document", "content": "x"}]}
        task = make_task("artifact_check", GraderType.ARTIFACT, {"expected_type": "code_file"})
        result = await grader.grade(make_trial(outcome=outcome), [], task)
        assert result.score == 0.0
        assert "Expected type" in result.explanation

    async def test_content_mismatch_scores_partial(self):
        grader = ArtifactCheckGrader()
        outcome = {"artifacts": [{"type": "code_file", "content": "no match here"}]}
        task = make_task("artifact_check", GraderType.ARTIFACT, {"content_regex": r"def \w+\("})
        result = await grader.grade(make_trial(outcome=outcome), [], task)
        assert result.score == 0.3

    def test_extract_artifacts_from_spans(self):
        grader = ArtifactCheckGrader()
        spans = [{
            "name": "artifact.create",
            "attributes": {
                "agenthub.artifact_type": "code_file",
                "agenthub.artifact_id": "art_1",
                "agenthub.content": "# code",
            },
        }]
        artifacts = grader._extract_artifacts(make_trial(), spans)
        assert artifacts[0]["type"] == "code_file"
        assert artifacts[0]["id"] == "art_1"
