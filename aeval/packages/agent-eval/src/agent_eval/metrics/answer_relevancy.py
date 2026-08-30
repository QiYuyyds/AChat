"""P0 metric — Answer Relevancy (回答与问题的相关度)."""

from __future__ import annotations

from agent_eval.metrics.base import BaseLLMMetric, MetricError, MetricResult


class AnswerRelevancyMetric(BaseLLMMetric):
    """
    回答相关度: Agent 回答是否切题, 是否解决了用户的问题。

    评分逻辑: LLM 从回答中提取独立陈述并逐一判断与问题的相关度,
    输出平均相关度分数。
    """

    name = "answer_relevancy"

    _SYSTEM_PROMPT = """你是一个评测专家。请评估 Agent 的回答与用户问题的相关度。

步骤:
1. 从 Agent 回答中提取所有独立陈述
2. 对每个陈述, 判断它与用户问题的相关度 (0-1)
3. 计算平均相关度分数

以 JSON 格式返回:
{
  "statements": ["陈述1", "陈述2"],
  "relevancies": [0.9, 0.3],
  "score": 0.85,
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
        user_prompt = f"用户问题: {input}\n\nAgent 回答: {actual_output}"
        data = await self._llm_judge(self._SYSTEM_PROMPT, user_prompt)

        score = self._score_of(data)
        if "score" not in data:
            raise MetricError(f"judge response missing 'score': {data!r}")

        return MetricResult(
            name=self.name,
            score=score,
            reason=str(data.get("reason", "")),
            details={
                "statements": data.get("statements", []),
                "relevancies": data.get("relevancies", []),
            },
            threshold=self.threshold,
        )
