"""Unit/integration tests for the EvalRunner core (tasks 3.1–3.5)."""

import logging

import pytest
from agent_eval.core.runner import EvalRunner, NoOpEnvironment
from agent_eval.core.types import (
    EvalSuite,
    EvalTask,
    GraderConfig,
    GraderResult,
    GraderType,
)
from agent_eval.examples.mock_runner import MockAgentRunner, MockTraceProvider
from agent_eval.storage.memory import MemoryStorage

FAST = (0.0, 0.01)


def make_task(task_id: str, graders: list[GraderConfig], **overrides) -> EvalTask:
    defaults = dict(id=task_id, prompt=f"prompt for {task_id}", graders=graders)
    defaults.update(overrides)
    return EvalTask(**defaults)


def code_grader(name: str = "code_based", **config) -> GraderConfig:
    return GraderConfig(type=GraderType.CODE, name=name, config=config)


def auto_pass_grader(name: str) -> GraderConfig:
    """code_based 无 checks 时自动通过"""
    return GraderConfig(type=GraderType.CODE, name=name)


def make_suite(tasks: list[EvalTask], name: str = "runner-suite") -> EvalSuite:
    return EvalSuite(name=name, tasks=tasks)


def make_runner(agent, *, graders=None, environment=None, storage=None, **kwargs) -> EvalRunner:
    return EvalRunner(
        agent_runner=agent,
        trace_provider=MockTraceProvider(),
        storage=storage or MemoryStorage(),
        environment=environment or NoOpEnvironment(),
        graders=graders or [],
        retry_base_delay=0.01,
        **kwargs,
    )


# ─── 3.1 环境泄漏检测 ─────────────────────────────────────────────────────────


class LeakEnvironment(NoOpEnvironment):
    """对指定 task 报告泄漏的环境管理器"""

    def __init__(self, dirty_tasks: set[str]):
        self.dirty_tasks = dirty_tasks
        self.current_task: str | None = None
        self.restored: list[dict] = []
        self.verified = 0

    async def setup(self, task: EvalTask) -> None:
        self.current_task = task.id

    async def snapshot(self) -> dict:
        return {"base": True}

    async def verify_clean(self, baseline: dict) -> dict:
        self.verified += 1
        if self.current_task in self.dirty_tasks:
            return {"clean": False, "differences": ["leftover.tmp"]}
        return {"clean": True, "differences": []}

    async def restore(self, baseline: dict) -> None:
        self.restored.append(baseline)


class TestLeakDetection:
    async def test_leak_warns_and_restores_without_failing_trial(self, caplog):
        agent = MockAgentRunner(success_rate=1.0, latency_range=FAST)
        env = LeakEnvironment(dirty_tasks={"t1"})
        runner = make_runner(agent, environment=env)
        suite = make_suite([make_task("t1", [auto_pass_grader("code_based")])])

        with caplog.at_level(logging.WARNING, logger="agent_eval.core.runner"):
            run = await runner.run_suite(suite)

        assert run.status == "completed"
        trial = run.trials["t1"][0]
        assert trial.success is True  # 泄漏不判 trial 失败
        assert env.verified > 0
        assert len(env.restored) >= 1
        assert "Environment leak detected" in caplog.text
        assert "leftover.tmp" in caplog.text

    async def test_clean_environment_no_restore(self, caplog):
        agent = MockAgentRunner(success_rate=1.0, latency_range=FAST)
        env = LeakEnvironment(dirty_tasks=set())
        runner = make_runner(agent, environment=env)
        suite = make_suite([make_task("t1", [auto_pass_grader("code_based")])])

        with caplog.at_level(logging.WARNING, logger="agent_eval.core.runner"):
            await runner.run_suite(suite)

        assert env.restored == []
        assert "Environment leak detected" not in caplog.text

    async def test_leak_check_failure_does_not_break_trial(self):
        class BrokenEnv(LeakEnvironment):
            async def verify_clean(self, baseline: dict) -> dict:
                raise RuntimeError("boom")

        agent = MockAgentRunner(success_rate=1.0, latency_range=FAST)
        runner = make_runner(agent, environment=BrokenEnv(dirty_tasks={"t1"}))
        suite = make_suite([make_task("t1", [auto_pass_grader("code_based")])])

        run = await runner.run_suite(suite)

        assert run.status == "completed"
        assert run.trials["t1"][0].success is True


