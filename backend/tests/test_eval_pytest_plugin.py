"""Aeval pytest plugin integration demo — task 4.2.

Part 1 (in-process, real pytest fixtures from the plugin registered in
backend/conftest.py): 同步 fixture measure stub 化指标返回 MetricResult、
异步经 .async_metric 直接 await、注册表 fixture、缺 LLM 配置报错可读。

Part 2 (subprocess): --eval-suite 路径以 MockRunner suite 跑通 terminal
summary 与阈值门禁 (退出码断言: pass@1 达标 → 0; 低于阈值 → 非 0)。
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import eval_harness
import pytest

from eval_harness.metrics.base import MetricResult
from eval_harness.metrics.llm_judge import LLMNotConfiguredError


def _stub_llm(score: float = 0.9, reason: str = "stub ok"):
    async def llm_fn(system: str, user: str) -> str:
        return json.dumps({"score": score, "reason": reason}, ensure_ascii=False)

    return llm_fn


# ─── Part 1: fixtures (经 backend/conftest.py 注册的插件) ────────────────────


class TestFixtures:
    def test_sync_measure_with_stubbed_metric(self, answer_relevancy):
        """同步 fixture 直接 measure — 无事件循环管理, 返回 MetricResult。"""
        answer_relevancy.async_metric.llm_fn = _stub_llm(0.9)
        result = answer_relevancy.measure(
            input="什么是退款政策？", actual_output="30 天内可退货"
        )
        assert isinstance(result, MetricResult)
        assert result.name == "answer_relevancy"
        assert result.score == 0.9
        assert result.reason == "stub ok"
        assert result.success is True

    async def test_async_metric_direct_await(self, answer_relevancy):
        """异步测试取原始对象直接 await (不与 pytest-asyncio 抢 loop)。"""
        answer_relevancy.async_metric.llm_fn = _stub_llm(0.4)
        result = await answer_relevancy.async_metric.measure("q", "a")
        assert isinstance(result, MetricResult)
        assert result.score == 0.4
        assert result.success is False

    def test_eval_metrics_registry(self, eval_metrics):
        assert set(eval_metrics) == {
            "answer_relevancy",
            "faithfulness",
            "context_recall",
            "context_precision",
        }
        metric = eval_metrics["faithfulness"]
        assert metric.name == "faithfulness"
        metric.async_metric.llm_fn = _stub_llm(1.0)
        result = metric.measure(input="q", actual_output="a", context=["doc"])
        assert isinstance(result, MetricResult)
        assert result.score == 1.0

    def test_missing_llm_config_error_is_readable(self, faithfulness):
        with pytest.raises(LLMNotConfiguredError) as exc:
            faithfulness.measure("q", "a", context=["doc"])
        assert "llm_fn" in str(exc.value)
        assert "inject" in str(exc.value)  # 提示如何修复

    def test_all_four_metric_fixtures_measure(self, answer_relevancy, faithfulness,
                                              context_recall, context_precision):
        stub = _stub_llm(0.8)
        # 各指标按其语义提供必要参数 (缺参指标会走 score=0 的显式失败路径)
        cases = [
            (answer_relevancy, {}),
            (faithfulness, {"context": ["doc"]}),
            (context_recall, {"expected_output": "exp", "retrieval_context": ["doc"]}),
            (context_precision, {"retrieval_context": ["doc"]}),
        ]
        for wrapper, kwargs in cases:
            wrapper.async_metric.llm_fn = stub
            result = wrapper.measure(input="q", actual_output="a", **kwargs)
            assert isinstance(result, MetricResult)
            assert result.score == 0.8
            assert result.success is True


# ─── Part 2: --eval-suite 门禁 (MockRunner suite, 子进程真实 pytest) ─────────


DEMO_CONFTEST = """\
import pytest

pytest_plugins = ["eval_harness.metrics.pytest_plugin"]

