"""Minimal Aeval example — programmatic API, fully offline.

Runs the bundled suite against the built-in MockAgentRunner and prints the
run summary. No external services required.

Usage:
    python examples/minimal/runner.py

Prefer the CLI? This suite also runs as-is with the default mock runner:
    eval-suite run examples/minimal/suite.yaml
"""

import asyncio

from agent_eval.core.runner import EvalRunner
from agent_eval.core.suite import load_suite
from agent_eval.examples.mock_runner import MockAgentRunner, MockTraceProvider
from agent_eval.storage.sqlite import SqliteStorage

SUITE_PATH = "examples/minimal/suite.yaml"
DB_PATH = "./aeval.db"


async def main() -> None:
    suite = load_suite(SUITE_PATH)
    print(f"Loaded suite: {suite.name} v{suite.version} ({len(suite.tasks)} tasks)")

    storage = SqliteStorage(DB_PATH)
    await storage.initialize()

    runner = EvalRunner(
        agent_runner=MockAgentRunner(success_rate=1.0, latency_range=(0.0, 0.01)),
        trace_provider=MockTraceProvider(),
        storage=storage,
    )
    run = await runner.run_suite(suite)

    print(f"\nRun {run.run_id}: {run.status}")
    print(f"Pass@1: {run.summary.pass_at_k[1]:.1%}")
    print(f"Pass^1: {run.summary.pass_power_k[1]:.1%}")
    print(f"Avg score: {run.summary.avg_score:.4f}")
    if run.summary.failures:
        print("Failures:", ", ".join(run.summary.failures))
    print(f"\nResults persisted to {DB_PATH} — inspect with:")
    print(f"  eval-suite show {run.run_id}")
    print("  eval-suite list runs")


if __name__ == "__main__":
    asyncio.run(main())
