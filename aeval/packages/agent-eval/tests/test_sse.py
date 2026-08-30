"""Run 事件总线 + SSE 流端点测试 (任务 3.3)。

覆盖:
    - bus: 多订阅者收完整序列; 终态缓存注入; 断开不泄漏队列; 慢队列丢旧
    - 端点: 运行中订阅收增量且 run_complete 收尾; 完成后订阅立即收终态;
      未知 run 404; 无 runner 503; 心跳/关流后订阅清理
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from agent_eval.api.app import create_app as create_eval_app
from agent_eval.api.events import RunEventBus, run_event_bus
from agent_eval.core.runner import EvalRunner
from agent_eval.examples.mock_runner import MockAgentRunner, MockTraceProvider
from agent_eval.storage.memory import MemoryStorage

from test_api import SAMPLE_SUITE, make_eval_client, make_runner


# ─── RunEventBus 单元 ────────────────────────────────────────────────────────


class TestRunEventBus:
    async def test_multi_subscriber_full_sequence(self):
        bus = RunEventBus()
        q1, q2 = bus.subscribe("r1"), bus.subscribe("r1")
        for i in range(3):
            bus.publish("r1", "trial_complete", {"task_id": "t", "trial_index": i})
        bus.publish("r1", "run_complete", {"status": "completed"})

        for q in (q1, q2):
            types = [q.get_nowait()["type"] for _ in range(4)]
            assert types == ["trial_complete"] * 3 + ["run_complete"]
        assert bus.subscriber_count("r1") == 2

    async def test_late_subscriber_gets_cached_terminal(self):
        bus = RunEventBus()
        bus.publish("r1", "run_complete", {"status": "completed"})
        q = bus.subscribe("r1")
        event = q.get_nowait()
        assert event["type"] == "run_complete"
        assert event["status"] == "completed"

    async def test_unsubscribe_no_leak(self):
        bus = RunEventBus()
        q = bus.subscribe("r1")
        assert bus.subscriber_count("r1") == 1
        bus.unsubscribe("r1", q)
        assert bus.subscriber_count("r1") == 0
        bus.unsubscribe("r1", q)  # 幂等
        # 取消订阅后再 publish 不影响任何队列
        bus.publish("r1", "task_start", {})
        assert q.empty()

    async def test_slow_queue_drops_oldest(self):
        bus = RunEventBus()
        q = bus.subscribe("r1")
        for i in range(300):
            bus.publish("r1", "task_start", {"seq": i})
        assert q.qsize() <= 256
        # 最新事件仍在 (丢的是最旧的)
        latest = json.dumps({})  # 占位; 直接消费到尾
        while q.qsize() > 1:
            q.get_nowait()
        assert q.get_nowait()["seq"] == 299

    async def test_terminal_cache_bounded(self):
        bus = RunEventBus()
        for i in range(600):
            bus.publish(f"r{i}", "run_complete", {"status": "completed"})
        assert bus.terminal_event("r0") is None  # 最旧的被淘汰
        assert bus.terminal_event("r599") is not None


# ─── SSE 端点 ────────────────────────────────────────────────────────────────


async def _read_sse_until(
    resp: httpx.Response, stop_type: str, max_events: int = 200
) -> list[dict]:
    """逐行读取 SSE data 帧, 直到指定类型事件 (含) 或流结束。"""
    events: list[dict] = []
    async for line in resp.aiter_lines():
        if not line.startswith("data: "):
            continue
        event = json.loads(line[len("data: "):])
        events.append(event)
        if event.get("type") == stop_type or len(events) >= max_events:
            break
    return events


class TestStreamEndpoint:
    async def test_completed_run_immediate_terminal(self):
        runner = make_runner()
        async with make_eval_client(runner) as c:
            await c.post("/suites", json=SAMPLE_SUITE)
            run_id = (await c.post("/runs", json={"suite_name": "api-suite"})).json()["run_id"]
            # 等待 run 结束 (FAST 延迟; POST 后先经 pending 再 running)
            for _ in range(200):
                status = (await c.get(f"/runs/{run_id}")).json()["status"]
                if status in ("completed", "failed", "cancelled"):
                    break
                await asyncio.sleep(0.05)

            async with c.stream("GET", f"/runs/{run_id}/stream") as resp:
                assert resp.status_code == 200
                events = await _read_sse_until(resp, "run_complete")
            assert len(events) == 1
            assert events[0]["type"] == "run_complete"
            assert events[0]["status"] == "completed"

    async def test_unknown_run_404(self):
        async with make_eval_client(make_runner()) as c:
            resp = await c.get("/runs/run_missing/stream")
            assert resp.status_code == 404

    async def test_no_runner_503(self):
        async with make_eval_client(None) as c:
            resp = await c.get("/runs/run_x/stream")
            assert resp.status_code == 503

    async def test_running_run_full_sequence_and_no_subscriber_leak(self):
        agent = MockAgentRunner(success_rate=1.0, latency_range=(0.15, 0.2))
        runner = EvalRunner(
            agent_runner=agent,
            trace_provider=MockTraceProvider(),
            storage=MemoryStorage(),
        )
        async with make_eval_client(runner) as c:
            await c.post("/suites", json=SAMPLE_SUITE)
            run_id = (await c.post("/runs", json={"suite_name": "api-suite"})).json()["run_id"]

            async with c.stream("GET", f"/runs/{run_id}/stream") as resp:
                assert resp.status_code == 200
                events = await _read_sse_until(resp, "run_complete")

            types = [e["type"] for e in events]
            assert types[-1] == "run_complete"
            assert "task_start" in types
            assert "trial_start" in types
            assert "trial_complete" in types
            assert "task_complete" in types
            # 事件载荷幂等键
            trial_events = [e for e in events if e["type"] == "trial_complete"]
            assert all("task_id" in e and "trial_index" in e for e in trial_events)
            # SSE 关流后订阅被清理 (无队列泄漏)
            for _ in range(50):
                if run_event_bus.subscriber_count(run_id) == 0:
                    break
                await asyncio.sleep(0.02)
            assert run_event_bus.subscriber_count(run_id) == 0

    async def test_multi_subscribers_same_run_endpoint(self):
        agent = MockAgentRunner(success_rate=1.0, latency_range=(0.15, 0.2))
        runner = EvalRunner(
            agent_runner=agent,
            trace_provider=MockTraceProvider(),
            storage=MemoryStorage(),
        )
        async with make_eval_client(runner) as c:
            await c.post("/suites", json=SAMPLE_SUITE)
            run_id = (await c.post("/runs", json={"suite_name": "api-suite"})).json()["run_id"]

            async def collect():
                async with c.stream("GET", f"/runs/{run_id}/stream") as resp:
                    return await _read_sse_until(resp, "run_complete")

            results = await asyncio.gather(collect(), collect())
            for events in results:
                assert events[-1]["type"] == "run_complete"
                assert "task_complete" in [e["type"] for e in events]