from eval_harness.core.runner import EvalRunner
from eval_harness.examples.mock_runner import MockAgentRunner, MockTraceProvider
from eval_harness.metrics import pytest_plugin as aeval_plugin
from eval_harness.storage.memory import MemoryStorage


def _mock_factory():
    return EvalRunner(
        agent_runner=MockAgentRunner(success_rate=1.0, latency_range=(0.0, 0.01)),
        trace_provider=MockTraceProvider(),
        storage=MemoryStorage(),
    )


aeval_plugin.set_eval_runner_factory(_mock_factory)
"""

DEMO_TEST = """\
def test_smoke():
    assert True
"""

SUITE_PASS = """\
name: mock-gate-pass
description: 门禁演示 — 全部通过
version: 1.0.0
tasks:
  - id: task_ok
    prompt: "hello"
    graders:
      - type: code
        name: code_based
    max_trials: 2
"""

# code_based 含必失败检查 → trial 全失败 → pass@1 = 0.0
SUITE_FAIL = """\
name: mock-gate-fail
description: 门禁演示 — 低于阈值
version: 1.0.0
tasks:
  - id: task_bad
    prompt: "hello"
    graders:
      - type: code
        name: code_based
        config:
          checks:
            - type: contains
              value: "NEVER_PRESENT_IN_MOCK_OUTPUT"
              target: transcript
    max_trials: 1
"""


def _run_pytest(tmp_path: Path, *args: str) -> subprocess.CompletedProcess:
    """在隔离目录以子进程跑真实 pytest (PYTHONPATH 指向 backend/app)。"""
    app_dir = Path(eval_harness.__file__).resolve().parent.parent
    env = {
        **os.environ,
        "PYTHONPATH": str(app_dir) + os.pathsep + os.environ.get("PYTHONPATH", ""),
        # 隔离: 不继承主会话的插件/缓存状态
        "PYTEST_ADDOPTS": "",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
    }
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", *args],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )


def _setup_demo_dir(tmp_path: Path, suite_yaml: str) -> Path:
    (tmp_path / "conftest.py").write_text(DEMO_CONFTEST, encoding="utf-8")
    (tmp_path / "test_smoke.py").write_text(DEMO_TEST, encoding="utf-8")
    suite = tmp_path / "suite.yaml"
    suite.write_text(suite_yaml, encoding="utf-8")
    return suite


class TestSuiteGate:
    def test_gate_passes_with_adequate_score(self, tmp_path):
        suite = _setup_demo_dir(tmp_path, SUITE_PASS)
        proc = _run_pytest(
            tmp_path, "--eval-suite", str(suite), "--eval-threshold", "0.7", "."
        )

        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "AEVAL EVALUATION GATE" in proc.stdout
        assert "mock-gate-pass" in proc.stdout
        assert "Pass@k: @1=1.0000" in proc.stdout
        assert "GATE PASSED: pass@1 1.0000 >= threshold 0.7000" in proc.stdout

    def test_gate_fails_below_threshold_with_nonzero_exit(self, tmp_path):
        suite = _setup_demo_dir(tmp_path, SUITE_FAIL)
        proc = _run_pytest(
            tmp_path, "--eval-suite", str(suite), "--eval-threshold", "0.7", "."
        )

        assert proc.returncode != 0, proc.stdout
        assert "AEVAL EVALUATION GATE" in proc.stdout
        assert "GATE FAILED: pass@1 0.0000 < threshold 0.7000" in proc.stdout

    def test_gate_error_fails_loudly(self, tmp_path):
        # suite 文件不存在 → 门禁错误 → 退出码非 0 (静默放行是 CI 事故)
        _setup_demo_dir(tmp_path, SUITE_PASS)
        missing = tmp_path / "no_such_suite.yaml"
        proc = _run_pytest(
            tmp_path, "--eval-suite", str(missing), "--eval-threshold", "0.7", "."
        )

        assert proc.returncode != 0, proc.stdout
        assert "GATE ERROR" in proc.stdout