# ─── 3.2 TransientError 重试 ─────────────────────────────────────────────────


class TestTransientRetry:
    async def test_transient_then_success_retries(self):
        agent = MockAgentRunner(
            latency_range=FAST,
            script={"t1": ["transient", "success"]},
        )
        runner = make_runner(agent, max_trial_retries=2)
        suite = make_suite(
            [make_task("t1", [auto_pass_grader("code_based")], max_trials=1)]
        )

        run = await runner.run_suite(suite)

        assert run.status == "completed"
        trial = run.trials["t1"][0]
        assert trial.success is True
        assert agent.call_counts["t1"] == 2  # 1 次重试后成功

    async def test_retries_exhausted_returns_failed_trial(self):
        agent = MockAgentRunner(
            latency_range=FAST,
            script={"t1": ["transient"]},  # 每次都瞬态错误
        )
        runner = make_runner(agent, max_trial_retries=2)
        suite = make_suite(
            [make_task("t1", [auto_pass_grader("code_based")], max_trials=1)]
        )

        run = await runner.run_suite(suite)

        trial = run.trials["t1"][0]
        assert trial.success is False
        assert "TransientError after 2 retries" in trial.error
        assert agent.call_counts["t1"] == 3  # 1 + 2 retries
        assert run.status == "completed"  # suite 不中断

    async def test_suite_continues_after_exhausted_retries(self):
        agent = MockAgentRunner(
            latency_range=FAST,
            script={"t_bad": ["transient"], "t_ok": ["success"]},
        )
        runner = make_runner(agent, max_trial_retries=1)
        suite = make_suite([
            make_task("t_bad", [auto_pass_grader("code_based")]),
            make_task("t_ok", [auto_pass_grader("code_based")]),
        ])

        run = await runner.run_suite(suite)

        assert run.status == "completed"
        assert run.trials["t_bad"][0].success is False
        assert run.trials["t_ok"][0].success is True

    async def test_timeout_is_not_retried(self):
        agent = MockAgentRunner(
            latency_range=FAST,
            script={"t1": ["timeout"]},
            timeout_duration=2.0,
        )
        runner = make_runner(agent, per_trial_timeout=0.05)
        suite = make_suite(
            [make_task("t1", [auto_pass_grader("code_based")], max_trials=1)]
        )

        run = await runner.run_suite(suite)

        trial = run.trials["t1"][0]
        assert trial.success is False
        assert "timed out" in trial.error
        assert agent.call_counts["t1"] == 1  # 不重试


# ─── 3.3 Grader Pipeline (依赖拓扑 / 超时) ────────────────────────────────────


class AlwaysPassGrader:
    name = "always_pass"

    async def grade(self, trial, spans, task, context=None) -> GraderResult:
        return GraderResult(
            grader_name=self.name,
            grader_type=GraderType.CODE,
            score=1.0,
            passed=True,
            explanation="pass",
        )


class SlowGrader:
    name = "slow_grader"

    def __init__(self, delay: float = 1.0):
        self.delay = delay

    async def grade(self, trial, spans, task, context=None) -> GraderResult:
        import asyncio

        await asyncio.sleep(self.delay)
        return GraderResult(
            grader_name=self.name,
            grader_type=GraderType.CODE,
            score=1.0,
            passed=True,
            explanation="slow pass",
        )


