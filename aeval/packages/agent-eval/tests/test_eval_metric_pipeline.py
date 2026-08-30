"""Integration tests — metric graders flow through the full scoring pipeline.

Covers the eval-harness spec deltas: registry dispatch, unknown metric name,
config-error without registry, and required/weight/cache semantics consistent
with other graders (task 4.3).
"""

import pytest
from agent_eval.core.runner import EvalRunner
from agent_eval.core.types import (
    EvalSuite,
    EvalTask,
    GraderConfig,
    GraderType,
    ScoreStrategy,
)
from agent_eval.examples.mock_runner import MockAgentRunner, MockTraceProvider
from agent_eval.metrics.base import Metric, MetricResult
from agent_eval.storage.memory import MemoryStorage


class StubMetric(Metric):
    """确定性 stub 指标 — 返回预设分数并记录 measure() 入参"""

    name = "stub_metric"
    threshold = 0.6

    def __init__(self, score: float = 0.9):
        self.score = score
        self.measure_calls: list[dict] = []

    async def measure(self, input, actual_output, expected_output=None,
                      context=None, retrieval_context=None) -> MetricResult:
        self.measure_calls.append({
            "input": input, "actual_output": actual_output,
        })
        return MetricResult(
            name=self.name,
            score=self.score,
            reason="stub reason",
            details={"input_echo": input},
            threshold=self.threshold,
        )


def metric_config(name: str = "metric", **config) -> GraderConfig:
    return GraderConfig(type=GraderType.METRIC, name=name, config=config)


def make_runner(**kwargs) -> EvalRunner:
    defaults = dict(
        agent_runner=MockAgentRunner(
            latency_range=(0.0, 0.01),
            script={"t1": ["success"], "t2": ["success"]},
        ),
        trace_provider=MockTraceProvider(),
        storage=MemoryStorage(),
    )
    defaults.update(kwargs)
    return EvalRunner(**defaults)


def make_suite(tasks: list[EvalTask]) -> EvalSuite:
    return EvalSuite(name="metric-suite", tasks=tasks)


async def run_one(runner: EvalRunner, task: EvalTask):
    result = await runner.run_suite(make_suite([task]))
    assert result.status == "completed"
    trial = result.trials[task.id][0]
    return result, trial


# ─── 注册表分发 ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_single_metric_dispatch():
    stub = StubMetric(score=0.9)
    runner = make_runner(metrics_registry={"stub_metric": stub})
    task = EvalTask(
        id="t1",
        prompt="用户问题",
        graders=[metric_config(metric_name="stub_metric")],
    )
    _, trial = await run_one(runner, task)

    result = trial.grader_results[0]
    assert result.grader_name == "metric"
    assert result.grader_type == GraderType.METRIC
    assert result.score == 0.9
    assert result.passed is True  # 0.9 >= stub threshold 0.6
    assert result.explanation == "stub reason"
    assert result.details["metric"] == "stub_metric"
    # transcript 首条作为 input 传入指标
    assert stub.measure_calls[0]["input"] == "用户问题"
    # 指标分数参与 trial 成功判定 (HYBRID, 加权达标)
    assert trial.success is True


@pytest.mark.asyncio
async def test_named_metric_configs_dispatch_per_name():
    """多个 metric 配置按名称分发, 结果各归其名 (合成数据默认双指标场景)"""
    runner = make_runner(metrics_registry={
        "relevancy": StubMetric(score=0.9),
        "faithful": StubMetric(score=0.2),
    })
    task = EvalTask(
        id="t1",
        prompt="q",
        graders=[
            metric_config(name="relevancy"),
            metric_config(name="faithful"),
        ],
    )
    _, trial = await run_one(runner, task)

    by_name = {r.grader_name: r for r in trial.grader_results}
    assert by_name["relevancy"].score == 0.9
    assert by_name["relevancy"].passed is True
    assert by_name["faithful"].score == 0.2
    assert by_name["faithful"].passed is False


# ─── 未知指标 / 未配置注册表 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_metric_name_scores_zero_with_reason():
    runner = make_runner(metrics_registry={"stub_metric": StubMetric()})
    task = EvalTask(
        id="t1", prompt="q",
        graders=[metric_config(metric_name="no_such_metric")],
    )
    _, trial = await run_one(runner, task)

    result = trial.grader_results[0]
    assert result.score == 0.0
    assert result.passed is False
    assert "未知指标" in result.explanation
    assert "no_such_metric" in result.explanation


@pytest.mark.asyncio
async def test_missing_registry_returns_config_error_result():
    runner = make_runner()  # 未注入 metrics_registry
    task = EvalTask(
        id="t1", prompt="q",
        graders=[metric_config(metric_name="stub_metric")],
    )
    _, trial = await run_one(runner, task)

    result = trial.grader_results[0]
    assert result.score == 0.0
    assert result.passed is False
    assert "未知指标" in result.explanation


@pytest.mark.asyncio
async def test_missing_metric_name_returns_config_error():
    runner = make_runner(metrics_registry={"stub_metric": StubMetric()})
    task = EvalTask(id="t1", prompt="q", graders=[metric_config()])
    _, trial = await run_one(runner, task)

    result = trial.grader_results[0]
    assert result.score == 0.0
    assert "metric_name" in result.explanation


