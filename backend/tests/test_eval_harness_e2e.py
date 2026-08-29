"""End-to-end test: mini suite through MockRunner (task 6.2).

3 tasks × 3 trials covering the four framework behaviors:
- t_timeout:  trial timeout isolation (per_trial_timeout)
- t_transient: TransientError exponential-backoff retry
- t_deps:     dependency-based grader skip + environment leak detection

Asserts RunSummary pass@k / pass^k / saturation semantics.
"""

import logging

import pytest
from eval_harness.core.runner import EvalRunner, NoOpEnvironment
from eval_harness.core.types import EvalSuite, EvalTask, GraderConfig, GraderType
from eval_harness.examples.mock_runner import MockAgentRunner, MockTraceProvider
from eval_harness.storage.memory import MemoryStorage

FAST = (0.0, 0.01)


class LeakEnvironment(NoOpEnvironment):
    """t_deps 的 trial 结束后报告环境泄漏, 其余干净"""

    def __init__(self):
        self.current_task: str | None = None
        self.restore_calls = 0

    async def setup(self, task: EvalTask) -> None:
        self.current_task = task.id

    async def snapshot(self) -> dict:
        return {"base": True}

    async def verify_clean(self, baseline: dict) -> dict:
        if self.current_task == "t_deps":
            return {"clean": False, "differences": ["leftover.tmp"]}
        return {"clean": True, "differences": []}

    async def restore(self, baseline: dict) -> None:
        self.restore_calls += 1


def _grader(name: str, **config) -> GraderConfig:
    return GraderConfig(type=GraderType.CODE, name=name, config=config)


def build_mini_suite() -> EvalSuite:
    return EvalSuite(
        name="mini-e2e",
        tasks=[
            EvalTask(
                id="t_timeout",
                prompt="hang forever",
                graders=[_grader("code_based")],
                max_trials=3,
            ),
            EvalTask(
                id="t_transient",
                prompt="flaky network",
                graders=[_grader("code_based")],
                max_trials=3,
            ),
            EvalTask(
                id="t_deps",
                prompt="leaky task",
                graders=[
                    # 依赖的 code_based 放后面 → 验证拓扑排序先跑依赖
                    GraderConfig(
                        type=GraderType.CODE,
                        name="always_pass",
                        dependencies=["code_based"],
                    ),
                    _grader(
                        "code_based",
                        checks=[{"type": "contains", "value": "art_", "target": "outcome"}],
                    ),
                ],
                max_trials=3,
            ),
        ],
    )


def build_agent() -> MockAgentRunner:
    # 每个 trial 依次消耗脚本项; 3 trials × 最多 3 次 attempt
    return MockAgentRunner(
        latency_range=FAST,
        timeout_duration=2.0,
        script={
            "t_timeout": ["timeout"],
            "t_transient": ["transient", "transient", "success"] * 3,
            "t_deps": ["failure"] * 9,
        },
    )


class AlwaysPassGrader:
    name = "always_pass"

    async def grade(self, trial, spans, task, context=None):
        from eval_harness.core.types import GraderResult

        return GraderResult(
            grader_name=self.name,
            grader_type=GraderType.CODE,
            score=1.0,
            passed=True,
            explanation="pass",
        )


@pytest.fixture
def leak_env() -> LeakEnvironment:
    return LeakEnvironment()


@pytest.fixture
def agent() -> MockAgentRunner:
    return build_agent()


async def run_mini_suite(agent: MockAgentRunner, leak_env: LeakEnvironment) -> dict:
    runner = EvalRunner(
        agent_runner=agent,
        trace_provider=MockTraceProvider(),
        storage=MemoryStorage(),
        environment=leak_env,
        graders=[AlwaysPassGrader()],
        per_trial_timeout=0.1,
        max_trial_retries=2,
        retry_base_delay=0.01,
        enable_grader_cache=False,
    )
    run = await runner.run_suite(build_mini_suite())
    summary = run.summary.model_dump()
    return {"run": run, "summary": summary, "runner": runner}


