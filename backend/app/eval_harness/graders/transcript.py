"""
Transcript grader — transcript analysis.

Analyzes the efficiency of an agent's execution:
- Number of turns vs limit
- Token usage vs limit
- Tool call redundancy

Config schema:
    {
        "max_turns": 20,
        "max_tokens": 10000,
        "threshold": 0.5
    }
"""

from __future__ import annotations

from typing import Any

from eval_harness.core.contract import EvalContext, Grader
from eval_harness.core.types import GraderResult, GraderType, EvalTask, TrialResult


class TranscriptGrader:
    """转录记录分析评分器"""

    name = "transcript"

    async def grade(
        self,
        trial: TrialResult,
        spans: list[dict[str, Any]],
        task: EvalTask,
        context: EvalContext | None = None,
    ) -> GraderResult:
        config = task.get_grader_config(self.name)
        max_turns = config.get("max_turns", 20)
        max_tokens = config.get("max_tokens", 10000)
        threshold = config.get("threshold", 0.5)

        # 从 metrics 获取实际值
        n_turns = trial.metrics.get("n_turns", 0)
        n_tokens = trial.metrics.get("n_total_tokens", 0)

        # 计算分数 (线性衰减)
        turns_score = max(0.0, 1.0 - n_turns / max_turns) if max_turns > 0 else 1.0
        tokens_score = max(0.0, 1.0 - n_tokens / max_tokens) if max_tokens > 0 else 1.0

        # 计算工具调用冗余度
        redundancy = self._calc_redundancy(spans)

        # 综合分数
        score = (turns_score + tokens_score + (1.0 - redundancy)) / 3.0

        return GraderResult(
            grader_name=self.name,
            grader_type=GraderType.TRANSCRIPT,
            score=score,
            passed=score >= threshold,
            explanation=(
                f"turns={n_turns}/{max_turns}, "
                f"tokens={n_tokens}/{max_tokens}, "
                f"redundancy={redundancy:.1%}"
            ),
            details={
                "turns_score": turns_score,
                "tokens_score": tokens_score,
                "redundancy": redundancy,
            },
        )

    def _calc_redundancy(self, spans: list[dict[str, Any]]) -> float:
        """计算工具调用冗余度"""
        tool_calls = [
            span.get("attributes", {}).get("agenthub.tool_name", "")
            for span in spans
            if "tool.call" in span.get("name", "")
        ]

        if not tool_calls:
            return 0.0

        unique_calls = set(tool_calls)
        # 冗余度 = 1 - (唯一调用数 / 总调用数)
        return 1.0 - len(unique_calls) / len(tool_calls)