class TestGraderPipeline:
    async def test_dependency_not_passed_skips_with_zero(self):
        agent = MockAgentRunner(
            latency_range=FAST,
            script={"t1": ["failure"]},  # outcome 无 artifacts → check 失败
        )
        dependent = GraderConfig(
            type=GraderType.CODE,
            name="always_pass",
            dependencies=["code_based"],  # 声明在前, 依赖在后 → 验证拓扑重排
        )
        runner = make_runner(agent, graders=[AlwaysPassGrader()])
        suite = make_suite([
            make_task("t1", [dependent, code_grader(
                "code_based",
                checks=[{"type": "contains", "value": "art_", "target": "outcome"}],
            )]),
        ])

        run = await runner.run_suite(suite)

        results = {r.grader_name: r for r in run.trials["t1"][0].grader_results}
        assert results["code_based"].passed is False
        assert results["always_pass"].score == 0.0
        assert "依赖未满足" in results["always_pass"].explanation
        assert "code_based" in results["always_pass"].explanation

    async def test_dependency_satisfied_runs(self):
        agent = MockAgentRunner(success_rate=1.0, latency_range=FAST)
        dependent = GraderConfig(
            type=GraderType.CODE, name="always_pass", dependencies=["code_based"]
        )
        runner = make_runner(agent, graders=[AlwaysPassGrader()])
        suite = make_suite([make_task("t1", [dependent, auto_pass_grader("code_based")])])

        run = await runner.run_suite(suite)

        results = {r.grader_name: r for r in run.trials["t1"][0].grader_results}
        assert results["always_pass"].score == 1.0

    async def test_unconfigured_dependency_skips(self):
        agent = MockAgentRunner(success_rate=1.0, latency_range=FAST)
        dependent = GraderConfig(
            type=GraderType.CODE,
            name="always_pass",
            dependencies=["not_in_task"],
        )
        runner = make_runner(agent, graders=[AlwaysPassGrader()])
        suite = make_suite([make_task("t1", [dependent])])

        run = await runner.run_suite(suite)

        result = run.trials["t1"][0].grader_results[0]
        assert result.score == 0.0
        assert "依赖未满足" in result.explanation

    async def test_grader_timeout_scores_zero(self):
        agent = MockAgentRunner(success_rate=1.0, latency_range=FAST)
        runner = make_runner(agent, graders=[SlowGrader(delay=1.0)], grader_timeout=0.05)
        suite = make_suite([make_task("t1", [GraderConfig(type=GraderType.CODE, name="slow_grader")])])

        run = await runner.run_suite(suite)

        result = run.trials["t1"][0].grader_results[0]
        assert result.score == 0.0
        assert result.passed is False
        assert "timeout" in result.explanation

    async def test_unknown_grader_scores_zero(self):
        agent = MockAgentRunner(success_rate=1.0, latency_range=FAST)
        runner = make_runner(agent)
        suite = make_suite([make_task("t1", [GraderConfig(type=GraderType.CODE, name="nope")])])

        run = await runner.run_suite(suite)

        result = run.trials["t1"][0].grader_results[0]
        assert result.score == 0.0
        assert "Unknown grader" in result.explanation


# ─── 3.4 多采样与缓存 ─────────────────────────────────────────────────────────


class SequencedModelGrader:
    """每次调用返回序列中的下一个分数 (LLM Judge 模拟)"""

    name = "model_based"

    def __init__(self, scores: list[float]):
        self.scores = list(scores)
        self.calls = 0

    async def grade(self, trial, spans, task, context=None) -> GraderResult:
        score = self.scores[min(self.calls, len(self.scores) - 1)]
        self.calls += 1
        return GraderResult(
            grader_name=self.name,
            grader_type=GraderType.MODEL,
            score=score,
            passed=score >= 0.7,
            explanation=f"sample {self.calls}",
        )


