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
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_eval.core.suite import load_suite  # noqa: E402

SUITE_PATH = Path(__file__).resolve().parent.parent / "eval_suites" / "first-suite.yaml"


async def main(import_only: bool) -> int:
    suite = load_suite(SUITE_PATH)
    print(f"[aeval] suite loaded: {suite.name} v{suite.version} "
          f"({len(suite.tasks)} tasks)")
    if import_only:
        for task in suite.tasks:
            print(f"  - {task.id}: {task.description}")
        return 0

    from app.db import engine as engine_mod
    from app.eval_integration.config import create_aeval_runner

    # 真实链路需要 backend DB（agent 执行会话/消息落库）——与 app 启动等价
    await engine_mod.init_db()

    runner = await create_aeval_runner()
    print("[aeval] runner assembled — running against real AChat Agent ...")
    result = await runner.run_suite(suite)

    print(f"\n[aeval] run {result.run_id} status={result.status} "
          f"duration={result.duration_ms:.0f}ms")
    if result.error:
        print(f"[aeval] run error: {result.error}")

    summary = result.summary
    if summary is not None:
        # pass@k / avg_score 在新口径下可能是 None (有效样本不足 = 证据不足,
        # 不是 0 分)。直接 :.3f 格式化 None 会抛 TypeError。
        def _fmt(value: Any, spec: str = "{:.3f}") -> str:
            return "证据不足" if value is None else spec.format(value)

        print(f"[aeval] pass@1={_fmt(summary.pass_at_k.get(1), '{:.1%}')} "
              f"avg_score={_fmt(summary.avg_score)} "
              f"(valid={summary.valid_trials} invalid={summary.invalid_trials} "
              f"pending={summary.pending_trials})")
        for ts in summary.task_summaries:
            print(f"  - {ts.task_id}: trials={ts.total_trials} "
                  f"pass@1={_fmt(ts.pass_at_k.get(1), '{:.1%}')} "
                  f"avg={_fmt(ts.avg_score)} "
                  f"valid={ts.valid_trials} invalid={ts.invalid_trials} "
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
