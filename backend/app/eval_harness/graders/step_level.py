"""
Step-level grader — compares the agent's tool-call sequence against an
expected trace (design decision D6, first version: exact index-by-index
comparison only).

From the trace spans, extracts the sequence of ``tool.call`` steps (tool name
from span attributes, falling back to the span name), aligns it with the
task's ``expected_trace`` config by index, reports the first wrong step and
scores ``correct_steps / total_steps``.

Config schema:
    {
        "expected_trace": ["fs_read", "fs_write", "bash"],  # required
        "threshold": 0.7,  # optional, pass threshold
    }

When ``expected_trace`` is not configured the grader auto-passes (nothing to
compare against), mirroring code_based's behavior with no checks.
"""

from __future__ import annotations

from typing import Any

from eval_harness.core.contract import EvalContext
from eval_harness.core.types import EvalTask, GraderResult, GraderType, TrialResult


class StepLevelGrader:
    """步骤级评估 — expected_trace 按索引对照, 定位首个错误步骤"""

    name = "step_level"

    async def grade(
        self,
        trial: TrialResult,
        spans: list[dict[str, Any]],
        task: EvalTask,
        context: EvalContext | None = None,
    ) -> GraderResult:
        config = task.get_grader_config(self.name)
        expected = config.get("expected_trace")
        threshold = config.get("threshold", 0.7)

        actual = self._extract_steps(spans)

        if not expected:
            return GraderResult(
                grader_name=self.name,
                grader_type=GraderType.CUSTOM,
                score=1.0,
                passed=True,
                explanation="No expected_trace configured, auto-pass",
                details={"actual_steps": actual},
            )

        total = len(expected)
        step_details: list[dict[str, Any]] = []
        first_error: int | None = None

        for i in range(total):
            expected_step = expected[i]
            actual_step = actual[i] if i < len(actual) else None
            ok = actual_step == expected_step
            if not ok and first_error is None:
                first_error = i
            step_details.append({
                "index": i,
                "expected": expected_step,
                "actual": actual_step,
                "correct": ok,
            })

        correct_count = sum(1 for s in step_details if s["correct"])
        score = correct_count / total if total > 0 else 1.0

        explanation = f"{correct_count}/{total} steps correct"
        if first_error is not None:
            explanation += (
                f"; first error at step {first_error}: "
                f"expected '{expected[first_error]}', "
                f"got '{actual[first_error] if first_error < len(actual) else None}'"
            )

        return GraderResult(
            grader_name=self.name,
            grader_type=GraderType.CUSTOM,
            score=score,
            passed=score >= threshold,
            explanation=explanation,
            details={
                "steps": step_details,
                "first_error_step": first_error,
                "actual_steps": actual,
                "extra_steps": (
                    actual[total:] if len(actual) > total else []
                ),
            },
        )

    @staticmethod
    def _extract_steps(spans: list[dict[str, Any]]) -> list[str]:
        """从 spans 提取 tool.call 步骤序列 (工具名, 回退到 span 名称)"""
        steps: list[str] = []
        for span in spans:
            name = span.get("name", "")
            if "tool.call" not in name and "tool_call" not in name:
                continue
            attrs = span.get("attributes", {}) or {}
            tool_name = (
                attrs.get("agenthub.tool_name")
                or attrs.get("tool_name")
                or name
            )
            steps.append(str(tool_name))
        return steps
