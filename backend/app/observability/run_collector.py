"""Per-run in-memory span attribute collector for online rule evaluation.

The OTel ``BatchSpanProcessor`` sends spans to Phoenix asynchronously, so
reading spans back from Phoenix immediately after a run completes is racy.
This collector captures the same span attributes in-process, keyed by
``run_id``, so ``_run_online_eval_hook`` can access them without delay.

The collected snapshots are plain dicts (``{"name": "llm.generate",
"agenthub.finish_reason": "end_turn", ...}``) so ``run_rule_evaluations``
can consume them unchanged via ``_get_span_attr`` / ``_span_name_matches``.

Lifecycle:
  1. ``record(run_id, span_name, **attrs)`` — called at span exit points
  2. ``collect(run_id)`` → ``list[dict]`` — called by eval hook
  3. ``clear(run_id)`` — called after eval to free memory
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class RunSpanCollector:
    """Per-run span attribute collector (in-memory singleton).

    Stores snapshots as plain dicts so that ``eval_rules._get_span_attr``
    (which checks ``isinstance(span, dict)`` first) and
    ``_span_name_matches`` can read them directly.
    """

    _runs: dict[str, list[dict[str, Any]]] = {}

    @classmethod
    def record(cls, run_id: str, span_name: str, **attrs: Any) -> None:
        """Record a span snapshot for the given run."""
        snapshots = cls._runs.get(run_id)
        if snapshots is None:
            snapshots = []
            cls._runs[run_id] = snapshots
        snapshot: dict[str, Any] = {"name": span_name}
        snapshot.update(attrs)
        snapshots.append(snapshot)

    @classmethod
    def collect(cls, run_id: str) -> list[dict[str, Any]]:
        """Return all span snapshots for a run (empty list if none)."""
        return cls._runs.get(run_id, [])

    @classmethod
    def clear(cls, run_id: str) -> None:
        """Remove all snapshots for a run."""
        cls._runs.pop(run_id, None)


run_span_collector = RunSpanCollector
