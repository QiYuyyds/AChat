"""Integration tests for POST /api/eval/metrics/batch — task 2.2 (stub registry).

Covers: 合法请求 200 且 results/summary/pass_rate 齐全、thresholds 优先生效、
未注册指标 422 (列无效指标, 零打分)、runner 未装配 503、注册表未装配 503、
LLM 未配置 503。
"""

import json

import httpx
import pytest

from agent_eval.api.app import create_app as create_eval_app
from agent_eval.core.runner import EvalRunner
from agent_eval.examples.mock_runner import MockAgentRunner, MockTraceProvider
from agent_eval.metrics.answer_relevancy import AnswerRelevancyMetric
from agent_eval.metrics.faithfulness import FaithfulnessMetric
from agent_eval.storage.memory import MemoryStorage

FAST = (0.0, 0.01)


def stub_llm(score: float = 0.9):
    calls: list[tuple[str, str]] = []

    async def llm_fn(system: str, user: str) -> str:
        calls.append((system, user))
        return json.dumps({"score": score, "reason": "stub"}, ensure_ascii=False)

    llm_fn.calls = calls  # type: ignore[attr-defined]
    return llm_fn


def make_runner(llm_fn) -> EvalRunner:
    return EvalRunner(
        agent_runner=MockAgentRunner(success_rate=1.0, latency_range=FAST),
        trace_provider=MockTraceProvider(),
        storage=MemoryStorage(),
        metrics_registry={
            "answer_relevancy": AnswerRelevancyMetric(llm_fn=llm_fn, threshold=0.5),
            "faithfulness": FaithfulnessMetric(llm_fn=llm_fn, threshold=0.5),
        },
        llm_fn=llm_fn,
    )


def make_client(runner: EvalRunner | None) -> httpx.AsyncClient:
    app = create_eval_app(runner=runner)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


VALID_REQUEST = {
    "test_cases": [
        {"input": "q1", "actual_output": "a1", "context": ["doc1"]},
        {"input": "q2", "actual_output": "a2", "context": ["doc1"]},
    ],
    "metrics": ["answer_relevancy", "faithfulness"],
    "thresholds": {"answer_relevancy": 0.8},
}


class TestBatchEndpoint:
    async def test_valid_request_returns_full_summary(self):
        llm = stub_llm(0.9)
        async with make_client(make_runner(llm)) as client:
            resp = await client.post("/metrics/batch", json=VALID_REQUEST)

        assert resp.status_code == 200
        data = resp.json()

        # results 与 summary 字段齐全
        assert len(data["results"]) == 2
        for case in data["results"]:
            assert set(case["scores"]) == {"answer_relevancy", "faithfulness"}
            for score in case["scores"].values():
                assert {"score", "success", "reason"} <= set(score)
        assert set(data["summary"]) == {"answer_relevancy", "faithfulness"}
        assert data["pass_count"] == 2
        assert data["fail_count"] == 0
        assert data["pass_rate"] == 1.0

    async def test_thresholds_take_priority(self):
        llm = stub_llm(0.6)
        async with make_client(make_runner(llm)) as client:
            resp = await client.post("/metrics/batch", json=VALID_REQUEST)

        assert resp.status_code == 200
        data = resp.json()
        # answer_relevancy 0.6 < 显式阈值 0.8 → fail; faithfulness 默认 0.5 → pass
        assert data["summary"]["answer_relevancy"]["threshold"] == 0.8
        assert data["summary"]["answer_relevancy"]["pass_count"] == 0
        assert data["summary"]["faithfulness"]["threshold"] == 0.5
        assert data["summary"]["faithfulness"]["pass_count"] == 2
        assert data["results"][0]["overall_pass"] is False
        assert data["pass_rate"] == 0.0

    async def test_unknown_metric_422_without_scoring(self):
        llm = stub_llm()
        request = {**VALID_REQUEST, "metrics": ["answer_relevancy", "ghost"]}
        async with make_client(make_runner(llm)) as client:
            resp = await client.post("/metrics/batch", json=request)

        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["unknown_metrics"] == ["ghost"]
        assert llm.calls == []  # 未执行任何打分

    async def test_no_runner_503(self):
        async with make_client(None) as client:
            resp = await client.post("/metrics/batch", json=VALID_REQUEST)
        assert resp.status_code == 503

    async def test_no_metrics_registry_503(self):
        runner = EvalRunner(
            agent_runner=MockAgentRunner(success_rate=1.0, latency_range=FAST),
            trace_provider=MockTraceProvider(),
            storage=MemoryStorage(),
        )
        async with make_client(runner) as client:
            resp = await client.post("/metrics/batch", json=VALID_REQUEST)
        assert resp.status_code == 503

    async def test_llm_not_configured_503(self):
        runner = EvalRunner(
            agent_runner=MockAgentRunner(success_rate=1.0, latency_range=FAST),
            trace_provider=MockTraceProvider(),
            storage=MemoryStorage(),
            metrics_registry={
                "answer_relevancy": AnswerRelevancyMetric(llm_fn=None),
                "faithfulness": FaithfulnessMetric(llm_fn=None),
            },
            llm_fn=None,
        )
        async with make_client(runner) as client:
            resp = await client.post("/metrics/batch", json=VALID_REQUEST)
        assert resp.status_code == 503
        assert "LLM" in resp.json()["detail"]

    async def test_empty_test_cases_accepted(self):
        llm = stub_llm()
        async with make_client(make_runner(llm)) as client:
            resp = await client.post(
                "/metrics/batch", json={**VALID_REQUEST, "test_cases": []}
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["results"] == []
        assert data["pass_rate"] == 0.0
        assert llm.calls == []

    async def test_empty_metrics_rejected(self):
        async with make_client(make_runner(stub_llm())) as client:
            resp = await client.post(
                "/metrics/batch", json={**VALID_REQUEST, "metrics": []}
            )
        assert resp.status_code == 422  # Pydantic min_length=1