class TestMiniSuiteE2E:
    async def test_full_run_completes(self, agent, leak_env):
        result = await run_mini_suite(agent, leak_env)
        run = result["run"]
        assert run.status == "completed"
        assert run.error is None
        assert set(run.trials.keys()) == {"t_timeout", "t_transient", "t_deps"}
        assert all(len(trials) == 3 for trials in run.trials.values())

    async def test_timeout_trials_fail_with_error(self, agent, leak_env):
        result = await run_mini_suite(agent, leak_env)
        trials = result["run"].trials["t_timeout"]
        assert all(not t.success for t in trials)
        assert all("timed out" in (t.error or "") for t in trials)
        # 超时不重试
        assert agent.call_counts["t_timeout"] == 3

    async def test_transient_retries_recover(self, agent, leak_env):
        result = await run_mini_suite(agent, leak_env)
        trials = result["run"].trials["t_transient"]
        assert all(t.success for t in trials)
        # 每个 trial: 2 次瞬态失败 + 1 次成功 = 3 次 × 3 trials
        assert agent.call_counts["t_transient"] == 9

    async def test_dependency_skip(self, agent, leak_env):
        result = await run_mini_suite(agent, leak_env)
        trials = result["run"].trials["t_deps"]
        for trial in trials:
            by_name = {gr.grader_name: gr for gr in trial.grader_results}
            assert by_name["code_based"].passed is False
            assert by_name["always_pass"].score == 0.0
            assert "依赖未满足" in by_name["always_pass"].explanation

    async def test_leak_detected_and_restored_without_failing_trial(
        self, agent, leak_env, caplog
    ):
        with caplog.at_level(logging.WARNING, logger="eval_harness.core.runner"):
            result = await run_mini_suite(agent, leak_env)

        assert leak_env.restore_calls == 3  # t_deps 的 3 个 trial 各恢复一次
        assert caplog.text.count("Environment leak detected") == 3
        # 泄漏不判 trial 失败 — t_deps 的失败来自评分, 不是泄漏
        trials = result["run"].trials["t_deps"]
        assert all(
            "leak" not in (t.error or "").lower() for t in trials
        )

    async def test_summary_pass_rates(self, agent, leak_env):
        result = await run_mini_suite(agent, leak_env)
        summary = result["summary"]

        ts = {t["task_id"]: t for t in summary["task_summaries"]}

        # t_transient: 3/3 成功 → pass@1 = pass@3 = pass^3 = 1.0
        assert ts["t_transient"]["pass_at_k"][1] == 1.0
        assert ts["t_transient"]["pass_power_k"][3] == 1.0

        # t_timeout / t_deps: 全部失败
        assert ts["t_timeout"]["pass_at_k"][1] == 0.0
        assert ts["t_deps"]["pass_power_k"][1] == 0.0

        # 全局: 1/3 task 通过
        assert summary["pass_at_k"][1] == pytest.approx(1.0 / 3.0)
        assert summary["total_tasks"] == 3
        assert summary["total_trials"] == 9
        assert sorted(summary["failures"]) == ["t_deps", "t_timeout"]

    async def test_not_saturated(self, agent, leak_env):
        result = await run_mini_suite(agent, leak_env)
        sat = result["summary"]["saturation"]
        assert sat["is_saturated"] is False
        assert sat["recommendation"] is None

    async def test_saturated_when_all_tasks_pass(self):
        # 全部通过 → 超过半数 task pass@1 ≥ 0.95 → 饱和告警
        agent = MockAgentRunner(success_rate=1.0, latency_range=FAST)
        runner = EvalRunner(
            agent_runner=agent,
            trace_provider=MockTraceProvider(),
            storage=MemoryStorage(),
        )
        suite = EvalSuite(
            name="saturated",
            tasks=[
                EvalTask(id=f"t{i}", prompt="p", graders=[_grader("code_based")])
                for i in range(3)
            ],
        )
        run = await runner.run_suite(suite)
        sat = run.summary.saturation
        assert sat["is_saturated"] is True
        assert "更有挑战性" in sat["recommendation"]

    async def test_run_persisted_and_queryable(self, agent, leak_env):
        result = await run_mini_suite(agent, leak_env)
        run = result["run"]

        # run_suite 的 finally 已把最终状态落盘, 可从存储读回
        stored = await result["runner"].storage.get_run(run.run_id)
        assert stored is not None
        assert stored.status == "completed"
        assert stored.summary is not None
        assert set(stored.trials.keys()) == {"t_timeout", "t_transient", "t_deps"}
