"""Verify RunSpanCollector produces correct data for run_rule_evaluations.

This is an integration test for the eval data pipeline fix:
  collector.record() → collector.collect() → run_rule_evaluations() → meaningful scores
"""

from app.observability.run_collector import run_span_collector
from app.observability.eval_rules import run_rule_evaluations


def test_collector_feeds_eval_rules():
    """Simulate a complete run: 2 LLM turns, 1 tool call, 1 finalize."""
    run_id = "test_run_eval_pipeline"
    run_span_collector.clear(run_id)

    # Simulate 2 LLM calls (turns)
    run_span_collector.record(
        run_id, "llm.generate",
        **{
            "agenthub.finish_reason": "tool_calls",
            "agenthub.input_tokens": 100,
            "agenthub.output_tokens": 50,
            "agenthub.turn": 1,
        },
    )
    run_span_collector.record(
        run_id, "llm.generate",
        **{
            "agenthub.finish_reason": "end_turn",
            "agenthub.input_tokens": 200,
            "agenthub.output_tokens": 80,
            "agenthub.turn": 2,
        },
    )

    # Simulate 1 successful tool call
    run_span_collector.record(
        run_id, "tool.call",
        **{
            "agenthub.tool_name": "bash",
            "agenthub.args_summary": "echo hello",
            "agenthub.success": True,
        },
    )

    # Simulate finalize
    run_span_collector.record(
        run_id, "agent.finalize",
        **{
            "agenthub.stop_reason": "end_turn",
            "agenthub.total_turns": 2,
        },
    )

    # Collect and evaluate
    spans = run_span_collector.collect(run_id)
    assert len(spans) == 4, f"Expected 4 spans, got {len(spans)}"

    scores = run_rule_evaluations("fake_trace_id", spans)

    # Verify scores are computed correctly
    score_map = {s.name: s for s in scores}
    assert "task_completion_rate" in score_map
    assert score_map["task_completion_rate"].score == 1.0, "end_turn should give 1.0"

    assert "tool_success_rate" in score_map
    assert score_map["tool_success_rate"].score == 1.0, "1/1 succeeded"

    assert "redundant_tool_calls" in score_map
    assert score_map["redundant_tool_calls"].score == 0.0, "0/1 redundant"

    assert "turns_to_complete" in score_map
    assert score_map["turns_to_complete"].score == 2.0, "2 turns"

    assert "token_usage" in score_map
    assert score_map["token_usage"].score == 430.0, "100+50+200+80=430"

    assert "error_detection" in score_map
    assert score_map["error_detection"].score == 1.0, "no errors"

    assert "max_turns_exceeded" in score_map
    assert score_map["max_turns_exceeded"].score == 1.0, "not abnormal"

    run_span_collector.clear(run_id)


def test_collector_empty_run():
    """Empty collector should produce default scores."""
    run_id = "test_empty_run"
    run_span_collector.clear(run_id)

    spans = run_span_collector.collect(run_id)
    assert len(spans) == 0

    scores = run_rule_evaluations("fake_trace_id", spans)
    score_map = {s.name: s for s in scores}

    # With no data, task_completion should be 0 (unknown finish_reason)
    assert score_map["task_completion_rate"].score == 0.0
    # No tools → default 1.0
    assert score_map["tool_success_rate"].score == 1.0

    run_span_collector.clear(run_id)


def test_collector_redundant_tools():
    """Two identical tool calls should be flagged as redundant."""
    run_id = "test_redundant"
    run_span_collector.clear(run_id)

    for _ in range(2):
        run_span_collector.record(
            run_id, "tool.call",
            **{
                "agenthub.tool_name": "bash",
                "agenthub.args_summary": "echo hi",
                "agenthub.success": True,
            },
        )

    spans = run_span_collector.collect(run_id)
    scores = run_rule_evaluations("fake_trace_id", spans)
    score_map = {s.name: s for s in scores}

    assert score_map["redundant_tool_calls"].score == 0.5, "1/2 redundant"

    run_span_collector.clear(run_id)
