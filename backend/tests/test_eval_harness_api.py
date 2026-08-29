"""Integration tests for the eval harness REST API (tasks 5.1–5.6).

Covers: suite CRUD → start run (MockRunner) → poll results → compare full
chain, cancel, human score submission, graders listing, the 503 behavior when
no runner is injected, and coexistence of /api/eval/judge/* with the mounted
eval sub-app routes.
"""

import asyncio

import httpx
import pytest
import pytest_asyncio
from eval_harness.api.app import create_app as create_eval_app
from eval_harness.core.runner import EvalRunner
from eval_harness.examples.mock_runner import MockAgentRunner, MockTraceProvider
from eval_harness.storage.memory import MemoryStorage

FAST = (0.0, 0.01)


def make_runner(agent: MockAgentRunner | None = None) -> EvalRunner:
    return EvalRunner(
        agent_runner=agent or MockAgentRunner(success_rate=1.0, latency_range=FAST),
        trace_provider=MockTraceProvider(),
        storage=MemoryStorage(),
    )


def make_eval_client(runner: EvalRunner | None):
    app = create_eval_app(runner=runner)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest_asyncio.fixture
async def client():
    async with make_eval_client(make_runner()) as c:
        yield c


SAMPLE_SUITE = {
    "name": "api-suite",
    "description": "suite created via API",
    "version": "1.0.0",
    "tasks": [
        {
            "id": "t1",
            "prompt": "hello",
            "graders": [{"type": "code", "name": "code_based"}],
            "max_trials": 2,
        },
        {
            "id": "t2",
            "prompt": "world",
            "graders": [{"type": "code", "name": "code_based"}],
        },
    ],
}


async def _poll_run(client: httpx.AsyncClient, run_id: str, timeout: float = 10.0) -> dict:
    """轮询 run 直到结束状态"""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get(f"/runs/{run_id}")
        assert resp.status_code == 200
        data = resp.json()
        if data["status"] in ("completed", "failed", "cancelled"):
            return data
        await asyncio.sleep(0.05)
    raise TimeoutError(f"Run {run_id} did not finish in {timeout}s")


# ─── Runner 未注入 → 503 (task 5.5) ──────────────────────────────────────────


class TestNoRunner:
    @pytest.mark.parametrize(
        "method,path,body", [
            ("get", "/runs", None),
            ("post", "/runs", {"suite_name": "s"}),
            ("get", "/runs/run_x", None),
            ("delete", "/runs/run_x", None),
            ("get", "/runs/run_x/trials", None),
            ("post", "/compare", {"run_id_a": "x", "run_id_b": "y"}),
            ("post", "/runs/run_x/cancel", None),
            ("post", "/runs/run_x/human-scores",
             {"task_id": "t", "trial_index": 0, "score": 1.0}),
        ],
    )
    async def test_routes_return_503_without_runner(self, method, path, body):
        async with make_eval_client(None) as c:
            resp = await c.request(method, path, json=body)
        assert resp.status_code == 503

    async def test_health_and_graders_work_without_runner(self):
        async with make_eval_client(None) as c:
            assert (await c.get("/health")).status_code == 200
            assert (await c.get("/graders")).status_code == 200


# ─── Suite CRUD ───────────────────────────────────────────────────────────────


class TestSuiteCRUD:
    async def test_create_list_get_delete(self, client):
        resp = await client.post("/suites", json=SAMPLE_SUITE)
        assert resp.status_code == 200
        assert resp.json()["task_count"] == 2

        resp = await client.get("/suites")
        assert any(s["name"] == "api-suite" for s in resp.json()["suites"])

        resp = await client.get("/suites/api-suite")
        assert resp.json()["name"] == "api-suite"

        resp = await client.delete("/suites/api-suite")
        assert resp.json()["deleted"] is True
        assert (await client.get("/suites/api-suite")).status_code == 404

    async def test_invalid_suite_rejected(self, client):
        bad = {**SAMPLE_SUITE, "name": "bad", "tasks": []}
        resp = await client.post("/suites", json=bad)
        assert resp.status_code == 422  # Pydantic 校验失败


# ─── Run 全链路 (task 5.6) ────────────────────────────────────────────────────