class FixedAgent:
    """每次返回完全相同结果的 AgentRunner (用于缓存命中测试)"""

    async def run(self, task: EvalTask):
        return (
            "trace_fixed",
            [{"role": "user", "content": task.prompt}, {"role": "assistant", "content": "fixed"}],
            {"success": False, "files": {}, "artifacts": []},
        )


class CountingGrader:
    name = "counting"

    def __init__(self):
        self.calls = 0

    async def grade(self, trial, spans, task, context=None) -> GraderResult:
        self.calls += 1
        return GraderResult(
            grader_name=self.name,
            grader_type=GraderType.CODE,
            score=0.9,
            passed=True,
            explanation="counted",
        )


class TestMultiSampleAndCache:
    async def test_multi_sample_aggregates_scores(self):
        agent = MockAgentRunner(success_rate=1.0, latency_range=FAST)
        grader = SequencedModelGrader([0.2, 0.6, 1.0])
        config = GraderConfig(
            type=GraderType.MODEL, name="model_based", sample_count=3
        )
        runner = make_runner(agent, graders=[grader])
        suite = make_suite([make_task("t1", [config], max_trials=1)])

        run = await runner.run_suite(suite)

        result = run.trials["t1"][0].grader_results[0]
        assert result.score == pytest.approx(0.6)
        assert result.uncertainty == pytest.approx(0.4)  # 极差/2 = (1.0-0.2)/2
        assert result.confidence == pytest.approx(0.6)  # 1 - uncertainty
        assert result.sample_count == 3
        assert result.details["sample_scores"] == [0.2, 0.6, 1.0]
        assert grader.calls == 3

    async def test_multi_sample_majority_vote_passed(self):
        agent = MockAgentRunner(success_rate=1.0, latency_range=FAST)
        grader = SequencedModelGrader([0.9, 0.9, 0.1])  # 2/3 passed
        config = GraderConfig(type=GraderType.MODEL, name="model_based", sample_count=3)
        runner = make_runner(agent, graders=[grader])
        suite = make_suite([make_task("t1", [config])])

        run = await runner.run_suite(suite)

        result = run.trials["t1"][0].grader_results[0]
        assert result.passed is True

    async def test_cache_hits_across_identical_trials(self):
        agent = FixedAgent()
        grader = CountingGrader()
        runner = make_runner(
            agent, graders=[grader], enable_grader_cache=True, verify_environment=False
        )
        suite = make_suite([make_task("t1", [GraderConfig(type=GraderType.CODE, name="counting")], max_trials=3)])

        await runner.run_suite(suite)

        assert grader.calls == 1  # 3 个相同 trial 只评一次

    async def test_cache_disabled(self):
        agent = FixedAgent()
        grader = CountingGrader()
        runner = make_runner(
            agent, graders=[grader], enable_grader_cache=False, verify_environment=False
        )
        suite = make_suite([make_task("t1", [GraderConfig(type=GraderType.CODE, name="counting")], max_trials=2)])

        await runner.run_suite(suite)

        assert grader.calls == 2

    async def test_multi_sample_bypasses_cache(self):
        agent = FixedAgent()
        grader = SequencedModelGrader([0.5, 0.5, 0.5, 0.5, 0.5, 0.5])
        config = GraderConfig(type=GraderType.MODEL, name="model_based", sample_count=3)
        runner = make_runner(
            agent, graders=[grader], enable_grader_cache=True, verify_environment=False
        )
        suite = make_suite([make_task("t1", [config], max_trials=2)])

        await runner.run_suite(suite)

        # 多采样绕过缓存: 每个 trial 都完整采样 (2 trials × 3 samples)
        assert grader.calls == 6


# ─── 3.5 EvalContext 贯穿 ─────────────────────────────────────────────────────


class StateWriterGrader:
    name = "state_writer"

    async def grade(self, trial, spans, task, context=None) -> GraderResult:
        context.shared_state["writer_ran"] = True
        return GraderResult(
            grader_name=self.name, grader_type=GraderType.CODE,
            score=1.0, passed=True, explanation="ok",
        )


