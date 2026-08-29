"""Unit tests for eval_harness core metrics (pass@k / pass^k / aggregation)."""

import pytest
from eval_harness.core.metrics import (
    aggregate_metrics,
    extract_metrics,
    pass_at_k,
    pass_power_k,
)
from eval_harness.core.types import TrialResult


def trial(success: bool, index: int = 0, score: float | None = None) -> TrialResult:
    kwargs: dict = {
        "trial_index": index,
        "success": success,
    }
    if score is not None:
        kwargs["grader_results"] = [
            {
                "grader_name": "g",
                "grader_type": "code",
                "score": score,
                "passed": score >= 0.7,
            }
        ]
    return TrialResult(**kwargs)


class TestPassAtK:
    def test_any_success_within_n(self):
        trials = [trial(False), trial(True), trial(True)]
        assert pass_at_k(trials, k=1) == 1.0
        assert pass_at_k(trials, k=3) == 1.0

    def test_all_failures(self):
        trials = [trial(False), trial(False), trial(False)]
        assert pass_at_k(trials, k=1) == 0.0
        assert pass_at_k(trials, k=3) == 0.0

    def test_binomial_extrapolation_spec_scenario(self):
        # spec: 3 trials with 1 success, pass@5 = 1-(1-1/3)^5 ≈ 0.868
        trials = [trial(True), trial(False), trial(False)]
        expected = 1.0 - (1.0 - 1.0 / 3.0) ** 5
        assert pass_at_k(trials, k=5) == pytest.approx(expected)
        assert pass_at_k(trials, k=5) == pytest.approx(0.868, abs=0.001)

    def test_extrapolation_zero_and_one(self):
        assert pass_at_k([trial(False)] * 3, k=5) == 0.0
        assert pass_at_k([trial(True)] * 3, k=5) == 1.0

    def test_edge_cases(self):
        assert pass_at_k([], k=1) == 0.0
        assert pass_at_k([trial(True)], k=0) == 0.0


class TestPassPowerK:
    def test_all_success_within_n(self):
        trials = [trial(True), trial(True), trial(True)]
        assert pass_power_k(trials, k=1) == 1.0
        assert pass_power_k(trials, k=3) == 1.0

    def test_any_failure_within_n(self):
        trials = [trial(True), trial(False), trial(True)]
        assert pass_power_k(trials, k=2) == 0.0

    def test_binomial_extrapolation(self):
        # spec: k > n 外推 P(全部成功) = p^k
        trials = [trial(True), trial(True), trial(False)]
        expected = (2.0 / 3.0) ** 5
        assert pass_power_k(trials, k=5) == pytest.approx(expected)

    def test_extrapolation_zero(self):
        assert pass_power_k([trial(False)] * 3, k=5) == 0.0

    def test_edge_cases(self):
        assert pass_power_k([], k=1) == 0.0
        assert pass_power_k([trial(True)], k=0) == 0.0


class TestAggregateMetrics:
    def test_avg_min_max(self):
        t1 = trial(True)
        t1.metrics = {"n_turns": 2.0, "latency_ms": 100.0}
        t2 = trial(True)
        t2.metrics = {"n_turns": 4.0, "latency_ms": 300.0}
        agg = aggregate_metrics([t1, t2])
        assert agg["n_turns_avg"] == 3.0
        assert agg["n_turns_min"] == 2.0
        assert agg["n_turns_max"] == 4.0
        assert agg["latency_ms_avg"] == 200.0

    def test_empty(self):
        assert aggregate_metrics([]) == {}


class TestExtractMetrics:
    def test_extracts_from_spans(self):
        spans = [
            {"name": "agent.turn", "attributes": {}},
            {"name": "agent.message", "attributes": {}},
            {
                "name": "tool.call",
                "attributes": {"agenthub.total_tokens": 120},
            },
            {
                "name": "tool.call",
                "attributes": {"llm.usage.total_tokens": "30"},
            },
        ]
        metrics = extract_metrics(
            spans, ["n_turns", "n_toolcalls", "n_total_tokens"]
        )
        assert metrics["n_turns"] == 2.0
        assert metrics["n_toolcalls"] == 2.0
        assert metrics["n_total_tokens"] == 150.0

    def test_non_numeric_tokens_ignored(self):
        spans = [
            {"name": "tool.call", "attributes": {"agenthub.total_tokens": "abc"}},
            {"name": "tool.call", "attributes": {"llm.usage.total_tokens": None}},
        ]
        metrics = extract_metrics(spans, ["n_total_tokens"])
        assert metrics == {"n_total_tokens": 0.0}

    def test_respects_tracked_list(self):
        spans = [{"name": "tool.call", "attributes": {}}]
        metrics = extract_metrics(spans, ["n_toolcalls"])
        assert metrics == {"n_toolcalls": 1.0}
