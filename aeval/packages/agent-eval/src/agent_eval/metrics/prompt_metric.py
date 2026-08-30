"""Prompt variant A/B — PromptMetric (纯 LLM 层, 不经 AgentRunner).

对多个 Prompt 变体: 渲染模板 (str.format(**context)) → llm_fn 生成回答 →
指标打分 → n_trials 取平均 → 声明胜者。不经过 AgentRunner — Agent 行为层的
A/B 由 Dashboard run compare 覆盖, 本模块补 Prompt 层的快速对比通道。

胜者判定 (v1 简化语义): 各指标平均分求和最大者; 结果对象保留逐 trial 明细,
为 Phase 3 显著性检验预留。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_eval.metrics.base import Metric, MetricResult
from agent_eval.metrics.llm_judge import LLMFn, require_llm_fn


class PromptTemplateError(Exception):
    """变体模板渲染失败 (str.format 缺 key / 格式错误) — 校验错误。"""


@dataclass
class PromptVariant:
    """Prompt 变体 (template 经 str.format(**context) 渲染)"""

    name: str
    template: str


@dataclass
class PromptTrialDetail:
    """单次试验明细 (Phase 3 显著性检验预留)"""

    output: str
    scores: dict[str, float] = field(default_factory=dict)  # metric name → score


@dataclass
class PromptComparisonResult:
    """变体对比结果 (含逐 trial 明细与 winner 标注)"""

    variant_name: str
    metric_scores: dict[str, float] = field(default_factory=dict)  # n_trials 平均
    trials: list[PromptTrialDetail] = field(default_factory=list)
    winner: bool = False


class PromptMetric:
    """
    Prompt A/B 对比 — 渲染 → 生成 → 打分 → n_trials 平均 → 声明胜者。

    用法:
        pm = PromptMetric(variants=[...], metrics=[...], llm_fn=llm_fn)
        results = await pm.compare(context={"question": "..."}, n_trials=3)
        winner = pm.declare_winner(results)

    胜者判定 (v1): 指标平均分求和最大 (平分取声明序首个)。
    变体模板作为 system prompt 传给 llm_fn (user 为空串, 与蓝图一致)。
    """

    def __init__(
        self,
        variants: list[PromptVariant],
        metrics: list[Metric],
        llm_fn: LLMFn | None = None,
    ):
        if not variants:
            raise ValueError("PromptMetric requires at least one variant")
        if not metrics:
            raise ValueError("PromptMetric requires at least one metric")
        self.variants = list(variants)
        self.metrics = list(metrics)
        self.llm_fn = llm_fn

    async def compare(
        self,
        context: dict[str, Any],
        n_trials: int = 3,
    ) -> list[PromptComparisonResult]:
        """
        对比所有变体 (每个变体 n_trials 次试验取平均)。

        Raises:
            LLMNotConfiguredError: 未注入 llm_fn (明确配置错误)
            PromptTemplateError: 模板渲染缺 key / 格式错误 (校验错误)
            ValueError: n_trials < 1
        """
        llm_fn = require_llm_fn(self.llm_fn)
        if n_trials < 1:
            raise ValueError(f"n_trials must be >= 1, got {n_trials}")

        results: list[PromptComparisonResult] = []
        for variant in self.variants:
            prompt = self._render(variant, context)
            trials: list[PromptTrialDetail] = []
            for _ in range(n_trials):
                output = await llm_fn(prompt, "")
                detail = PromptTrialDetail(output=output)
                for metric in self.metrics:
                    result: MetricResult = await metric.measure(
                        input=prompt,
                        actual_output=output,
                    )
                    detail.scores[metric.name] = result.score
                trials.append(detail)

            metric_scores = {
                metric.name: sum(t.scores[metric.name] for t in trials) / len(trials)
                for metric in self.metrics
            }
            results.append(PromptComparisonResult(
                variant_name=variant.name,
                metric_scores=metric_scores,
                trials=trials,
            ))

        self.declare_winner(results)
        return results

    @staticmethod
    def declare_winner(
        results: list[PromptComparisonResult],
    ) -> PromptComparisonResult:
        """声明胜者 (v1 求和语义: 指标平均分求和最大; 平分取首个)。

        原地标注: 恰有一个变体 winner=True (清除既有标注)。返回胜者。
        """
        if not results:
            raise ValueError("no comparison results to declare a winner from")
        winner = max(results, key=lambda r: sum(r.metric_scores.values()))
        for r in results:
            r.winner = r is winner
        return winner

    @staticmethod
    def _render(variant: PromptVariant, context: dict[str, Any]) -> str:
        """渲染变体模板; 缺 key / 格式错误转 PromptTemplateError (校验错误)。"""
        try:
            return variant.template.format(**context)
        except KeyError as e:
            raise PromptTemplateError(
                f"variant '{variant.name}' template missing context key: {e} "
                f"(template: {variant.template!r})"
            ) from e
        except (IndexError, ValueError) as e:
            raise PromptTemplateError(
                f"variant '{variant.name}' template render failed: {e}"
            ) from e
