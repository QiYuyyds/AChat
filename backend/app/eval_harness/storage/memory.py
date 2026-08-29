"""
In-memory storage implementation.

Useful for testing and short-lived runs.
Data is lost when the process exits.

Usage:
    storage = MemoryStorage()
    await storage.save_run(run_result)
"""

from __future__ import annotations

import copy
from typing import Any

from eval_harness.core.types import EvalSuite, RunResult
from eval_harness.dataset.storage import MemoryDatasetStorage


class MemoryStorage:
    """内存存储实现 — 用于测试"""

    def __init__(self):
        self._runs: dict[str, RunResult] = {}
        self._suites: dict[str, EvalSuite] = {}
        self._human_score_requests: list[dict[str, Any]] = []
        # 数据集存储组合暴露 (与 runs/suites 分表, 见 dataset/storage.py)
        self.datasets = MemoryDatasetStorage()

    # ── Run 操作 ──

    async def save_run(self, run: RunResult) -> None:
        """保存运行结果"""
        self._runs[run.run_id] = copy.deepcopy(run)

    async def get_run(self, run_id: str) -> RunResult | None:
        """获取运行结果"""
        run = self._runs.get(run_id)
        return copy.deepcopy(run) if run else None

    async def list_runs(
        self, suite_name: str | None = None, limit: int = 50
    ) -> list[RunResult]:
        """列出运行历史"""
        runs = list(self._runs.values())
        if suite_name:
            runs = [r for r in runs if r.suite_name == suite_name]
        runs.sort(key=lambda r: r.started_at, reverse=True)
        return runs[:limit]

    async def delete_run(self, run_id: str) -> bool:
        """删除运行结果"""
        if run_id in self._runs:
            del self._runs[run_id]
            return True
        return False

    # ── Suite 操作 ──

    async def save_suite(self, suite: EvalSuite) -> None:
        """保存评测套件"""
        self._suites[suite.name] = copy.deepcopy(suite)

    async def get_suite(self, name: str) -> EvalSuite | None:
        """获取评测套件"""
        suite = self._suites.get(name)
        return copy.deepcopy(suite) if suite else None

    async def list_suites(self) -> list[EvalSuite]:
        """列出所有评测套件"""
        return list(self._suites.values())

    async def delete_suite(self, name: str) -> bool:
        """删除评测套件"""
        if name in self._suites:
            del self._suites[name]
            return True
        return False

    # ── 人工评分请求 ──

    async def save_human_score_request(self, request: dict[str, Any]) -> None:
        """保存人工评分请求 (HumanGrader pending 语义)"""
        self._human_score_requests.append(dict(request))

    async def list_human_score_requests(
        self, run_id: str | None = None
    ) -> list[dict[str, Any]]:
        """列出人工评分请求"""
        if run_id is None:
            return [dict(r) for r in self._human_score_requests]
        return [
            dict(r) for r in self._human_score_requests if r.get("run_id") == run_id
        ]
