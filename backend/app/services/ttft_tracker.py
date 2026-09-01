"""TTFT (time-to-first-token) marks for the first-token latency breakdown.

A tiny run-scoped timestamp collector shared by AgentRunner (run lifecycle)
and CustomAdapter (request lifecycle) — a separate leaf module so the adapter
can mark without importing agent_runner (circular import).

Marks per ``run_id`` (first write wins — a mark is never overwritten):

=================  ==========================================================
``run_start``      execute_run publishes ``run.start``
``context_ready``  build_adapter_input returned (execute_simple_run)
``request_sent``   CustomAdapter.call_once issues the LLM HTTP request
``first_chunk``    CustomAdapter.call_once receives the first stream chunk
``first_delta``    the first ``part.delta`` event is published (consume_stream)
=================  ==========================================================

Turn-1 provider prefix-cache tokens are recorded alongside (first write
wins), and :func:`take` returns (and removes) the whole entry when
consume_stream logs the ``[ttft]`` breakdown at the run's first
``message.end``. Entries are diagnostic-only and bounded; the oldest entry is
dropped once the cap is reached.
"""

from __future__ import annotations

from app.utils.clock import now_ms

# Marks recorded for a run (usage keys are stored in the same dict).
_MARK_NAMES = (
    "run_start",
    "context_ready",
    "request_sent",
    "first_chunk",
    "first_delta",
)

_MAX_TRACKED_RUNS = 128

_marks: dict[str, dict[str, int]] = {}


def mark(run_id: str, name: str) -> None:
    """Record ``now_ms()`` for (run_id, name); first write wins."""
    entry = _marks.get(run_id)
    if entry is None:
        if len(_marks) >= _MAX_TRACKED_RUNS:
            # Diagnostic buffer full — drop the oldest entry.
            oldest = next(iter(_marks))
            _marks.pop(oldest, None)
        entry = {}
        _marks[run_id] = entry
    if name not in entry:
        entry[name] = now_ms()


def record_turn1_usage(
    run_id: str, input_tokens: int, cache_read_tokens: int
) -> None:
    """Record turn-1 provider usage (prefix-cache hit tokens); first write wins.

    Only the run's first LLM call contributes — later ReAct turns' usage is
    ignored, so the numbers reflect the exact request whose TTFT is measured.
    """
    entry = _marks.get(run_id)
    if entry is None or "usage_input_tokens" in entry:
        return
    entry["usage_input_tokens"] = input_tokens
    entry["usage_cache_read_tokens"] = cache_read_tokens


def take(run_id: str) -> dict[str, int] | None:
    """Return (and remove) the mark entry for run_id, or None if untracked."""
    return _marks.pop(run_id, None)
