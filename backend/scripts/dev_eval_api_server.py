"""Standalone Aeval API server for dashboard live verification (task 6.5).

Runs the eval harness sub-app with a slow MockAgentRunner — no AChat DB
needed. Suites/runs live in MemoryStorage; POST a suite then start a run.

Usage:
    python scripts/dev_eval_api_server.py   # http://127.0.0.1:8900
"""

from __future__ import annotations

import uvicorn
from agent_eval.api.app import create_app
from agent_eval.core.runner import EvalRunner
from agent_eval.examples.mock_runner import MockAgentRunner, MockTraceProvider
from agent_eval.storage.memory import MemoryStorage
from fastapi import FastAPI

runner = EvalRunner(
    agent_runner=MockAgentRunner(success_rate=1.0, latency_range=(3.0, 5.0)),
    trace_provider=MockTraceProvider(),
    storage=MemoryStorage(),
)

inner = create_app(runner=runner)

# 与 main.py 相同的挂载前缀 (dashboard rewrites 把 /api/eval/* 原样转发)
app = FastAPI()
app.mount("/api/eval", inner)


if __name__ == "__main__":
    print("Aeval dev API on http://127.0.0.1:8900/api/eval (slow mock runner, 3-5s/trial)")
    uvicorn.run(app, host="127.0.0.1", port=8900, log_level="warning")