# ─── required / weight / 缓存语义 ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_required_metric_failure_fails_trial():
    runner = make_runner(metrics_registry={"stub_metric": StubMetric(score=0.1)})
    task = EvalTask(
        id="t1", prompt="q",
        graders=[metric_config(metric_name="stub_metric", required=True)],
        score_strategy=ScoreStrategy.HYBRID,
    )
    _, trial = await run_one(runner, task)
    assert trial.success is False


@pytest.mark.asyncio
async def test_metric_weight_counts_in_weighted_score():
    # 0.2 的指标权重 1, 1.0 的 code grader (自动通过) 权重 1 → 加权 0.6 达标
    runner = make_runner(metrics_registry={"stub_metric": StubMetric(score=0.2)})
    task = EvalTask(
        id="t1", prompt="q",
        graders=[
            metric_config(metric_name="stub_metric", weight=1.0),
            GraderConfig(type=GraderType.CODE, name="code_based", weight=1.0),
        ],
        score_strategy=ScoreStrategy.WEIGHTED,
        score_threshold=0.6,
    )
    _, trial = await run_one(runner, task)
    scores = {r.grader_name: r.score for r in trial.grader_results}
    assert scores["metric"] == 0.2
    assert scores["code_based"] == 1.0
    assert trial.success is True  # (0.2*1 + 1.0*1)/2 = 0.6 >= 0.6

    # 阈值抬高到 0.7 → 不达标
    task2 = task.model_copy(update={"score_threshold": 0.7, "id": "t2"})
    _, trial2 = await run_one(runner, task2)
    assert trial2.success is False


@pytest.mark.asyncio
async def test_metric_result_cached_by_content():
    stub = StubMetric(score=0.9)
    # 确定性 agent: transcript/outcome 完全一致才能命中内容寻址缓存
    # (MockAgentRunner 每次生成随机 artifact id, 不适合本测试)
    class DeterministicAgent:
        async def run(self, task):
            transcript = [
                {"role": "user", "content": task.prompt},
                {"role": "assistant", "content": "fixed answer"},
            ]
            return "trace_fixed", transcript, {"success": True}

    runner = EvalRunner(
        agent_runner=DeterministicAgent(),
        trace_provider=MockTraceProvider(),
        storage=MemoryStorage(),
        metrics_registry={"stub_metric": stub},
        enable_grader_cache=True,
    )
    task = EvalTask(
        id="t1",
        prompt="q",
        graders=[metric_config(metric_name="stub_metric")],
        max_trials=2,
    )
    result = await runner.run_suite(make_suite([task]))
    assert result.status == "completed"
    first, second = result.trials[task.id]

    assert "cached" not in first.grader_results[0].details
    assert second.grader_results[0].details["cached"] is True
    assert second.grader_results[0].score == 0.9
    # 同一 runner 内同内容 trial 只真正计算一次
    assert len(stub.measure_calls) == 1


@pytest.mark.asyncio
async def test_llm_fn_injected_into_unconfigured_metrics():
    """runner.llm_fn 注入注册表中未配置 llm_fn 的指标"""

    class LLMCountingMetric(Metric):
        name = "llm_counting"
        threshold = 0.5

        def __init__(self):
            self.llm_fn = None
            self.calls = 0

        async def measure(self, input, actual_output, **kwargs) -> MetricResult:
            self.calls += 1
            assert self.llm_fn is not None  # runner 注入先于 measure
            return MetricResult(name=self.name, score=0.8, reason="ok")

    metric = LLMCountingMetric()

    async def fake_llm(system: str, user: str) -> str:
        return "llm"

    runner = make_runner(
        metrics_registry={"llm_counting": metric},
        llm_fn=fake_llm,
    )
    assert metric.llm_fn is fake_llm  # 注入发生在构造期

    task = EvalTask(
        id="t1", prompt="q",
        graders=[metric_config(metric_name="llm_counting")],
        max_trials=1,
    )
    _, trial = await run_one(runner, task)
    assert metric.calls == 1
    assert trial.grader_results[0].score == 0.8


# ─── metric + 其它 grader 混合 (依赖语义一致) ────────────────────────────────


@pytest.mark.asyncio
async def test_metric_respects_dependency_semantics():
    """metric grader 可依赖其它 grader, 依赖未通过时记 0 分跳过"""
    runner = make_runner(
        metrics_registry={"stub_metric": StubMetric(score=0.9)},
        agent_runner=MockAgentRunner(
            latency_range=(0.0, 0.01), script={"t1": ["failure"]}
        ),
    )
    task = EvalTask(
        id="t1", prompt="q",
        graders=[
            GraderConfig(
                type=GraderType.METRIC,
                name="metric",
                config={"metric_name": "stub_metric"},
                dependencies=["code_based"],
            ),
            GraderConfig(
                type=GraderType.CODE,
                name="code_based",
                config={"checks": [{"type": "contains", "value": "never-present", "target": "transcript"}]},
            ),
        ],
    )
    _, trial = await run_one(runner, task)

    by_name = {r.grader_name: r for r in trial.grader_results}
    assert by_name["code_based"].passed is False
    assert by_name["metric"].score == 0.0
    assert "依赖未满足" in by_name["metric"].explanation
