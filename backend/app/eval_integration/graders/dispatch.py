"""AChatDispatchGrader — orchestrator 派发质量 (任务 2.3)。

子任务数 / 最大派发深度 / 完成率, score=完成率, threshold 默认 0.8。

真实 span 事实 (§14.1 已核对):
    - 派发 span 名为 ``tool.dispatch``, 属性 ``agenthub.child_agent_id`` /
      ``agenthub.dispatch_depth`` / ``agenthub.dispatch_visibility``;
      **无** per-dispatch 成功标记属性
    - 子 run 的 ``agent.finalize`` span 携带 ``agenthub.run_id`` 与
      ``agenthub.stop_reason`` (``complete`` = 干净收尾)
    - 本地库 ``agent_runs`` 表有 parent_run_id + status, 为完成率权威来源

完成率信号优先级:
    1. dispatch span 的 ``agenthub.success`` 属性 (显式契约; 合成 spans 与
       未来插桩直接可用)
    2. 本地库 agent_runs: parent_run_id ∈ trial run_ids 的子 run 状态
    3. 子 run 的 agent.finalize span stop_reason == "complete" 计数
    4. 均不可得 → 完成率 0 并在 explanation 说明 (不静默)
"""

from __future__ import annotations

import logging
from typing import Any

from eval_harness.core.contract import EvalContext
from eval_harness.core.types import EvalTask, GraderResult, GraderType, TrialResult

logger = logging.getLogger(__name__)


def _span_attr(span: dict[str, Any], key: str, default: Any = None) -> Any:
    return (span.get("attributes") or {}).get(key, default)


def _dispatch_spans(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [s for s in spans if "tool.dispatch" in str(s.get("name", ""))]


class AChatDispatchGrader:
    """派发质量评分器 — 子任务数 / 最大派发深度 / 完成率。"""

    name = "achat_dispatch"

    def __init__(self, *, db_lookup: Any = None):
        """
        Args:
            db_lookup: 自定义异步 ``(parent_run_ids: list[str]) ->
                dict[child_run_id, status]`` (测试注入); 缺省查本地库
        """
        self._db_lookup = db_lookup

    async def grade(
        self,
        trial: TrialResult,
        spans: list[dict[str, Any]],
        task: EvalTask,
        context: EvalContext | None = None,
    ) -> GraderResult:
        config = task.get_grader_config(self.name)
        threshold = float(config.get("threshold", 0.8))

        dispatches = _dispatch_spans(spans)
        n_subtasks = len(dispatches)
        max_depth = max(
            (int(_span_attr(s, "agenthub.dispatch_depth", 0) or 0) for s in dispatches),
            default=0,
        )

        if n_subtasks == 0:
            return GraderResult(
                grader_name=self.name,
                grader_type=GraderType.CUSTOM,
                score=0.0,
                passed=False,
                explanation="No dispatch found",
                details={"n_subtasks": 0, "max_depth": 0, "completion_rate": 0.0},
            )

        completion_rate, completion_source = await self._completion_rate(
            trial, spans, dispatches
        )

        return GraderResult(
            grader_name=self.name,
            grader_type=GraderType.CUSTOM,
            score=completion_rate,
            passed=completion_rate >= threshold,
            explanation=(
                f"Dispatch: {n_subtasks} subtasks, depth={max_depth}, "
                f"completed {completion_rate:.0%} (source: {completion_source})"
            ),
            details={
                "n_subtasks": n_subtasks,
                "max_depth": max_depth,
                "completion_rate": completion_rate,
                "completion_source": completion_source,
                "threshold": threshold,
            },
        )

    async def _completion_rate(
        self,
        trial: TrialResult,
        spans: list[dict[str, Any]],
        dispatches: list[dict[str, Any]],
    ) -> tuple[float, str]:
        # 1. 显式 success 属性 (设计契约 §14.2)
        if any("agenthub.success" in (s.get("attributes") or {}) for s in dispatches):
            completed = sum(
                1 for s in dispatches if _span_attr(s, "agenthub.success", False)
            )
            return completed / len(dispatches), "span:agenthub.success"

        parent_run_ids = [
            str(r) for r in (trial.outcome.get("run_ids") or []) if r
        ]

        # 2. 本地库子 run 状态 (真实链路权威)
        statuses = await self._child_run_statuses(parent_run_ids)
        if statuses:
            completed = sum(1 for st in statuses.values() if st == "complete")
            return completed / len(statuses), "db:agent_runs"

        # 3. 子 run finalize spans (排除顶层 run 自身的 finalize)
        parent_ids = set(parent_run_ids)
        finalize_spans = [
            s for s in spans
            if "agent.finalize" in str(s.get("name", ""))
            and _span_attr(s, "agenthub.run_id") not in parent_ids
        ]
        if finalize_spans:
            completed = sum(
                1 for s in finalize_spans
                if _span_attr(s, "agenthub.stop_reason", "") == "complete"
            )
            return completed / len(finalize_spans), "span:agent.finalize"

        return 0.0, "unknown"

    async def _child_run_statuses(self, parent_run_ids: list[str]) -> dict[str, str]:
        if not parent_run_ids:
            return {}
        if self._db_lookup is not None:
            return dict(await self._db_lookup(parent_run_ids))
        try:
            from sqlalchemy import select

            from app.db.engine import get_local_db
            from app.db.models import AgentRun

            async with get_local_db() as db:
                rows = (
                    await db.execute(
                        select(AgentRun.id, AgentRun.status).where(
                            AgentRun.parent_run_id.in_(parent_run_ids)
                        )
                    )
                ).all()
            return {row[0]: row[1] for row in rows}
        except Exception as e:  # noqa: BLE001 - DB 不可用时退回 span 信号
            logger.debug("dispatch grader DB lookup unavailable: %s", e)
            return {}
