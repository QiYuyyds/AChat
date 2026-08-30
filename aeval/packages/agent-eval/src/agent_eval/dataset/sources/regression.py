"""Regression sample extraction — close the eval→failure→extract→re-eval loop.

Extracts failed trials from a Run into regression items (D5):
- one item per failing TASK (first failed trial provides trace_id reference)
- prompt from the trial transcript's first message, falling back to the
  suite task's prompt; trials without any prompt source are skipped
- graders/env copied from the suite task so extracted items are re-runnable
- merging into a dataset dedupes on normalized prompt so regression samples
  don't balloon with repeated runs
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from agent_eval.core.types import EvalSuite, RunResult, TrialResult
from agent_eval.dataset.models import EvalDataset, EvalDatasetItem, SourceType, now_ms

# 回归样本数上限默认值 (D5)
DEFAULT_MAX_ITEMS = 50


def normalize_prompt(prompt: str) -> str:
    """prompt 归一化 (去首尾空白 + 折叠连续空白), 用于去重比较"""
    return re.sub(r"\s+", " ", prompt or "").strip()


@dataclass
class RegressionReport:
    """提取/合入报告"""

    run_id: str = ""
    failed_trials: int = 0
    extracted: int = 0
    skipped: list[dict[str, Any]] = field(default_factory=list)
    merged: int = 0
    merged_skipped: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "failed_trials": self.failed_trials,
            "extracted": self.extracted,
            "skipped": self.skipped,
            "merged": self.merged,
            "merged_skipped": self.merged_skipped,
        }


class RegressionExtractor:
    """从 Run 失败 trial 中提取回归样本"""

    def __init__(self, max_items: int = DEFAULT_MAX_ITEMS):
        self.max_items = max_items

    def extract_from_run(
        self,
        run: RunResult,
        suite: EvalSuite | None = None,
        max_items: int | None = None,
    ) -> tuple[list[EvalDatasetItem], RegressionReport]:
        """
        从一次 Run 的失败 trial 中提取回归条目。

        同一 task 的多个失败 trial 只提取一条 (prompt 相同,
        取首个失败 trial 的 trace_id 为引用)。

        Args:
            run: 评测运行结果
            suite: 可选的源 Suite — 提供任务 prompt 兜底与 graders/env 复用
            max_items: 本次提取上限 (默认取实例配置)

        Returns:
            (items, report)
        """
        cap = max_items if max_items is not None else self.max_items
        suite_tasks = {t.id: t for t in suite.tasks} if suite else {}

        report = RegressionReport(run_id=run.run_id)
        items: list[EvalDatasetItem] = []

        for task_id, trials in run.trials.items():
            failed = [t for t in trials if not t.success]
            report.failed_trials += len(failed)
            if not failed:
                continue

            # 同一 task 的多个失败 trial 只提取一条 (prompt 相同, 取首个失败 trial)
            first_failure: TrialResult = failed[0]
            prompt = self._prompt_of(first_failure, suite_tasks.get(task_id))
            if not prompt:
                report.skipped.append({
                    "task_id": task_id,
                    "trial_index": first_failure.trial_index,
                    "reason": "no prompt available (empty transcript, no suite task)",
                })
                continue

            if len(items) >= cap:
                report.skipped.append({
                    "task_id": task_id,
                    "trial_index": first_failure.trial_index,
                    "reason": f"max_items={cap} reached",
                })
                continue

            task = suite_tasks.get(task_id)
            error_note = (
                f" (error: {first_failure.error[:120]})"
                if first_failure.error
                else ""
            )
            items.append(
                EvalDatasetItem(
                    id=f"regression_{task_id}_{first_failure.trial_index}",
                    prompt=prompt,
                    description=f"Regression: {task_id}{error_note}",
                    graders=list(task.graders) if task else [],
                    env=dict(task.env) if task else {},
                    metadata={
                        "capabilities": [],
                        "regression": {
                            "run_id": run.run_id,
                            "task_id": task_id,
                            "trial_index": first_failure.trial_index,
                            "error": first_failure.error,
                        },
                    },
                    source_type=SourceType.REGRESSION,
                    source_ref=first_failure.trace_id or run.run_id,
                    created_at=now_ms(),
                )
            )

        report.extracted = len(items)
        return items, report

    @staticmethod
    def _prompt_of(trial: TrialResult, suite_task: Any = None) -> str:
        """trial prompt: transcript 首条消息优先, suite 任务 prompt 兜底"""
        if trial.transcript:
            first = trial.transcript[0]
            if isinstance(first, dict):
                content = first.get("content", "")
                if isinstance(content, str) and content.strip():
                    return content
        if suite_task is not None:
            return suite_task.prompt or ""
        return ""

    def merge_into_dataset(
        self,
        dataset: EvalDataset,
        items: list[EvalDatasetItem],
    ) -> tuple[EvalDataset, RegressionReport]:
        """
        将提取的条目合入数据集 — 按 prompt 归一化去重。

        数据集或新增条目中 prompt 归一化后重复的条目跳过 (记录于报告),
        避免回归样本随 run 次数膨胀。

        Returns:
            (更新后的 dataset 副本, 报告)
        """
        report = RegressionReport(run_id="")
        existing = {normalize_prompt(i.prompt) for i in dataset.items if i.prompt}

        merged_items = list(dataset.items)
        for raw in items:
            # 容忍 dict 形态 (API/JSON 透传场景)
            item = raw if isinstance(raw, EvalDatasetItem) else EvalDatasetItem(**raw)
            key = normalize_prompt(item.prompt)
            if not key or key in existing:
                report.merged_skipped.append({
                    "item_id": item.id,
                    "reason": "duplicate prompt (normalized) already in dataset",
                })
                continue
            existing.add(key)
            merged_items.append(item)
            report.merged += 1

        # 数据集内 ID 冲突时重命名 (不同 run 的同名回归条目)
        taken_ids = set()
        updated = []
        for item in merged_items:
            if item.id in taken_ids:
                suffix = 1
                while f"{item.id}_v{suffix}" in taken_ids:
                    suffix += 1
                item = item.model_copy(update={"id": f"{item.id}_v{suffix}"})
            taken_ids.add(item.id)
            updated.append(item)

        new_dataset = dataset.model_copy(update={
            "items": updated,
            "updated_at": now_ms(),
        })
        return new_dataset, report
