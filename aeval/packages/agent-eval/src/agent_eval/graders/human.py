"""
Human grader — routes scoring to human experts with pending semantics.

Semantics (design decision D5): ``grade()`` returns IMMEDIATELY with a pending
result (score=0, passed=False, ``details.status="pending"``, confidence=0) and
persists a score request to Storage. The run completes normally; pending
trials are listed separately in the summary and excluded from pass rates.
Scores come back later via ``POST /api/eval/runs/{run_id}/human-scores``.

Config schema:
    {
        "threshold": 0.7,       # optional, defaults to task.score_threshold
        "instructions": "...",  # optional guidance shown to the reviewer
    }
"""

from __future__ import annotations

import time
from typing import Any

from agent_eval.core.contract import EvalContext
from agent_eval.core.types import EvalTask, GraderResult, GraderType, TrialResult


class HumanGrader:
    """
    人工评分器 — pending 语义, 不阻塞 run 完成。

    可选注入 Storage (EvalRunner 构造时自动注入): 评分请求通过
    ``save_human_score_request`` 落库, 供 Dashboard (change ②) 拉取。
    """

    name = "human"

    def __init__(self, storage: Any | None = None):
        self.storage = storage

    async def grade(
        self,
        trial: TrialResult,
        spans: list[dict[str, Any]],
        task: EvalTask,
        context: EvalContext | None = None,
    ) -> GraderResult:
        config = task.get_grader_config(self.name)

        request: dict[str, Any] = {
            "run_id": context.run_id if context else "",
            "task_id": task.id,
            "trial_index": trial.trial_index,
            "grader_name": self.name,
            "prompt": task.prompt,
            "instructions": config.get("instructions", ""),
            "transcript": trial.transcript,
            "outcome": trial.outcome,
            "created_at": time.time() * 1000,
        }

        # 评分请求写入 Storage (自定义 Storage 未实现该可选方法时跳过)
        if self.storage is not None:
            save = getattr(self.storage, "save_human_score_request", None)
            if save is not None:
                await save(request)

        return GraderResult(
            grader_name=self.name,
            grader_type=GraderType.CUSTOM,
            score=0.0,
            passed=False,
            explanation="等待人工评分",
            details={
                "status": "pending",
                "request": request,
            },
            confidence=0.0,
        )
