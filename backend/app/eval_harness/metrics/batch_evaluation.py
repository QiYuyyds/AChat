"""Batch evaluation over pre-existing outputs — BatchEvaluator + request/result models.

对**已有输出** (历史对话 / 日志, 非运行 Agent 产生) 的用例集批量计算指标:
输入 (input/actual_output, 可选 expected_output/context/retrieval_context) 与
指标名列表, 输出逐条结果 + 汇总 (各指标 avg/min/max、pass/fail 计数、pass_rate)。

- 指标名解析前置: 任一未注册抛 UnknownMetricsError (携带无效名列表), 未发任何 LLM 调用
- 单条异常隔离: 单条用例内指标报错记入该条 result, 不中断整批
- 受限并发: asyncio.Semaphore (默认 4)
- LLM 函数沿用注入约定 (LLMFn); 未注入走 require_llm_fn 明确配置错误, 非静默 0 分
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field

from eval_harness.metrics.base import Metric
from eval_harness.metrics.llm_judge import LLMFn, require_llm_fn

DEFAULT_CONCURRENCY = 4


class UnknownMetricsError(Exception):
    """请求中的指标名未注册 (携带无效名列表; 解析前置, 零 LLM 调用)。"""

    def __init__(self, unknown: list[str]):
        self.unknown = list(unknown)
        super().__init__(f"Unknown metrics: {', '.join(self.unknown)}")


class BatchTestCase(BaseModel):
    """单条待评用例 — 已有输出 (非运行 Agent 产生)"""

    input: str
    actual_output: str
    expected_output: str | None = None
    context: list[str] | None = None
    retrieval_context: list[str] | None = None


class BatchEvaluationRequest(BaseModel):
    """批量评测请求 (与 REST POST /api/eval/metrics/batch 请求体同构)"""

    test_cases: list[BatchTestCase] = Field(default_factory=list)
    metrics: list[str] = Field(
        ..., min_length=1, description="要计算的指标名 (须经注册表解析)"
    )
    thresholds: dict[str, float] = Field(
        default_factory=dict,
        description="逐指标覆盖 Metric 默认阈值 (显式给出的优先生效)",
    )


class MetricScore(BaseModel):
    """单条用例上单指标的结果 (异常隔离: error 非空时 score=0)"""

    name: str
    score: float
    reason: str = ""
    threshold: float = 0.5  # 实际使用的阈值 (thresholds 覆盖后)
    success: bool = False
    error: str | None = None


class BatchCaseResult(BaseModel):
    """单条用例的逐指标结果"""

    index: int
    input: str
    scores: dict[str, MetricScore] = Field(default_factory=dict)
    overall_pass: bool = False


class BatchMetricSummary(BaseModel):
    """单指标汇总 (threshold 记录实际使用的阈值)"""

    avg: float = 0.0
    min: float = 0.0
    max: float = 0.0
    pass_count: int = 0
    fail_count: int = 0
    pass_rate: float = 0.0
    threshold: float = 0.5


class BatchEvaluationResult(BaseModel):
    """批量评测结果"""

    results: list[BatchCaseResult] = Field(default_factory=list)
    summary: dict[str, BatchMetricSummary] = Field(default_factory=dict)
    pass_count: int = 0
    fail_count: int = 0
    pass_rate: float = 0.0


class BatchEvaluator:
    """
    批量评测器 — 对已有输出用例集计算指标 (受限并发 + 单条异常隔离)。

    用法:
        evaluator = BatchEvaluator(metrics_registry, llm_fn=llm_fn, concurrency=4)
        result = await evaluator.evaluate(request)

    llm_fn 注入约定与 EvalRunner 一致: 注入注册表中未自行配置 llm_fn 的指标。
    """

    def __init__(
        self,
        metrics_registry: dict[str, Metric] | None,
        llm_fn: LLMFn | None = None,
        concurrency: int = DEFAULT_CONCURRENCY,
    ):
        self.metrics_registry = dict(metrics_registry or {})
        self.llm_fn = llm_fn
        self.concurrency = max(1, concurrency)
        if self.llm_fn is not None:
            for metric in self.metrics_registry.values():
                if getattr(metric, "llm_fn", None) is None:
                    metric.llm_fn = self.llm_fn

    async def evaluate(
        self,
        request: BatchEvaluationRequest,
    ) -> BatchEvaluationResult:
        """
        执行批量评测。

        指标名解析前置 (未注册 → UnknownMetricsError, 零 LLM 调用);
        逐条逐指标计算 (Semaphore 受限并发, 单条异常记入该条 result)。
        """
        metrics = self._resolve_metrics(request.metrics)
        self._ensure_llm_configured(metrics)

        thresholds = {
            m.name: request.thresholds.get(m.name, m.threshold) for m in metrics
        }
        semaphore = asyncio.Semaphore(self.concurrency)

        async def _score(
            case_index: int,
            case: BatchTestCase,
            metric: Metric,
        ) -> tuple[int, str, MetricScore]:
            async with semaphore:
                score = await self._measure_isolated(
                    metric, thresholds[metric.name], case
                )
            return case_index, metric.name, score

        pairs = await asyncio.gather(
            *(
                _score(i, case, m)
                for i, case in enumerate(request.test_cases)
                for m in metrics
            )
        )

        by_case: list[dict[str, MetricScore]] = [{} for _ in request.test_cases]
        for case_index, metric_name, score in pairs:
            by_case[case_index][metric_name] = score

        results = [
            BatchCaseResult(
                index=i,
                input=case.input,
                scores=by_case[i],
                overall_pass=all(s.success for s in by_case[i].values()),
            )
            for i, case in enumerate(request.test_cases)
        ]

        pass_count = sum(1 for r in results if r.overall_pass)
        total = len(results)
        return BatchEvaluationResult(
            results=results,
            summary=self._compute_summary(results, thresholds),
            pass_count=pass_count,
            fail_count=total - pass_count,
            pass_rate=pass_count / total if total else 0.0,
        )

    # ── 内部 ──────────────────────────────────────────────────────────────

    def _resolve_metrics(self, names: list[str]) -> list[Metric]:
        """解析指标名 → 实例; 任一未注册即抛 UnknownMetricsError (前置)。"""
        unknown = sorted({n for n in names if n not in self.metrics_registry})
        if unknown:
            raise UnknownMetricsError(unknown)
        # 保序去重 (同名指标重复请求只算一次)
        seen: dict[str, Metric] = {}
        for name in names:
            seen.setdefault(name, self.metrics_registry[name])
        return list(seen.values())

    def _ensure_llm_configured(self, metrics: list[Metric]) -> None:
        """请求到的指标必须拿到 LLM 函数 (自身持有或注入), 否则明确配置错误。"""
        missing = [m.name for m in metrics if getattr(m, "llm_fn", None) is None]
        if not missing:
            return
        # 走 require_llm_fn 的明确配置错误语义 (而非静默 0 分)
        llm_fn = require_llm_fn(self.llm_fn)
        for name in missing:
            self.metrics_registry[name].llm_fn = llm_fn

    @staticmethod
    async def _measure_isolated(
        metric: Metric,
        threshold: float,
        case: BatchTestCase,
    ) -> MetricScore:
        """单条单指标计算; 异常记入该条结果 (score=0 + error), 不中断整批。"""
        try:
            result = await metric.measure(
                input=case.input,
                actual_output=case.actual_output,
                expected_output=case.expected_output,
                context=case.context,
                retrieval_context=case.retrieval_context,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            return MetricScore(
                name=metric.name,
                score=0.0,
                reason=f"指标计算失败: {e}",
                threshold=threshold,
                success=False,
                error=str(e),
            )
        # success 按实际使用阈值重算 (MetricResult.success 用的是指标自带阈值)
        return MetricScore(
            name=metric.name,
            score=result.score,
            reason=result.reason,
            threshold=threshold,
            success=result.score >= threshold,
        )

    @staticmethod
    def _compute_summary(
        results: list[BatchCaseResult],
        thresholds: dict[str, float],
    ) -> dict[str, BatchMetricSummary]:
        """按指标聚合 avg/min/max + pass/fail 计数 (记录实际使用阈值)。"""
        by_metric: dict[str, list[MetricScore]] = {}
        for r in results:
            for name, s in r.scores.items():
                by_metric.setdefault(name, []).append(s)

        summary: dict[str, BatchMetricSummary] = {}
        for name, scores in by_metric.items():
            values = [s.score for s in scores]
            pass_count = sum(1 for s in scores if s.success)
            total = len(scores)
            summary[name] = BatchMetricSummary(
                avg=sum(values) / total,
                min=min(values),
                max=max(values),
                pass_count=pass_count,
                fail_count=total - pass_count,
                pass_rate=pass_count / total if total else 0.0,
                threshold=thresholds.get(name, 0.5),
            )
        return summary
