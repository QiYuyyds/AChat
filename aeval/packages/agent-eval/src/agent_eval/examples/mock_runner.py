"""
Mock AgentRunner for testing and demonstration.

Simulates an agent by returning predefined results, with per-task scripted
behaviors for exercising the framework's failure paths.

Usage:
    from agent_eval.examples.mock_runner import MockAgentRunner

    # Random behavior (demo)
    runner = EvalRunner(agent_runner=MockAgentRunner())

    # Scripted behavior (tests): each task consumes its behavior list in
    # order across calls; the last entry repeats once exhausted.
    agent = MockAgentRunner(
        latency_range=(0.0, 0.01),
        script={
            "task_ok": ["success"],
            "task_flaky": ["transient", "transient", "success"],
            "task_dead": ["failure"],
            "task_slow": ["timeout"],
        },
    )
"""

from __future__ import annotations

import asyncio
import random
import time
import uuid
from typing import Any

from agent_eval.core.contract import AgentRunner, TransientError
from agent_eval.core.types import EvalTask


class MockAgentRunner:
    """
    模拟 AgentRunner。

    用于测试和演示框架功能，无需真实 Agent 系统。
    支持脚本化场景: success / failure / transient / timeout。
    """

    def __init__(
        self,
        success_rate: float = 0.7,
        latency_range: tuple[float, float] = (0.1, 0.5),
        script: dict[str, list[str]] | None = None,
        timeout_duration: float = 10.0,
    ):
        """
        Args:
            success_rate: 随机模式下的模拟成功率 (0.0-1.0)
            latency_range: 模拟延迟范围 (秒)
            script: task_id → 行为序列 ("success"|"failure"|"transient"|"timeout"),
                    逐次调用消耗, 耗尽后重复最后一项
            timeout_duration: "timeout" 行为的挂起时长 (秒),
                              配合 EvalRunner(per_trial_timeout=...) 触发超时
        """
        self.success_rate = success_rate
        self.latency_range = latency_range
        self.script = script or {}
        self.timeout_duration = timeout_duration
        self.call_counts: dict[str, int] = {}

    def _next_behavior(self, task_id: str, call_index: int) -> str | None:
        """取该 task 指定调用的脚本行为 (无脚本返回 None = 随机模式)"""
        behaviors = self.script.get(task_id)
        if not behaviors:
            return None
        return behaviors[min(call_index, len(behaviors) - 1)]

    async def run(
        self,
        task: EvalTask,
    ) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
        """
        模拟 Agent 执行。

        Returns:
            trace_id, transcript, outcome
        """
        index = self.call_counts.get(task.id, 0)
        self.call_counts[task.id] = index + 1
        behavior = self._next_behavior(task.id, index)

        # 超时场景: 长时间挂起, 由框架的 per_trial_timeout 打断
        if behavior == "timeout":
            await asyncio.sleep(self.timeout_duration)

        # 模拟延迟
        latency = random.uniform(*self.latency_range)
        await asyncio.sleep(latency)

        # 瞬态错误场景: 框架按指数退避重试
        if behavior == "transient":
            raise TransientError(
                f"Mock transient failure for {task.id} (call {index + 1})"
            )

        # 生成 trace_id (编码 task id, 便于 MockTraceProvider 关联 spans)
        trace_id = f"trace_{task.id}_{uuid.uuid4().hex[:8]}"

        # 构建 transcript
        transcript = [
            {
                "role": "user",
                "content": task.prompt,
            },
            {
                "role": "assistant",
                "content": f"Mock response for task: {task.id}",
            },
        ]

        # 构建 outcome (模拟成功/失败)
        if behavior == "success":
            success = True
        elif behavior == "failure":
            success = False
        else:
            success = random.random() < self.success_rate

        outcome: dict[str, Any] = {
            "success": success,
            "files": {
                "output.py": f"# Mock output for {task.id}\ndef hello(): pass\n",
            },
            "artifacts": [
                {
                    "type": "code_file",
                    "id": f"art_{uuid.uuid4().hex[:8]}",
                    "content": f"# Generated code for {task.id}",
                }
            ] if success else [],
        }

        return trace_id, transcript, outcome


class MockTraceProvider:
    """模拟 TraceProvider

    可选按 task id 关联 span 数据: trace_id 形如 "trace_{task_id}_{suffix}"
    时返回 spans_by_task[task_id] (若已配置), 否则返回默认 spans。
    """

    def __init__(
        self,
        spans_by_task: dict[str, list[dict[str, Any]]] | None = None,
        default_spans: list[dict[str, Any]] | None = None,
    ):
        self.spans_by_task = spans_by_task or {}
        self.default_spans = default_spans or self._build_default_spans()

    @staticmethod
    def _build_default_spans() -> list[dict[str, Any]]:
        return [
            {
                "name": "agent.turn",
                "attributes": {
                    "agenthub.total_tokens": 150,
                },
                "start_time": "2026-08-29T10:00:00Z",
                "end_time": "2026-08-29T10:00:01Z",
                "status": {"status_code": "OK"},
            },
            {
                "name": "tool.call",
                "attributes": {
                    "agenthub.tool_name": "fs_write",
                    "agenthub.success": True,
                },
                "start_time": "2026-08-29T10:00:01Z",
                "end_time": "2026-08-29T10:00:02Z",
                "status": {"status_code": "OK"},
            },
        ]

    async def get_spans(self, trace_id: str) -> list[dict[str, Any]]:
        """返回模拟 span 数据 (优先按 task id 匹配)"""
        if trace_id.startswith("trace_"):
            remainder = trace_id[len("trace_"):]
            for task_id, spans in self.spans_by_task.items():
                if remainder == task_id or remainder.startswith(f"{task_id}_"):
                    return spans
        return self.default_spans

    async def get_trace_ids(
        self,
        filters: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> list[str]:
        return [f"trace_mock_{i}" for i in range(min(limit, 5))]
