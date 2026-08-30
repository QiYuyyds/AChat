"""P0 metric — Faithfulness (回答忠于 context 的程度, 防幻觉)."""

from __future__ import annotations

from agent_eval.metrics.base import BaseLLMMetric, MetricError, MetricResult


class FaithfulnessMetric(BaseLLMMetric):
    """
    忠实度: Agent 回答是否完全基于给定上下文, 无幻觉。

    无 context 时明确失败 (score=0 + 明确理由), 不猜测 (spec 场景:
    "对 faithfulness 计算但不提供 context → score=0 与'无上下文无法评估'")。
    """

    name = "faithfulness"

    _SYSTEM_PROMPT = """你是一个事实核查专家。请评估 Agent 回答是否忠于给定上下文。

步骤:
1. 从 Agent 回答中提取所有事实性陈述
2. 对每个陈述, 在上下文中查找支持证据
3. 判断: supported / unsupported
4. 计算忠实度 = 有支持的陈述数 / 总陈述数

以 JSON 格式返回:
{
  "claims": ["陈述1", "陈述2"],
  "verdicts": ["supported", "unsupported"],
  "unsupported_claims": ["不被上下文支持的陈述"],
  "score": 0.8,
  "reason": "一句话理由 (标注不被支持的陈述)"
}"""

    async def measure(
        self,
        input: str,
        actual_output: str,
        expected_output: str | None = None,
        context: list[str] | None = None,
        retrieval_context: list[str] | None = None,
    ) -> MetricResult:
        context = [c for c in (context or []) if str(c).strip()]
        if not context:
            # 缺参明确失败路径: 返回 score=0 与明确理由 (不猜、不静默)
            return MetricResult(
                name=self.name,
                score=0.0,
                reason="无上下文，无法评估忠实度 — 需要提供 context 或 retrieval_context",
                details={"error": "missing_context"},
                threshold=self.threshold,
            )

        context_str = "\n---\n".join(str(c) for c in context)
        user_prompt = f"上下文:\n{context_str}\n\nAgent 回答:\n{actual_output}"
        data = await self._llm_judge(self._SYSTEM_PROMPT, user_prompt)

        if "score" not in data:
            raise MetricError(f"judge response missing 'score': {data!r}")
        score = self._score_of(data)

        return MetricResult(
            name=self.name,
            score=score,
            reason=str(data.get("reason", "")),
            details={
                "claims": data.get("claims", []),
                "verdicts": data.get("verdicts", []),
                "unsupported_claims": data.get("unsupported_claims", []),
            },
            threshold=self.threshold,
        )
