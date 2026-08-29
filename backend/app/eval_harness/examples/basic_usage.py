"""
Basic usage example for Aeval.

Demonstrates how to:
1. Define an EvalSuite
2. Create an EvalRunner
3. Run the suite
4. View results

Run this file to verify the framework works:
    python -m eval_harness.examples.basic_usage
"""

from __future__ import annotations

import asyncio

from eval_harness.core.runner import EvalRunner
from eval_harness.core.types import (
    EvalSuite,
    EvalTask,
    GraderConfig,
    GraderType,
    ScoreStrategy,
)
from eval_harness.examples.mock_runner import MockAgentRunner, MockTraceProvider
from eval_harness.storage import MemoryStorage


def create_demo_suite() -> EvalSuite:
    """创建一个演示评测套件"""
    return EvalSuite(
        name="Demo Eval Suite",
        description="A simple demo suite to verify the framework",
        tasks=[
            EvalTask(
                id="hello-world",
                description="Agent should say hello",
                prompt="Say hello to me",
                graders=[
                    GraderConfig(
                        type=GraderType.CODE,
                        name="code_based",
                        required=True,
                        config={
                            "checks": [
                                {
                                    "type": "contains",
                                    "value": "Mock response",
                                    "target": "transcript",
                                }
                            ],
                            "threshold": 1.0,
                        },
                    ),
                    GraderConfig(
                        type=GraderType.ARTIFACT,
                        name="artifact_check",
                        config={
                            "expected_type": "code_file",
                        },
                    ),
                ],
                max_trials=3,
                score_strategy=ScoreStrategy.HYBRID,
                score_threshold=0.5,
            ),
            EvalTask(
                id="file-creation",
                description="Agent should create a file",
                prompt="Create a hello.py file",
                graders=[
                    GraderConfig(
                        type=GraderType.STATE,
                        name="state_check",
                        required=True,
                        config={
                            "expectations": [
                                {
                                    "type": "file_contains",
                                    "path": "output.py",
                                    "value": "hello",
                                }
                            ],
                            "threshold": 1.0,
                        },
                    ),
                    GraderConfig(
                        type=GraderType.TOOL_CALLS,
                        name="tool_calls",
                        config={
                            "required_tools": ["fs_write"],
                        },
                    ),
                ],
                max_trials=3,
                score_strategy=ScoreStrategy.ALL_PASS,
            ),
        ],
    )


async def main():
    """Run the demo"""
    print("=" * 60)
    print("Aeval Framework — Demo Run")
    print("=" * 60)

    # 1. Create components
    agent_runner = MockAgentRunner(success_rate=0.8)
    trace_provider = MockTraceProvider()
    storage = MemoryStorage()

    # 2. Create EvalRunner
    runner = EvalRunner(
        agent_runner=agent_runner,
        trace_provider=trace_provider,
        storage=storage,
        concurrency=2,
    )

    # 3. Create suite
    suite = create_demo_suite()
    print(f"\nSuite: {suite.name}")
    print(f"Tasks: {len(suite.tasks)}")
    print(f"Trials per task: {suite.tasks[0].max_trials}")

    # 4. Run suite
    print("\nRunning evaluation...")
    result = await runner.run_suite(suite)

    # 5. Display results
    print("\n" + "=" * 60)
    print("Results")
    print("=" * 60)

    print(f"\nRun ID: {result.run_id}")
    print(f"Status: {result.status}")
    print(f"Duration: {result.duration_ms:.0f}ms" if result.duration_ms else "N/A")

    if result.summary:
        summary = result.summary
        print(f"\nTotal Tasks: {summary.total_tasks}")
        print(f"Total Trials: {summary.total_trials}")
        print(f"Average Score: {summary.avg_score:.2f}")

        if 1 in summary.pass_at_k:
            print(f"Pass@1: {summary.pass_at_k[1]:.2f}")
        if 3 in summary.pass_at_k:
            print(f"Pass@3: {summary.pass_at_k[3]:.2f}")
        if 1 in summary.pass_power_k:
            print(f"Pass^1: {summary.pass_power_k[1]:.2f}")
        if 3 in summary.pass_power_k:
            print(f"Pass^3: {summary.pass_power_k[3]:.2f}")

        if summary.failures:
            print(f"\nFailed Tasks: {', '.join(summary.failures)}")

        print("\nPer-Task Results:")
        for ts in summary.task_summaries:
            status = "✅" if not ts.failures else "❌"
            print(
                f"  {status} {ts.task_id}: "
                f"avg_score={ts.avg_score:.2f}, "
                f"trials={ts.total_trials}, "
                f"failures={len(ts.failures)}"
            )

    print("\n" + "=" * 60)
    print("Demo complete!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
