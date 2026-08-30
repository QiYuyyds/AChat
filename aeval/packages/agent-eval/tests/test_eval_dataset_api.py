"""Integration tests for the dataset REST API — full closed loop (task 5.2).

Chain under test: create dataset → import / mine items → quality-check →
to-suite → start run (MockRunner) → regression extraction merge, plus dataset
CRUD / item CRUD / from-llm / coverage / version bump assertions.
"""

import asyncio
import json

import httpx
import pytest
import pytest_asyncio
from agent_eval.api.app import create_app as create_eval_app
from agent_eval.core.runner import EvalRunner
from agent_eval.examples.mock_runner import MockAgentRunner, MockTraceProvider
from agent_eval.metrics.base import Metric, MetricResult
from agent_eval.storage.memory import MemoryStorage

FAST = (0.0, 0.01)


class ScriptedMetric(Metric):
    """确定性指标 (经 runner.metrics_registry 注册, 供 metric grader 分发)。

    回答含 "fine" → 0.9 通过; 否则 0.0 — 使 t2 (broken answer) 的 trial 失败。
    """

    name = "stub_metric"
    threshold = 0.6

    async def measure(self, input, actual_output, **kwargs) -> MetricResult:
        score = 0.9 if "fine" in (actual_output or "") else 0.0
        return MetricResult(name=self.name, score=score, reason="scripted", threshold=self.threshold)


class ScriptedAgent:
    """t1 成功 / t2 失败 (失败 transcript 携带原始 prompt, 供回归提取)"""

    async def run(self, task):
        if task.id == "t2":
            return (
                "trace_t2_fail",
                [{"role": "user", "content": task.prompt},
                 {"role": "assistant", "content": "broken answer"}],
                {"success": False, "error": "boom"},
            )
        return (
            "trace_t1_ok",
            [{"role": "user", "content": task.prompt},
             {"role": "assistant", "content": "fine answer"}],
            {"success": True},
        )


def make_runner() -> EvalRunner:
    runner = EvalRunner(
        agent_runner=MockAgentRunner(success_rate=1.0, latency_range=FAST),
        trace_provider=MockTraceProvider(),
        storage=MemoryStorage(),
        metrics_registry={"stub_metric": ScriptedMetric()},
        llm_fn=None,
    )
    # 用确定性 agent 替换 (失败路径可控)
    runner.agent_runner = ScriptedAgent()
    return runner


@pytest_asyncio.fixture
async def client():
    app = create_eval_app(runner=make_runner())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _poll_run(client, run_id, timeout=10.0) -> dict:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        resp = await client.get(f"/runs/{run_id}")
        assert resp.status_code == 200
        data = resp.json()
        if data["status"] in ("completed", "failed", "cancelled"):
            return data
        await asyncio.sleep(0.05)
    raise TimeoutError(f"run {run_id} did not finish")


VALID_IMPORT = {
    "content": json.dumps({
        "name": "support-ds",
        "description": "客服数据集",
        "tags": ["support"],
        "items": [
            {
                "id": "ask-refund",
                "prompt": "退款政策是什么？",
                "description": "政策问答",
                "graders": [{"type": "metric", "name": "stub_metric",
                             "config": {"metric_name": "stub_metric"}}],
                "metadata": {"capabilities": ["qa"]},
            },
        ],
    }),
    "format": "json",
}


