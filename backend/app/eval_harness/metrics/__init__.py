"""LLM output quality metrics for the Aeval evaluation framework.

Modules:
    base          — MetricResult / Metric ABC / BaseLLMMetric / to_grader()
    llm_judge     — LLMFn protocol + tolerant JSON parsing + retry
    answer_relevancy / faithfulness / context_recall / context_precision — P0 metrics
    synthetic_data — Golden + SyntheticDataGenerator (documents → dataset items)

LLM functions are injected as protocols (async (system, user) -> str);
no LLM SDK is bound in the framework core.
"""

from eval_harness.metrics.answer_relevancy import AnswerRelevancyMetric
from eval_harness.metrics.base import (
    BaseLLMMetric,
    Metric,
    MetricError,
    MetricGraderAdapter,
    MetricResult,
)
from eval_harness.metrics.context_precision import ContextPrecisionMetric
from eval_harness.metrics.context_recall import ContextRecallMetric
from eval_harness.metrics.faithfulness import FaithfulnessMetric
from eval_harness.metrics.llm_judge import (
    DEFAULT_MAX_RETRIES,
    LLMFn,
    LLMJudgeError,
    LLMNotConfiguredError,
    extract_json_object,
    judge_json,
)
from eval_harness.metrics.synthetic_data import Golden, SyntheticDataGenerator


def build_default_metrics_registry(
    llm_fn: LLMFn | None = None,
    threshold: float = 0.5,
) -> dict[str, Metric]:
    """
    构建 P0 指标注册表 (name → Metric), 供 EvalRunner(metrics_registry=...) 注入。

    llm_fn 可为 None (metric grader 届时返回明确配置错误而非崩溃)。
    """
    metrics: list[Metric] = [
        AnswerRelevancyMetric(llm_fn=llm_fn, threshold=threshold),
        FaithfulnessMetric(llm_fn=llm_fn, threshold=threshold),
        ContextRecallMetric(llm_fn=llm_fn, threshold=threshold),
        ContextPrecisionMetric(llm_fn=llm_fn, threshold=threshold),
    ]
    return {m.name: m for m in metrics}


__all__ = [
    "Metric",
    "MetricResult",
    "MetricError",
    "BaseLLMMetric",
    "MetricGraderAdapter",
    "LLMFn",
    "LLMJudgeError",
    "LLMNotConfiguredError",
    "DEFAULT_MAX_RETRIES",
    "judge_json",
    "extract_json_object",
    "AnswerRelevancyMetric",
    "FaithfulnessMetric",
    "ContextRecallMetric",
    "ContextPrecisionMetric",
    "Golden",
    "SyntheticDataGenerator",
    "build_default_metrics_registry",
]
