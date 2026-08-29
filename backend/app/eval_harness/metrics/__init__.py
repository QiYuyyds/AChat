"""LLM output quality metrics for the Aeval evaluation framework.

Modules:
    base          — MetricResult / Metric ABC / BaseLLMMetric / to_grader()
    llm_judge     — LLMFn protocol + tolerant JSON parsing + retry
    answer_relevancy / faithfulness / context_recall / context_precision — P0 metrics
    synthetic_data — Golden + SyntheticDataGenerator (documents → dataset items)
    batch_evaluation — BatchEvaluator (对已有输出批量打分, P1)
    prompt_metric  — PromptMetric (Prompt 变体 A/B, P1)
    report         — 批量/run 结果渲染为 Markdown/JSON 报告 (P1)
    pytest_plugin  — pytest 集成 (fixtures + suite 门禁; 只依赖 pytest, 不在
                     此处导入以保持框架可无 pytest 运行 — 用例侧按需注册)

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
from eval_harness.metrics.batch_evaluation import (
    BatchCaseResult,
    BatchEvaluationRequest,
    BatchEvaluationResult,
    BatchEvaluator,
    BatchMetricSummary,
    BatchTestCase,
    MetricScore,
    UnknownMetricsError,
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
from eval_harness.metrics.prompt_metric import (
    PromptComparisonResult,
    PromptMetric,
    PromptTemplateError,
    PromptTrialDetail,
    PromptVariant,
)
from eval_harness.metrics.report import render_batch_report, render_run_report
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
    "BatchTestCase",
    "BatchEvaluationRequest",
    "BatchEvaluationResult",
    "BatchCaseResult",
    "BatchMetricSummary",
    "MetricScore",
    "BatchEvaluator",
    "UnknownMetricsError",
    "PromptVariant",
    "PromptTrialDetail",
    "PromptComparisonResult",
    "PromptMetric",
    "PromptTemplateError",
    "render_batch_report",
    "render_run_report",
]