class TestRunLifecycle:
    async def test_start_poll_results(self, client):
        await client.post("/suites", json=SAMPLE_SUITE)

        resp = await client.post("/runs", json={"suite_name": "api-suite"})
        assert resp.status_code == 200
        run_id = resp.json()["run_id"]  # 立即返回 run_id
        assert resp.json()["status"] == "running"

        run = await _poll_run(client, run_id)
        assert run["status"] == "completed"
        assert run["summary"]["total_tasks"] == 2
        assert set(run["summary"]["pass_at_k"].keys()) == {"1", "2", "3"}
        assert run["summary"]["pass_at_k"]["1"] == 1.0
        assert "saturation" in run["summary"]

    async def test_unknown_suite_404(self, client):
        resp = await client.post("/runs", json={"suite_name": "nope"})
        assert resp.status_code == 404

    async def test_trials_endpoint_with_and_without_task_filter(self, client):
        await client.post("/suites", json=SAMPLE_SUITE)
        run_id = (await client.post("/runs", json={"suite_name": "api-suite"})).json()["run_id"]
        await _poll_run(client, run_id)

        resp = await client.get(f"/runs/{run_id}/trials")
        assert resp.status_code == 200
        all_trials = resp.json()["trials"]
        assert set(all_trials.keys()) == {"t1", "t2"}
        assert len(all_trials["t1"]) == 2

        resp = await client.get(f"/runs/{run_id}/trials", params={"task_id": "t1"})
        assert resp.status_code == 200
        filtered = resp.json()
        assert filtered["task_id"] == "t1"
        assert len(filtered["trials"]) == 2
        assert "grader_results" in filtered["trials"][0]

    async def test_compare_two_runs(self, client):
        await client.post("/suites", json=SAMPLE_SUITE)
        run_a = (await client.post("/runs", json={"suite_name": "api-suite"})).json()["run_id"]
        run_b = (await client.post("/runs", json={"suite_name": "api-suite"})).json()["run_id"]
        await _poll_run(client, run_a)
        await _poll_run(client, run_b)

        resp = await client.post("/compare", json={"run_id_a": run_a, "run_id_b": run_b})
        assert resp.status_code == 200
        comparison = resp.json()["comparison"]
        assert "pass_at_k" in comparison
        assert "pass_power_k" in comparison
        assert "avg_score" in comparison
        assert comparison["avg_score"]["delta"] == 0.0
        assert comparison["regressions"] == []
        assert comparison["improvements"] == []

    async def test_compare_missing_run_404(self, client):
        resp = await client.post("/compare", json={"run_id_a": "x", "run_id_b": "y"})
        assert resp.status_code == 404


# ─── 取消运行 (task 5.2) ──────────────────────────────────────────────────────


class TestCancel:
    async def test_cancel_running_run(self):
        slow_agent = MockAgentRunner(success_rate=1.0, latency_range=(0.5, 0.5))
        async with make_eval_client(make_runner(slow_agent)) as client:
            await client.post("/suites", json=SAMPLE_SUITE)
            run_id = (await client.post("/runs", json={"suite_name": "api-suite"})).json()["run_id"]

            resp = await client.post(f"/runs/{run_id}/cancel")
            assert resp.status_code == 200
            assert resp.json()["cancelled"] is True

            run = await _poll_run(client, run_id, timeout=5.0)
            assert run["status"] == "cancelled"
            # 已完成的 trial 保留可查询
            trials = (await client.get(f"/runs/{run_id}/trials")).json()["trials"]
            assert isinstance(trials, dict)

    async def test_cancel_unknown_run_404(self, client):
        resp = await client.post("/runs/run_nope/cancel")
        assert resp.status_code == 404

    async def test_cancel_finished_run_409(self, client):
        await client.post("/suites", json=SAMPLE_SUITE)
        run_id = (await client.post("/runs", json={"suite_name": "api-suite"})).json()["run_id"]
        await _poll_run(client, run_id)

        resp = await client.post(f"/runs/{run_id}/cancel")
        assert resp.status_code == 409


# ─── Grader 列表 (task 5.3) ───────────────────────────────────────────────────


class TestGradersEndpoint:
    async def test_list_graders(self, client):
        resp = await client.get("/graders")
        assert resp.status_code == 200
        graders = resp.json()["graders"]
        names = {g["name"] for g in graders}
        assert {"code_based", "model_based", "human", "step_level"} <= names
        for g in graders:
            assert set(g) == {"name", "type", "description"}


