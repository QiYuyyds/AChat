"""P0 metric — Context Recall (expected_output 信息点被 retrieval_context 覆盖率)."""

from __future__ import annotations

from eval_harness.metrics.base import BaseLLMMetric, MetricError, MetricResult


class ContextRecallMetric(BaseLLMMetric):
    """
    上下文召回率: 检索到的文档是否包含回答所需的全部信息。

    需要 expected_output (信息点来源) 与 retrieval_context (检索结果);
    缺任一参数明确失败 (score=0 + 理由)。
    """

    name = "context_recall"

    _SYSTEM_PROMPT = """你是一个信息检索评测专家。请评估检索上下文的召回率。

步骤:
1. 从期望回答中提取所有关键信息点 (facts/claims)
2. 对每个信息点, 检查检索上下文中是否包含
3. 计算召回率 = 被覆盖的信息点数 / 总信息点数

以 JSON 格式返回:
{
  "information_points": ["信息点1", "信息点2"],
  "covered": [true, false],
  "score": 0.75,
  "reason": "一句话理由"
}"""

    async def measure(
        self,
        input: str,
        actual_output: str,
        expected_output: str | None = None,
        context: list[str] | None = None,
        retrieval_context: list[str] | None = None,
    ) -> MetricResult:
        docs = [d for d in (retrieval_context or []) if str(d).strip()]
        if not expected_output or not expected_output.strip() or not docs:
            return MetricResult(
                name=self.name,
                score=0.0,
                reason="需要 expected_output 和 retrieval_context 才能评估召回率",
                details={"error": "missing_parameters"},
                threshold=self.threshold,
            )

        retrieval_str = "\n---\n".join(str(d) for d in docs)
        user_prompt = (
            f"期望回答 (包含所有应覆盖的信息):\n{expected_output}\n\n"
            f"检索上下文:\n{retrieval_str}"
        )
        data = await self._llm_judge(self._SYSTEM_PROMPT, user_prompt)

        if "score" not in data:
            raise MetricError(f"judge response missing 'score': {data!r}")
        score = self._score_of(data)

        return MetricResult(
            name=self.name,
            score=score,
            reason=str(data.get("reason", "")),
            details={
                "information_points": data.get("information_points", []),
                "covered": data.get("covered", []),
            },
            threshold=self.threshold,
        )
