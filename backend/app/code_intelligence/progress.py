"""Parse CodeGraph verbose output into monotonic whole-run progress."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

_PHASES = ("scanning", "parsing", "storing", "resolving")
_PHASE_INDEX = {phase: index for index, phase in enumerate(_PHASES)}
_PHASE_RE = re.compile(r"Phase:\s*([a-z_]+)", re.IGNORECASE)
_PERCENT_RE = re.compile(r"\b\d+\s*/\s*\d+\s*\((\d{1,3})%\)")


@dataclass(frozen=True)
class CodeGraphProgress:
    phase: str
    phase_percent: int
    overall_percent: int


ProgressCallback = Callable[[CodeGraphProgress], None]


class CodeGraphProgressTracker:
    def __init__(self) -> None:
        self._phase: str | None = None
        self._overall_percent = -1

    def feed(self, line: str) -> CodeGraphProgress | None:
        phase_match = _PHASE_RE.search(line)
        if phase_match:
            phase = phase_match.group(1).lower()
            self._phase = phase if phase in _PHASE_INDEX else None
            return None

        if self._phase is None:
            return None
        percent_match = _PERCENT_RE.search(line)
        if percent_match is None:
            return None

        phase_percent = max(0, min(int(percent_match.group(1)), 100))
        phase_width = 100 / len(_PHASES)
        overall = round(
            (_PHASE_INDEX[self._phase] * phase_width)
            + (phase_percent * phase_width / 100)
        )
        overall = min(overall, 99)
        if overall <= self._overall_percent:
            return None
        self._overall_percent = overall
        return CodeGraphProgress(
            phase=self._phase,
            phase_percent=phase_percent,
            overall_percent=overall,
        )
