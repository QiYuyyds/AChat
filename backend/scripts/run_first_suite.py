"""真实链路验收脚本 (任务 4.2): 导入并运行 first-suite.yaml。

用法 (backend 目录下, 需 EVAL_HARNESS_ENABLED=true + EVAL_AGENT_ID 已配置):
    python scripts/run_first_suite.py            # 运行并打印汇总
    python scripts/run_first_suite.py --import-only

流程: create_aeval_runner() 装配真实 AChat AgentRunner → 加载 YAML →
POST 等价的 in-process run → 打印 pass@k / 任务结果 / 失败明细。
结果同时落库 (<data_dir>/aeval.db), 可经 GET /api/eval/runs/{run_id} 查询。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from eval_harness.core.suite import load_suite  # noqa: E402

SUITE_PATH = Path(__file__).resolve().parent.parent / "eval_suites" / "first-suite.yaml"


async def main(import_only: bool) -> int:
    suite = load_suite(SUITE_PATH)
    print(f"[aeval] suite loaded: {suite.name} v{suite.version} "
          f"({len(suite.tasks)} tasks)")
    if import_only:
        for task in suite.tasks:
            print(f"  - {task.id}: {task.description}")
        return 0

    from eval_integration.config import create_aeval_runner

    runner = await create_aeval_runner()
    print("[aeval] runner assembled — running against real AChat Agent ...")
    result = await runner.run_suite(suite)

    print(f"\n[aeval] run {result.run_id} status={result.status} "
          f"duration={result.duration_ms:.0f}ms")
    if result.error:
        print(f"[aeval] run error: {result.error}")

    summary = result.summary
    if summary is not None:
        print(f"[aeval] pass@1={summary.pass_at_k.get(1)} "
              f"avg_score={summary.avg_score:.3f}")
        for ts in summary.task_summaries:
            print(f"  - {ts.task_id}: trials={ts.total_trials} "
                  f"pass@1={ts.pass_at_k.get(1)} avg={ts.avg_score:.3f} "
                  f"failures={ts.failures}")

    failed = [tid for tid, trials in result.trials.items()
              for t in trials if not t.success]
    if failed:
        print(f"[aeval] failed tasks: {sorted(set(failed))}")
        for tid in sorted(set(failed)):
            for t in result.trials[tid]:
                if not t.success and t.error:
                    print(f"  - {tid} trial {t.trial_index}: {t.error}")

    print(f"[aeval] query via API: GET /api/eval/runs/{result.run_id}")
    return 0 if result.status == "completed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--import-only", action="store_true",
                        help="仅校验/导入 YAML, 不真实运行")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.import_only)))
