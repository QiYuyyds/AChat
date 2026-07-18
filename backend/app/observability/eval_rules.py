"""Online rule-based evaluation — computes metrics from OTel span data.

Called after each agent run (async, non-blocking).  Results are written to
Phoenix as trace-level eval annotations via the Phoenix Python SDK.

No LLM calls — all metrics are computed purely from span attributes.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from app.config import get_settings
from app.observability.tracer import is_trace_enabled

logger = logging.getLogger(__name__)

# Absolute safety bound only — not a product max-steps score threshold.
SAFETY_MAX_MODEL_CALLS = 10_000

# stop_reason values that indicate abnormal (non-model-done) Custom termination
_ABNORMAL_STOP_REASONS = frozenset({
    "budget_forced_final",
    "budget_exhausted",
    "duplicate_tool_breaker",
    "tool_error_breaker",
    "compact_failure_breaker",
    "max_tool_turns",
})


@dataclass
class EvalScore:
    """A single evaluation score."""
    name: str
    score: float
    explanation: str = ""
    label: str = ""


def _get_span_attr(span: Any, key: str, default=None):
    """Get an attribute from a span-like dict or object."""
    if isinstance(span, dict):
        return span.get(key, default)
    return getattr(span, key, default)


def run_rule_evaluations(trace_id: str, spans: list[Any]) -> list[EvalScore]:
    """Compute all rule-based metrics from span data.

    *spans* is a list of span-like objects (dict or OTel Span) with
    attributes such as ``agenthub.turn``, ``agenthub.finish_reason``,
    ``agenthub.tool_name``, ``agenthub.success``, etc.
    """
    scores: list[EvalScore] = []

    # ── Collect span data by type ──
    llm_spans = [s for s in spans if _span_name_matches(s, "llm.generate")]
    tool_spans = [s for s in spans if _span_name_matches(s, "tool.call")]
    dispatch_spans = [s for s in spans if _span_name_matches(s, "tool.dispatch")]
    agent_run_spans = [s for s in spans if _span_name_matches(s, "agent.run")]
    finalize_spans = [s for s in spans if _span_name_matches(s, "agent.finalize")]
    error_spans = [s for s in spans if _span_has_error(s)]

    total_turns = 0
    for fs in finalize_spans:
        total_turns = max(total_turns, _get_span_attr(fs, "agenthub.total_turns", 0) or 0)

    # Prefer explicit stop_reason from finalize / agent.run spans when present
    stop_reason = ""
    for s in list(finalize_spans) + list(agent_run_spans):
        sr = _get_span_attr(s, "agenthub.stop_reason", None) or _get_span_attr(s, "stop_reason", None)
        if sr:
            stop_reason = str(sr)
            break

    # ── Task Completion ──
    finish_reason = ""
    if llm_spans:
        last_llm = llm_spans[-1]
        finish_reason = _get_span_attr(last_llm, "agenthub.finish_reason", "") or ""

    scores.append(EvalScore(
        name="task_completion_rate",
        score=1.0 if finish_reason == "end_turn" else 0.0,
        explanation=f"finish_reason={finish_reason or 'unknown'}",
    ))
    # Replaces old MAX_TURNS=8 gate: score 0 only for abnormal Custom stops or
    # absolute safety-bound blowups — not for long successful model-done runs.
    abnormal = stop_reason in _ABNORMAL_STOP_REASONS or total_turns >= SAFETY_MAX_MODEL_CALLS
    scores.append(EvalScore(
        name="max_turns_exceeded",
        score=0.0 if abnormal else 1.0,
        explanation=(
            f"stop_reason={stop_reason or 'n/a'}, total_turns={total_turns}, "
            f"safety_max={SAFETY_MAX_MODEL_CALLS}"
        ),
    ))

    # ── Tool Quality ──
    total_tools = len(tool_spans)
    successful_tools = sum(
        1 for s in tool_spans
        if _get_span_attr(s, "agenthub.success", True)
    )
    scores.append(EvalScore(
        name="tool_success_rate",
        score=(successful_tools / total_tools) if total_tools > 0 else 1.0,
        explanation=f"{successful_tools}/{total_tools} succeeded",
    ))

    # Redundant tool calls (same tool_name + args_summary)
    seen_calls: set[tuple] = set()
    redundant = 0
    for s in tool_spans:
        key = (
            _get_span_attr(s, "agenthub.tool_name", ""),
            _get_span_attr(s, "agenthub.args_summary", ""),
        )
        if key in seen_calls:
            redundant += 1
        else:
            seen_calls.add(key)
    scores.append(EvalScore(
        name="redundant_tool_calls",
        score=(redundant / total_tools) if total_tools > 0 else 0.0,
        explanation=f"{redundant}/{total_tools} redundant",
    ))

    # ── Step Efficiency ──
    input_tokens = sum(
        _get_span_attr(s, "agenthub.input_tokens", 0) or 0
        for s in llm_spans
    )
    output_tokens = sum(
        _get_span_attr(s, "agenthub.output_tokens", 0) or 0
        for s in llm_spans
    )
    scores.append(EvalScore(
        name="turns_to_complete",
        score=float(total_turns),
        explanation=f"total_turns={total_turns}",
    ))
    scores.append(EvalScore(
        name="token_usage",
        score=float(input_tokens + output_tokens),
        explanation=f"input={input_tokens}, output={output_tokens}",
    ))

    # ── Error Detection ──
    scores.append(EvalScore(
        name="error_detection",
        score=0.0 if error_spans else 1.0,
        explanation=f"{len(error_spans)} error spans" if error_spans else "no errors",
    ))

    # ── Collaboration metrics (only if dispatch spans exist) ──
    if dispatch_spans or len(agent_run_spans) > 1:
        max_depth = max(
            (_get_span_attr(s, "agenthub.dispatch_depth", 0) or 0)
            for s in dispatch_spans
        ) if dispatch_spans else 0
        scores.append(EvalScore(
            name="dispatch_depth",
            score=float(max_depth),
            explanation=f"max_dispatch_depth={max_depth}",
        ))
        scores.append(EvalScore(
            name="subtask_count",
            score=float(len(dispatch_spans)),
            explanation=f"{len(dispatch_spans)} dispatches",
        ))
        scores.append(EvalScore(
            name="total_agent_runs",
            score=float(len(agent_run_spans)),
            explanation=f"{len(agent_run_spans)} agent runs",
        ))

    return scores


def _span_name_matches(span: Any, key: str) -> bool:
    """Check if a span's name contains the given key."""
    name = ""
    if isinstance(span, dict):
        name = span.get("name", "") or span.get("span_name", "")
    else:
        name = getattr(span, "name", "") or ""
    return key in str(name)