class StateReaderGrader:
    name = "state_reader"

    async def grade(self, trial, spans, task, context=None) -> GraderResult:
        return GraderResult(
            grader_name=self.name, grader_type=GraderType.CODE,
            score=1.0, passed=True, explanation="ok",
            details={
                "writer_ran": context.shared_state.get("writer_ran", False),
                "run_id": context.run_id,
            },
        )


class TestEvalContext:
    async def test_shared_state_flows_between_graders(self):
        agent = MockAgentRunner(success_rate=1.0, latency_range=FAST)
        runner = make_runner(agent, graders=[StateWriterGrader(), StateReaderGrader()])
        suite = make_suite([
            make_task("t1", [
                GraderConfig(
                    type=GraderType.CODE,
                    name="state_reader",
                    dependencies=["state_writer"],
                ),
                GraderConfig(type=GraderType.CODE, name="state_writer"),
            ]),
        ])

        run = await runner.run_suite(suite)

        reader = next(
            r for r in run.trials["t1"][0].grader_results if r.grader_name == "state_reader"
        )
        assert reader.details["writer_ran"] is True
        assert reader.details["run_id"] == run.run_id


# ─── 统计聚合 (一致性 / 饱和度 / pending) ─────────────────────────────────────


class TestSummaryStats:
    async def test_all_pass_triggers_saturation(self):
        agent = MockAgentRunner(success_rate=1.0, latency_range=FAST)
        runner = make_runner(agent)
        suite = make_suite([
            make_task(f"t{i}", [auto_pass_grader("code_based")]) for i in range(3)
        ])

        run = await runner.run_suite(suite)

        assert run.summary is not None
        sat = run.summary.saturation
        assert sat["is_saturated"] is True
        assert sat["saturation_ratio"] == 1.0
        assert "更有挑战性" in sat["recommendation"]

    async def test_consistency_flags_unstable_trials(self):
        agent = MockAgentRunner(success_rate=1.0, latency_range=FAST)
        grader = SequencedModelGrader([1.0, 0.0, 0.0])  # trial 间分数剧烈波动

        class FrozenSequencedGrader(SequencedModelGrader):
            # 每个 trial 固定消耗一个分数: max_trials=3 → 3 个分数
            pass

        config = GraderConfig(type=GraderType.MODEL, name="model_based", sample_count=1)
        runner = make_runner(agent, graders=[grader], enable_grader_cache=False)
        suite = make_suite([make_task("t1", [config], max_trials=3)])

        run = await runner.run_suite(suite)

        ts = run.summary.task_summaries[0]
        assert ts.score_std_dev > 0.2
        assert ts.consistent is False

    async def test_pending_trials_excluded_from_pass_rate(self):
        agent = MockAgentRunner(success_rate=1.0, latency_range=FAST)
        runner = make_runner(agent)

        # t_pending: 全部 trial pending; t_ok: 正常通过
        pending_graders = [
            GraderConfig(type=GraderType.CUSTOM, name="human"),
            auto_pass_grader("code_based"),
        ]
        suite = make_suite([
            make_task("t_pending", pending_graders, max_trials=2),
            make_task("t_ok", [auto_pass_grader("code_based")]),
        ])

        run = await runner.run_suite(suite)

        summary = run.summary
        pending_ts = next(ts for ts in summary.task_summaries if ts.task_id == "t_pending")
        ok_ts = next(ts for ts in summary.task_summaries if ts.task_id == "t_ok")

        assert pending_ts.pending_trials == [0, 1]
        assert pending_ts.failures == []  # pending 不算失败
        assert pending_ts.pass_at_k[1] == 0.0  # 无可计 trial
        assert ok_ts.pass_at_k[1] == 1.0

        # 人工评分请求已写入 storage
        requests = await runner.storage.list_human_score_requests()
        assert len(requests) == 2
        assert requests[0]["task_id"] == "t_pending"
