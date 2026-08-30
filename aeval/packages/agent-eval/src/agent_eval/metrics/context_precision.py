"""P0 metric — Context Precision (retrieval_context 中相关文档占比)."""

from __future__ import annotations

from agent_eval.metrics.base import BaseLLMMetric, MetricError, MetricResult


class ContextPrecisionMetric(BaseLLMMetric):
    """
    上下文精确率: 检索到的文档中有多少真正与问题相关。

    缺 retrieval_context 明确失败 (score=0 + 理由)。
    """

    name = "context_precision"

    _SYSTEM_PROMPT = """你是一个信息检索评测专家。请评估检索上下文的精确率。

步骤:
1. 对检索上下文中的每个文档, 判断它是否对回答用户问题有用
2. 计算精确率 = 有用文档数 / 总文档数

以 JSON 格式返回:
{
  "documents": [{"index": 0, "relevant": true, "reason": "..."}],
  "score": 0.67,
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
        if not docs:
            return MetricResult(
                name=self.name,
                score=0.0,
                reason="需要 retrieval_context 才能评估精确率",
                details={"error": "missing_parameters"},
                threshold=self.threshold,
            )

        doc_lines = "\n".join(f"[{i}] {doc}" for i, doc in enumerate(docs))
        user_prompt = f"用户问题: {input}\n\n检索文档:\n{doc_lines}"
        data = await self._llm_judge(self._SYSTEM_PROMPT, user_prompt)

        if "score" not in data:
            raise MetricError(f"judge response missing 'score': {data!r}")
        score = self._score_of(data)

        return MetricResult(
            name=self.name,
            score=score,
            reason=str(data.get("reason", "")),
            details={"documents": data.get("documents", [])},
            threshold=self.threshold,
        )
