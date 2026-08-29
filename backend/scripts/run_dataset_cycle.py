"""数据集闭环验收脚本 (change ③ 任务 6.2)。

真实链路: 手动编写数据集 → to-suite → 真实 Agent run → 回归样本提取 →
数据集升版 minor。与 run_first_suite.py 同款约束:
EVAL_AGENT_ID 必须配置 (EVAL_HARNESS_ENABLED 不影响 in-process 装配)。

用法 (backend 目录下):
    python scripts/run_dataset_cycle.py              # 完整闭环
    python scripts/run_dataset_cycle.py --dry-run    # 仅构建数据集与 suite (不跑 Agent)

每一步打印可核对的 ID (dataset_id / run_id / 版本), 结果全部落库
(<data_dir>/aeval.db), 可经 /api/eval/datasets/* 与 /runs/{run_id} 复查。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

DATASET_DEFINITION = {
    "name": "dataset-cycle-acceptance",
    "description": "闭环验收: 手动编写 → run → 回归提取 → 升版",
    "tags": ["acceptance"],
    "items": [
        {
            "id": "health-answer",
            "prompt": "你好，请简单介绍一下你自己能做什么。",
            "description": "基础问答冒烟",
            "graders": [
                {"type": "model", "name": "model_based",
                 "config": {"rubric": "回答清晰介绍了自身能力", "threshold": 0.7}},
            ],
            "metadata": {"capabilities": ["qa"]},
        },
    ],
}


async def main(dry_run: bool) -> int:
    from eval_harness.dataset.quality import DatasetQualityChecker
    from eval_harness.dataset.sources.manual import parse_dataset_payload
    from eval_harness.dataset.sources.regression import RegressionExtractor
    from eval_harness.dataset.version import DatasetVersionManager

    # 1. 手动编写数据集 (进程内定义 → 同一校验路径入库)
    dataset = parse_dataset_payload(DATASET_DEFINITION, source="run_dataset_cycle.py")
    quality = DatasetQualityChecker().check(dataset)
    print(f"[1/5] dataset built: {dataset.id} v{dataset.version} "
          f"items={len(dataset.items)} quality_ok={quality.ok}")
    if not quality.ok:
        for issue in quality.errors:
            print(f"      ERROR {issue.message}")
        return 1

    # 2. to-suite
    suite = dataset.to_suite()
    print(f"[2/5] suite converted: {suite.name} tasks={len(suite.tasks)} "
          f"metadata={json.dumps(suite.metadata, ensure_ascii=False)}")

    if dry_run:
        print("[dry-run] stopping before real Agent run")
        return 0

    from eval_integration.config import create_aeval_runner

    runner = await create_aeval_runner()
    print("[aeval] runner assembled — judge LLM "
          f"{'configured' if runner.llm_fn else 'NOT configured (metric graders will error)'}")

    # 3. 真实 Agent run
    result = await runner.run_suite(suite)
    print(f"[3/5] run finished: {result.run_id} status={result.status} "
          f"failures={result.summary.failures if result.summary else 'n/a'}")

    # 4. 回归样本提取并合入 (合入非空 → 升版 minor)
    suite_def = await runner.storage.get_suite(suite.name)
    items, extract_report = RegressionExtractor().extract_from_run(result, suite_def)
    dataset, merge_report = RegressionExtractor().merge_into_dataset(dataset, items)
    if merge_report.merged > 0:
        dataset = DatasetVersionManager.bump(
            dataset, "minor", change_note=f"regression merge from run {result.run_id}")
    await runner.storage.datasets.save_dataset(dataset)
    print(f"[4/5] regression merge: extracted={extract_report.extracted} "
          f"merged={merge_report.merged} dataset_version={dataset.version}")

    # 5. 变更记录
    for entry in dataset.change_log:
        print(f"[5/5] change_log: {entry['change_type']} → {entry['version']} "
              f"({entry['note']})")
    if not dataset.change_log:
        print("[5/5] no version bump (nothing merged — run had no failures)")

    print("\n[done] 复查入口:")
    print(f"  GET /api/eval/datasets/{dataset.id}")
    print(f"  GET /api/eval/runs/{result.run_id}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="不跑真实 Agent")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.dry_run)))