# ─── 人工评分回传 (task 5.4) ──────────────────────────────────────────────────


class TestHumanScores:
    async def _run_with_human_grader(self, client):
        suite = {
            "name": "human-suite",
            "tasks": [
                {
                    "id": "t1",
                    "prompt": "hello",
                    "graders": [
                        {"type": "custom", "name": "human"},
                        {"type": "code", "name": "code_based"},
                    ],
                    "max_trials": 1,
                },
            ],
        }
        await client.post("/suites", json=suite)
        run_id = (await client.post("/runs", json={"suite_name": "human-suite"})).json()["run_id"]
        run = await _poll_run(client, run_id)
        return run

    async def test_submit_score_updates_result_and_summary(self, client):
        run = await self._run_with_human_grader(client)
        run_id = run["run_id"]

        # 提交前: human grader pending, trial 未通过
        t1_summary = next(
            ts for ts in run["summary"]["task_summaries"] if ts["task_id"] == "t1"
        )
        assert t1_summary["pending_trials"] == [0]

        resp = await client.post(
            f"/runs/{run_id}/human-scores",
            json={"task_id": "t1", "trial_index": 0, "score": 1.0, "explanation": "good job"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["passed"] is True
        assert data["trial_success"] is True  # 混合策略加权分达到阈值
        assert data["summary"] is not None

        # 汇总已重算: pending 清空, pass@1 = 1.0
        updated = await client.get(f"/runs/{run_id}")
        t1_updated = next(
            ts for ts in updated.json()["summary"]["task_summaries"]
            if ts["task_id"] == "t1"
        )
        assert t1_updated["pending_trials"] == []
        assert t1_updated["pass_at_k"]["1"] == 1.0

        grader_results = updated.json()["trials"]["t1"][0]["grader_results"]
        human = next(gr for gr in grader_results if gr["grader_name"] == "human")
        assert human["score"] == 1.0
        assert human["passed"] is True

    async def test_unknown_task_or_trial_404(self, client):
        run = await self._run_with_human_grader(client)
        run_id = run["run_id"]

        resp = await client.post(
            f"/runs/{run_id}/human-scores",
            json={"task_id": "nope", "trial_index": 0, "score": 1.0},
        )
        assert resp.status_code == 404

        resp = await client.post(
            f"/runs/{run_id}/human-scores",
            json={"task_id": "t1", "trial_index": 9, "score": 1.0},
        )
        assert resp.status_code == 404

        # trial 上没有对应名称的 grader 结果
        resp = await client.post(
            f"/runs/{run_id}/human-scores",
            json={"task_id": "t1", "trial_index": 0, "score": 1.0, "grader_name": "nope"},
        )
        assert resp.status_code == 404

    async def test_unknown_run_404(self, client):
        resp = await client.post(
            "/runs/run_nope/human-scores",
            json={"task_id": "t1", "trial_index": 0, "score": 1.0},
        )
        assert resp.status_code == 404


# ─── /api/eval/judge/* 共存 (task 5.6) ────────────────────────────────────────


class TestJudgeCoexistence:
    async def test_judge_route_and_eval_subapp_coexist(self, monkeypatch):
        monkeypatch.setenv("EVAL_HARNESS_ENABLED", "true")
        from app.config import get_settings

        get_settings.cache_clear()
        try:
            from app.main import create_app as create_main_app

            app = create_main_app()

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as c:
                # eval 子应用路由可达
                health = await c.get("/api/eval/health")
                assert health.status_code == 200

                graders = await c.get("/api/eval/graders")
                assert graders.status_code == 200

                # judge 路由仍可达 (先注册先匹配; 默认关闭 → 403)
                judge = await c.post("/api/eval/judge/some_trace")
                assert judge.status_code == 403
        finally:
            get_settings.cache_clear()

    async def test_mount_absent_when_disabled(self, monkeypatch):
        monkeypatch.setenv("EVAL_HARNESS_ENABLED", "false")
        from app.config import get_settings

        get_settings.cache_clear()
        try:
            from app.main import create_app as create_main_app

            app = create_main_app()
            mount_paths = [
                r.path for r in app.routes if hasattr(r, "routes")
            ]
            assert "/api/eval" not in mount_paths
        finally:
            get_settings.cache_clear()