def _span_has_error(span: Any) -> bool:
    """Check if a span has an error status."""
    if isinstance(span, dict):
        status = span.get("status", {})
        return status.get("code") == "ERROR" if isinstance(status, dict) else False
    status = getattr(span, "status", None)
    if status is None:
        return False
    status_code = getattr(status, "status_code", None)
    return str(status_code) == "StatusCode.ERROR" or "ERROR" in str(status_code)


async def run_eval_and_log(trace_id: str, spans: list[Any]) -> None:
    """Run rule evaluations and write results to Phoenix.

    This is the entry point called via ``asyncio.create_task`` after
    an agent run completes.  It never raises — failures are logged.
    """
    settings = get_settings()
    if not settings.eval_rule_enabled:
        return
    if not is_trace_enabled():
        return

    try:
        scores = await asyncio.to_thread(run_rule_evaluations, trace_id, spans)
        await _write_evals_to_phoenix(trace_id, scores)
        logger.info("Rule eval complete for trace %s: %d scores", trace_id, len(scores))
    except Exception as e:
        logger.warning("Rule eval failed for trace %s: %s", trace_id, e)


async def _write_evals_to_phoenix(trace_id: str, scores: list[EvalScore]) -> None:
    """Write evaluation scores to Phoenix as trace-level annotations."""
    settings = get_settings()
    try:
        import phoenix as px
        from phoenix.trace import SpanEvaluations

        client = px.Client(endpoint=settings.phoenix_ui_url)
        for score in scores:
            # Phoenix eval API expects a name, score, and explanation
            client.log_evaluations(
                SpanEvaluations(
                    eval_name=score.name,
                    scores=[(trace_id, score.score, score.explanation)],
                ),
            )
    except ImportError:
        logger.debug("phoenix SDK not installed, skipping eval write")
    except Exception as e:
        logger.warning("Phoenix eval write failed: %s", e)
