"""Tests for GET /tasks/{task_id}/history — cross-run trial aggregation.

Covers: multi-run aggregation correctness (倒序 / trials_passed / avg_score /
grader 分解), unknown task 404, empty history, and grader breakdown summary.
"""

import httpx
import pytest
import pytest_asyncio
from agent_eval.api.app import create_app as create_eval_app
from agent_eval.core.runner import EvalRunner
from agent_eval.core.types import (
    EvalSuite,
    EvalTask,
    GraderConfig,
    GraderResult,
    GraderType,
    RunResult,
    TrialResult,
)
from agent_eval.examples.mock_runner import MockAgentRunner, MockTraceProvider
from agent_eval.storage.memory import MemoryStorage

BASE_MS = 1_700_000_000_000


def make_suite() -> EvalSuite:
    return EvalSuite(
        name="demo-suite",
        tasks=[
            EvalTask(
                id="t1",
                prompt="demo prompt",
                graders=[
                    GraderConfig(type=GraderType.CODE, name="code_based"),
                    GraderConfig(type=GraderType.MODEL, name="model_based"),
                ],
            ),
            EvalTask(
                id="t2",
                prompt="other prompt",
                graders=[GraderConfig(type=GraderType.CODE, name="code_based")],
            ),
        ],
    )


def make_trial(index: int, scores: dict[str, float], success: bool = True) -> TrialResult:
    return TrialResult(
        trial_index=index,
        success=success,
        grader_results=[
            GraderResult(
                grader_name=name,
                grader_type=GraderType.CODE if name == "code_based" else GraderType.MODEL,
                score=score,
                passed=score >= 0.7,
            )
            for name, score in scores.items()
        ],
    )


def make_run(run_id: str, started_at: float, trials: dict[str, list[TrialResult]]) -> RunResult:
    return RunResult(
        run_id=run_id,
        suite_name="demo-suite",
        status="completed",
        started_at=started_at,
        completed_at=started_at + 1_000,
        trials=trials,
    )


def make_runner() -> EvalRunner:
    return EvalRunner(
        agent_runner=MockAgentRunner(success_rate=1.0, latency_range=(0.0, 0.01)),
        trace_provider=MockTraceProvider(),
        storage=MemoryStorage(),
        llm_fn=None,
    )


@pytest_asyncio.fixture
async def client():
    runner = make_runner()
    await runner.storage.save_suite(make_suite())
    # 三次 run 含 t1 (时间交错), 一次 run 只含 t2
    await runner.storage.save_run(make_run("run_old", BASE_MS + 1_000, {
        "t1": [make_trial(0, {"code_based": 0.8, "model_based": 0.6}),
               make_trial(1, {"code_based": 0.4, "model_based": 0.2}, success=False)],
    }))
    await runner.storage.save_run(make_run("run_other", BASE_MS + 2_000, {
        "t2": [make_trial(0, {"code_based": 1.0})],
    }))
    await runner.storage.save_run(make_run("run_new", BASE_MS + 3_000, {
        "t1": [make_trial(0, {"code_based": 0.9, "model_based": 0.7})],
    }))
    app = create_eval_app(runner=runner)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.mark.asyncio
async def test_history_aggregates_multiple_runs(client):
    resp = await client.get("/tasks/t1/history")
    assert resp.status_code == 200
    body = resp.json()
    assert body["task_id"] == "t1"
    assert body["suite_name"] == "demo-suite"

    # 只含 t1 的 run, 按 started_at 倒序
    assert [h["run_id"] for h in body["history"]] == ["run_new", "run_old"]

    latest = body["history"][0]
    assert latest["trials_total"] == 1
    assert latest["trials_passed"] == 1
    assert latest["avg_score"] == pytest.approx(0.8)
    assert latest["graders"] == {"code_based": 0.9, "model_based": 0.7}

    older = body["history"][1]
    assert older["trials_total"] == 2
    assert older["trials_passed"] == 1
    # trial 平均分: (0.7 + 0.3) / 2 = 0.5
    assert older["avg_score"] == pytest.approx(0.5)
    assert older["graders"]["code_based"] == pytest.approx(0.6)
    assert older["graders"]["model_based"] == pytest.approx(0.4)


@pytest.mark.asyncio
async def test_history_unknown_task_404(client):
    resp = await client.get("/tasks/ghost/history")
    assert resp.status_code == 404
    assert "ghost" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_history_empty_when_task_never_ran(client):
    """task 存在于 suite 但从未参与 run → 空数组而非 404"""
    suite = make_suite()
    suite.tasks.append(
        EvalTask(id="t_never", prompt="x",
                 graders=[GraderConfig(type=GraderType.CODE, name="code_based")])
    )
    # 重写存储: 该 task 在 suite 中但无 run 记录
    runner = make_runner()
    await runner.storage.save_suite(suite)
    app = create_eval_app(runner=runner)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.get("/tasks/t_never/history")
    assert resp.status_code == 200
    assert resp.json()["history"] == []


@pytest.mark.asyncio
async def test_history_no_runner_503():
    app = create_eval_app(runner=None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.get("/tasks/t1/history")
    assert resp.status_code == 503