# ─── CRUD / import ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dataset_crud_and_import(client):
    # 创建 (空数据集)
    resp = await client.post("/datasets", json={
        "name": "empty-ds", "tags": ["blank"],
    })
    assert resp.status_code == 200
    created = resp.json()
    assert created["item_count"] == 0
    ds_id = created["id"]

    # 导入 (YAML/JSON 校验路径)
    resp = await client.post("/datasets/import", json=VALID_IMPORT)
    assert resp.status_code == 200
    imported = resp.json()
    assert imported["item_count"] == 1
    ds2 = imported["id"]

    # 列表 + tags 过滤
    resp = await client.get("/datasets")
    assert resp.status_code == 200
    ids = {d["id"] for d in resp.json()["datasets"]}
    assert {ds_id, ds2} <= ids

    resp = await client.get("/datasets", params={"tags": "support"})
    assert {d["id"] for d in resp.json()["datasets"]} == {ds2}

    # 详情 (含条目与溯源)
    resp = await client.get(f"/datasets/{ds2}")
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["items"][0]["source_type"] == "manual"
    assert detail["items"][0]["id"] == "ask-refund"

    # 名称解析兜底
    resp = await client.get("/datasets/support-ds")
    assert resp.status_code == 200

    # 404
    resp = await client.get("/datasets/ghost")
    assert resp.status_code == 404

    # 删除
    resp = await client.delete(f"/datasets/{ds_id}")
    assert resp.status_code == 200
    resp = await client.get(f"/datasets/{ds_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_import_validation_rejects_missing_fields(client):
    bad = {
        "content": json.dumps({
            "name": "bad-ds",
            "items": [{"id": "i1", "graders": [{"type": "model", "name": "model_based"}]}],
        }),
        "format": "json",
    }
    resp = await client.post("/datasets/import", json=bad)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "i1" in detail and "prompt" in detail


@pytest.mark.asyncio
async def test_item_crud(client):
    resp = await client.post("/datasets", json={"name": "items-ds"})
    ds_id = resp.json()["id"]

    # add
    resp = await client.post(f"/datasets/{ds_id}/items", json={
        "id": "i1", "prompt": "q1",
        "graders": [{"type": "model", "name": "model_based", "config": {"rubric": "r"}}],
    })
    assert resp.status_code == 200

    # duplicate → 409
    resp = await client.post(f"/datasets/{ds_id}/items", json={
        "id": "i1", "prompt": "q1",
        "graders": [{"type": "model", "name": "model_based"}],
    })
    assert resp.status_code == 409

    # list / update
    resp = await client.get(f"/datasets/{ds_id}/items")
    assert len(resp.json()["items"]) == 1

    resp = await client.put(f"/datasets/{ds_id}/items/i1", json={
        "id": "i1", "prompt": "q1-updated",
        "graders": [{"type": "model", "name": "model_based"}],
    })
    assert resp.status_code == 200
    resp = await client.get(f"/datasets/{ds_id}/items")
    assert resp.json()["items"][0]["prompt"] == "q1-updated"

    # delete
    resp = await client.delete(f"/datasets/{ds_id}/items/i1")
    assert resp.status_code == 200
    assert (await client.get(f"/datasets/{ds_id}/items")).json()["items"] == []


# ─── 质量 / 覆盖度 / 升版 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_quality_check_and_coverage(client):
    resp = await client.post("/datasets", json={"name": "quality-ds"})
    ds_id = resp.json()["id"]
    # 空 prompt + 缺 graders + 正常条目
    for item in (
        {"id": "ok", "prompt": "q1",
         "graders": [{"type": "model", "name": "model_based"}],
         "metadata": {"capabilities": ["qa"]}},
        {"id": "empty", "prompt": "", "graders": [{"type": "model", "name": "model_based"}]},
        {"id": "nog", "prompt": "q2", "graders": []},
    ):
        await client.post(f"/datasets/{ds_id}/items", json=item)

    resp = await client.get(f"/datasets/{ds_id}/quality-check")
    assert resp.status_code == 200
    report = resp.json()
    assert {e["code"] for e in report["errors"]} == {"empty_prompt", "missing_graders"}
    assert report["ok"] is False

    resp = await client.get(f"/datasets/{ds_id}/coverage", params={"expected": "rag"})
    coverage = resp.json()
    assert coverage["coverage"]["qa"] == round(1 / 5, 3)
    assert {"capability": "rag", "item_count": 0, "coverage": 0.0} in coverage["insufficient"]

    # to-suite 在质量错误时应拒绝
    resp = await client.post(f"/datasets/{ds_id}/to-suite", json={"save": False})
    assert resp.status_code == 422
    assert "fix the items" in resp.json()["detail"]


# ─── from-llm ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_from_llm_requires_llm_fn(client):
    resp = await client.post("/datasets", json={"name": "llm-ds"})
    ds_id = resp.json()["id"]
    resp = await client.post(f"/datasets/{ds_id}/from-llm", json={
        "scenario": "客服场景", "count": 3,
    })
    # runner 未注入 llm_fn → 503 明确配置错误
    assert resp.status_code == 503
    assert "LLM function not configured" in resp.json()["detail"]


# ─── 全链路闭环 ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_full_closed_loop(client):
    """创建 → 导入 → quality-check → to-suite → run → 回归提取 → 升版"""
    # 1. 导入带条目的数据集 (含可执行 grader)
    resp = await client.post("/datasets/import", json=VALID_IMPORT)
    assert resp.status_code == 200
    ds_id = resp.json()["id"]

    # 2. 质量检查通过
    resp = await client.get(f"/datasets/{ds_id}/quality-check")
    assert resp.json()["ok"] is True

    # 3. to-suite (落库)
    resp = await client.post(f"/datasets/{ds_id}/to-suite", json={"name": "support-suite"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["task_count"] == 1
    assert body["dataset_version"] == "1.0.0"
    assert body["metadata"]["dataset_id"] == ds_id
    suite_name = body["suite_name"]

    # suite 元数据记录数据集版本
    resp = await client.get(f"/suites/{suite_name}")
    assert resp.status_code == 200
    assert resp.json()["metadata"]["dataset_version"] == "1.0.0"

    # 4. 手工放一个失败 task 进 suite (ScriptedAgent 对 t2 失败) —
    #    通过 POST /suites 更新 suite, 增加 t2
    resp = await client.get(f"/suites/{suite_name}")
    suite = resp.json()
    suite["tasks"].append({
        "id": "t2",
        "prompt": "转账失败了怎么办？",
        "graders": [{"type": "metric", "name": "stub_metric",
                     "config": {"metric_name": "stub_metric"}}],
    })
    resp = await client.post("/suites", json=suite)
    assert resp.status_code == 200

    # 5. 启动 run (t1 成功, t2 失败)
    resp = await client.post("/runs", json={"suite_name": suite_name})
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]
    run = await _poll_run(client, run_id)
    assert run["status"] == "completed"
    assert set(run["summary"]["failures"]) == {"t2"}

    # 6. 回归提取合入 (bump minor)
    resp = await client.post(f"/datasets/{ds_id}/regression-extract", json={
        "run_id": run_id, "bump_version": "minor",
    })
    assert resp.status_code == 200
    result = resp.json()
    assert result["extraction"]["extracted"] == 1  # t2 首个失败 trial
    assert result["merge"]["merged"] == 1
    assert result["bumped"] is True
    assert result["version"] == "1.1.0"

    # 7. 回归条目可查, 溯源指向失败 trace
    resp = await client.get(f"/datasets/{ds_id}/items")
    items = resp.json()["items"]
    reg = [i for i in items if i["source_type"] == "regression"]
    assert len(reg) == 1
    assert reg[0]["prompt"] == "转账失败了怎么办？"
    assert reg[0]["source_ref"] == "trace_t2_fail"
    assert reg[0]["graders"], "回归条目应复用 suite task 的 graders"

    # 8. 变更记录可查询
    resp = await client.get(f"/datasets/{ds_id}")
    change_log = resp.json()["change_log"]
    assert len(change_log) == 1
    assert change_log[0]["change_type"] == "minor"
    assert change_log[0]["version"] == "1.1.0"

    # 9. 升版后的数据集再次 to-suite → 新版本落库
    resp = await client.post(f"/datasets/{ds_id}/to-suite", json={"name": "support-suite"})
    assert resp.status_code == 200
    assert resp.json()["dataset_version"] == "1.1.0"
    assert resp.json()["task_count"] == 2  # 原条目 + 回归条目


@pytest.mark.asyncio
async def test_regression_extract_missing_run_404(client):
    resp = await client.post("/datasets", json={"name": "reg-ds"})
    ds_id = resp.json()["id"]
    resp = await client.post(f"/datasets/{ds_id}/regression-extract", json={
        "run_id": "run_ghost",
    })
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_version_bump_endpoint(client):
    resp = await client.post("/datasets", json={"name": "ver-ds"})
    ds_id = resp.json()["id"]

    resp = await client.post(f"/datasets/{ds_id}/version", json={
        "change_type": "minor", "note": "首次扩充",
    })
    assert resp.status_code == 200
    assert resp.json()["version"] == "1.1.0"

    resp = await client.post(f"/datasets/{ds_id}/version", json={
        "change_type": "patch", "note": "修文案",
    })
    assert resp.json()["version"] == "1.1.1"

    resp = await client.post(f"/datasets/{ds_id}/version", json={
        "change_type": "bogus",
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_from_trace_with_empty_provider(client):
    """MockTraceProvider 无 spans → 挖掘 0 条但 API 正常返回"""
    resp = await client.post("/datasets", json={"name": "trace-ds"})
    ds_id = resp.json()["id"]
    resp = await client.post(f"/datasets/{ds_id}/from-trace", json={
        "strategy": "failed_tasks", "limit": 10,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["mining"]["mined"] == 0
    assert body["item_count"] == 0


@pytest.mark.asyncio
async def test_from_trace_unsupported_strategy_422(client):
    resp = await client.post("/datasets", json={"name": "trace-ds2"})
    ds_id = resp.json()["id"]
    resp = await client.post(f"/datasets/{ds_id}/from-trace", json={
        "strategy": "user_dissatisfied",
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_no_runner_503():
    app = create_eval_app(runner=None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/datasets")
        assert resp.status_code == 503
        resp = await c.post("/datasets", json={"name": "x"})
        assert resp.status_code == 503
