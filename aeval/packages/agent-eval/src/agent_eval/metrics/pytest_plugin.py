"""pytest integration — metric fixtures + suite gate (framework layer).

模块级只依赖 pytest + agent_eval 自身 (AChat 装配 eval_integration 在
runner 装配路径内延迟导入 — 纯框架使用场景不触碰 app 代码, AST import
检查仅约束 app.*)。

注册方式 (dev 期 import 注册, entry-point 打包延后至独立 repo — design D3):
根 conftest.py 中 ``pytest_plugins = ["agent_eval.metrics.pytest_plugin"]``

Fixtures:
    eval_metrics      — P0 指标注册表 (name → SyncMetric)
    answer_relevancy / faithfulness / context_recall / context_precision — 单指标
    eval_runner       — EvalRunner (默认经 eval_integration 装配, 未配置时
                        skip 并提示; 可用 set_eval_runner_factory 覆盖装配)

每个指标 fixture 值为 SyncMetric 同步包装: 同步测试直接调用
``measure(...)`` (内部 asyncio.run, 零事件循环管理); 异步测试取
``.async_metric`` 直接 await measure (不与 pytest-asyncio 抢 loop)。
指标默认未注入 llm_fn — 缺配置时报错信息可读 (LLMNotConfiguredError),
测试可经 ``fixture.async_metric.llm_fn = stub`` 注入。

Suite 门禁:
    pytest --eval-suite=suite.yaml --eval-threshold=0.7

常规测试循环结束后执行 suite 评测, terminal summary 打印 pass@k 与阈值
判定; pass@1 低于阈值时置 session.testsfailed (退出码非 0)。
评测 runner 装配与 eval_runner fixture 同源 (set_eval_runner_factory 可覆盖)。
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable

import pytest

from agent_eval.core.types import RunResult
from agent_eval.metrics import (
    AnswerRelevancyMetric,
    ContextPrecisionMetric,
    ContextRecallMetric,
    FaithfulnessMetric,
    build_default_metrics_registry,
)
from agent_eval.metrics.base import Metric, MetricResult


# ─── 同步包装 ────────────────────────────────────────────────────────────────


class SyncMetric:
    """Metric 同步包装 — measure 内部 asyncio.run, 供纯同步 pytest 测试调用。

    用法边界: 仅限无 running event loop 的同步测试 (notebook 等已有 loop
    的环境不适用); 异步测试取 .async_metric 直接 await measure。
    """

    def __init__(self, metric: Metric):
        self._metric = metric

    @property
    def async_metric(self) -> Metric:
        """原始异步 Metric (异步测试直接 await 其 measure)"""
        return self._metric

    @property
    def name(self) -> str:
        return self._metric.name

    @property
    def threshold(self) -> float:
        return self._metric.threshold

    def measure(
        self,
        input: str,
        actual_output: str,
        expected_output: str | None = None,
        context: list[str] | None = None,
        retrieval_context: list[str] | None = None,
    ) -> MetricResult:
        """同步 measure (内部 asyncio.run 桥接异步 Metric.measure)。"""
        return asyncio.run(
            self._metric.measure(
                input=input,
                actual_output=actual_output,
                expected_output=expected_output,
                context=context,
                retrieval_context=retrieval_context,
            )
        )


def _sync(metric: Metric) -> SyncMetric:
    return SyncMetric(metric)


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def eval_metrics() -> dict[str, SyncMetric]:
    """P0 指标注册表 (name → SyncMetric; 原始异步对象经 .async_metric)。"""
    return {name: _sync(m) for name, m in build_default_metrics_registry().items()}


@pytest.fixture
def answer_relevancy() -> SyncMetric:
    """AnswerRelevancyMetric 同步包装 (llm_fn 未注入 — 按需注入 stub/凭证)。"""
    return _sync(AnswerRelevancyMetric())


@pytest.fixture
def faithfulness() -> SyncMetric:
    """FaithfulnessMetric 同步包装。"""
    return _sync(FaithfulnessMetric())


@pytest.fixture
def context_recall() -> SyncMetric:
    """ContextRecallMetric 同步包装。"""
    return _sync(ContextRecallMetric())


@pytest.fixture
def context_precision() -> SyncMetric:
    """ContextPrecisionMetric 同步包装。"""
    return _sync(ContextPrecisionMetric())


# ─── Runner 装配 (eval_runner fixture 与 suite 门禁共用) ─────────────────────


class EvalRunnerUnavailableError(Exception):
    """Aeval runner 无法装配 (eval_integration 不可用或配置缺失)。"""


# 同步返回 runner, 或返回 awaitable (async 工厂)
EvalRunnerFactory = Callable[[], Any]

_eval_runner_factory: EvalRunnerFactory | None = None


def set_eval_runner_factory(factory: EvalRunnerFactory | None) -> None:
    """注入 runner 装配工厂 (测试/宿主覆盖装配点, 如 MockRunner 演示)。"""
    global _eval_runner_factory
    _eval_runner_factory = factory


async def build_eval_runner():
    """装配 EvalRunner: 注入工厂优先; 默认经 eval_integration (AChat 接入层)。

    Raises:
        EvalRunnerUnavailableError: 装配点不可用 (错误信息可读;
            eval_runner fixture 据此 skip, suite 门禁据此判门禁失败)
    """
    if _eval_runner_factory is not None:
        result = _eval_runner_factory()
        if inspect.isawaitable(result):
            result = await result
        return result

    # 延迟导入: 框架独立使用场景不依赖 AChat 接入层
    try:
        from eval_integration.config import create_aeval_runner
    except Exception as e:
        raise EvalRunnerUnavailableError(
            "eval_integration unavailable — cannot assemble an Aeval runner "
            "(standalone usage: register one via set_eval_runner_factory): "
            f"{e}"
        ) from e
    try:
        return await create_aeval_runner()
    except Exception as e:
        raise EvalRunnerUnavailableError(
            "Aeval runner assembly failed (check EVAL_AGENT_ID / EVAL_API_BASE / "
            f"token settings): {e}"
        ) from e


@pytest.fixture
def eval_runner():
    """EvalRunner 实例 (默认经 eval_integration 装配; 未配置时 skip 并提示)。"""
    try:
        return asyncio.run(build_eval_runner())
    except EvalRunnerUnavailableError as e:
        pytest.skip(f"eval_runner unavailable — skipping: {e}")


# ─── Suite 门禁 (--eval-suite / --eval-threshold) ────────────────────────────


class SuiteGatePlugin:
    """--eval-suite 门禁: 测试循环结束后跑 suite, 打印 pass@k 并按阈值置失败。

    阈值判定在 pytest_runtestloop wrapper 的 finally 中递增
    session.testsfailed — 早于 _main 的退出码计算 (terminal summary 阶段
    已无法影响退出码); 打印则放在 pytest_terminal_summary。
    """

    def __init__(self, suite_path: str, threshold: float):
        self.suite_path = suite_path
        self.threshold = threshold
        self.suite_name: str | None = None
        self.result: RunResult | None = None
        self.error: str | None = None
        self.failed = False

    @pytest.hookimpl(wrapper=True)
    def pytest_runtestloop(self, session):
        try:
            return (yield)
        finally:
            self._apply_gate(session)

    def _apply_gate(self, session) -> None:
        if session.config.option.collectonly:
            return
        try:
            self.result = asyncio.run(self._run_suite())
        except Exception as e:
            self.error = str(e)
        # 阈值判定: 分数低于阈值或评测本身出错都判门禁失败 (CI 语义 —
        # 装配失败静默放行比失败更危险)
        if self.error is not None or self._pass_at_1() < self.threshold:
            self.failed = True
            session.testsfailed += 1

    async def _run_suite(self):
        from agent_eval.core.suite import load_suite

        suite = load_suite(self.suite_path)
        self.suite_name = suite.name
        runner = await build_eval_runner()
        return await runner.run_suite(suite)

    def _pass_at_1(self) -> float:
        if self.result is None or self.result.summary is None:
            return 0.0
        return float(self.result.summary.pass_at_k.get(1, 0.0))

    def pytest_terminal_summary(self, terminalreporter, exitstatus) -> None:
        terminalreporter.write_sep("=", "AEVAL EVALUATION GATE")
        suite_label = f"{self.suite_name} ({self.suite_path})" if self.suite_name else self.suite_path
        terminalreporter.write_line(
            f"Suite: {suite_label} (threshold: {self.threshold:.4f})"
        )

        if self.error is not None:
            terminalreporter.write_line(f"GATE ERROR: {self.error}")
            return

        result = self.result
        if result is None:
            terminalreporter.write_line("GATE ERROR: evaluation produced no result")
            return

        summary = result.summary
        terminalreporter.write_line(
            f"Run {result.run_id} status={result.status} "
            f"tasks={summary.total_tasks if summary else 0} "
            f"trials={summary.total_trials if summary else 0}"
        )
        pass_at_k = summary.pass_at_k if summary else {}
        terminalreporter.write_line(
            "Pass@k: "
            + ", ".join(f"@{k}={v:.4f}" for k, v in sorted(pass_at_k.items()))
        )
        pass1 = self._pass_at_1()
        if self.failed:
            terminalreporter.write_line(
                f"GATE FAILED: pass@1 {pass1:.4f} < threshold {self.threshold:.4f}"
            )
        else:
            terminalreporter.write_line(
                f"GATE PASSED: pass@1 {pass1:.4f} >= threshold {self.threshold:.4f}"
            )


_gate_plugin: SuiteGatePlugin | None = None


def pytest_addoption(parser) -> None:
    group = parser.getgroup("aeval", "Aeval evaluation framework")
    group.addoption(
        "--eval-suite",
        action="store",
        default=None,
        help="Aeval 评测 Suite YAML 路径 (常规测试跑完后执行 suite 门禁)",
    )
    group.addoption(
        "--eval-threshold",
        action="store",
        type=float,
        default=0.7,
        help="门禁通过阈值 (pass@1, 默认 0.7)",
    )


def pytest_configure(config) -> None:
    global _gate_plugin
    suite_path = config.getoption("eval_suite")
    if suite_path:
        _gate_plugin = SuiteGatePlugin(
            suite_path, float(config.getoption("eval_threshold"))
        )
        config.pluginmanager.register(_gate_plugin)
