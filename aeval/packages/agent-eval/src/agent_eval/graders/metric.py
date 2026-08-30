"""
Metric grader — the built-in dispatcher from grader configs to Metric instances.

Task grader configs with `type: metric` route through this grader (D1):
- `name: metric` + `config.metric_name` (single dispatcher per task), or
- `name: <metric_name>` + `type: metric` (several metrics per task — the
  runner falls back to this dispatcher for unregistered metric-type configs)

The metric registry and LLM function are injected by EvalRunner
(metrics_registry=..., llm_fn=...). Unregistered metric names score 0 with
an explicit reason; missing LLM configuration surfaces a clear config error
result instead of crashing the run.
"""

from __future__ import annotations

from typing import Any

from agent_eval.core.contract import EvalContext
from agent_eval.core.types import (
    EvalTask,
    GraderConfig,
    GraderResult,
    GraderType,
    TrialResult,
)
from agent_eval.metrics.base import Metric, MetricError
from agent_eval.metrics.llm_judge import LLMFn, LLMJudgeError, LLMNotConfiguredError

# 计算失败 (配置/解析/调用) 映射为 0 分结果, 不 crash run (D2)
_CALC_ERRORS = (LLMNotConfiguredError, LLMJudgeError, MetricError)


class MetricGrader:
    """按 config.metric_name 从注入注册表分发到对应 Metric 计算"""

    name = "metric"

    def __init__(
        self,
        metrics_registry: dict[str, Metric] | None = None,
        llm_fn: LLMFn | None = None,
    ):
        # 由 EvalRunner 组合根覆盖注入 (与 storage 注入同一模式)
        self.metrics_registry: dict[str, Metric] = dict(metrics_registry or {})
        self.llm_fn = llm_fn

    async def grade(
        self,
        trial: TrialResult,
        spans: list[dict[str, Any]],
        task: EvalTask,
        context: EvalContext | None = None,
    ) -> GraderResult:
        config = self._active_config(task, context)
        metric_name = self._metric_name(config)

        if not metric_name:
            return self._result(config, 0.0, False, "metric grader 缺少 config.metric_name")

        metric = self.metrics_registry.get(metric_name)
        if metric is None:
            return self._result(
                config, 0.0, False, f"未知指标: {metric_name} (未在 metrics_registry 注册)"
            )

        kwargs = self._measure_kwargs(config, trial)
        try:
            result = await metric.measure(**kwargs)
        except _CALC_ERRORS as e:
            return self._result(config, 0.0, False, f"配置/计算错误: {e}")
        except Exception as e:  # noqa: BLE001 — 指标实现方错误同样不 crash run
            return self._result(config, 0.0, False, f"指标计算异常: {e}")

        threshold = float(config.config.get("threshold", metric.threshold))
        return self._result(
            config,
            result.score,
            result.score >= threshold,
            result.reason,
            details={
                **result.details,
                "metric": result.name,
                "metric_threshold": result.threshold,
                "grader_threshold": threshold,
            },
        )

    # ── Config resolution ────────────────────────────────────────────────

    @staticmethod
    def _active_config(task: EvalTask, context: EvalContext | None) -> GraderConfig:
        """当前生效的 metric 配置: runner 传入优先, 否则取名为 metric 的配置"""
        if context is not None and context.grader_config is not None:
            return context.grader_config
        for g in task.graders:
            if g.type == GraderType.METRIC:
                return g
        raise ValueError(f"task '{task.id}' has no metric grader config")

    @staticmethod
    def _metric_name(config: GraderConfig) -> str:
        """metric_name 显式配置优先; 命名分发型 (name=指标名) 取配置名"""
        explicit = config.config.get("metric_name")
        if explicit:
            return str(explicit)
        if config.name != MetricGrader.name:
            return config.name
        return ""

    @staticmethod
    def _measure_kwargs(config: GraderConfig, trial: TrialResult) -> dict[str, Any]:
        """从 trial transcript 与 grader config 提取 measure() 入参"""
        first = trial.transcript[0] if trial.transcript else {}
        last = trial.transcript[-1] if trial.transcript else {}
        prompt = first.get("content", "") if isinstance(first, dict) else ""
        output = last.get("content", "") if isinstance(last, dict) else ""

        return {
            "input": prompt,
            "actual_output": output,
            "expected_output": config.config.get("expected_output"),
            "context": config.config.get("context"),
            "retrieval_context": config.config.get("retrieval_context"),
        }

    @staticmethod
    def _result(
        config: GraderConfig,
        score: float,
        passed: bool,
        explanation: str,
        details: dict[str, Any] | None = None,
    ) -> GraderResult:
        return GraderResult(
            grader_name=config.name,
            grader_type=GraderType.METRIC,
            score=max(0.0, min(1.0, score)),
            passed=passed,
            explanation=explanation,
            details=details or {},
        )
