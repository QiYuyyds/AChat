"""HTTP Agent adapter pattern for Aeval — generalized from AChat's
AChatAgentRunner (see the AChat repo for the full wiring: workspace
sandboxing, event-bus completion detection, trace-id bridging, env seeding).

Contract assumed here (an HTTP eval API you control):

    POST {base_url}/agent/run
        body: {"prompt": str, "env": dict}
        resp: {"run_id": str}

    GET {base_url}/agent/runs/{run_id}
        resp: {
            "status": "running" | "completed" | "failed",
            "transcript": [{"role": ..., "content": ...}, ...],
            "outcome": {...},        # final workspace/environment state
            "trace_id": str,         # OTel trace id ("" if unavailable)
        }

Transient network failures are wrapped in TransientError so the framework
retries with exponential backoff; anything else fails the trial immediately.

This file is a template: copy it and adapt the endpoints to your agent.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

from agent_eval.core.contract import TransientError
from agent_eval.core.types import EvalTask


class HTTPAgentRunner:
    """AgentRunner implementation that drives an HTTP agent endpoint."""

    def __init__(
        self,
        base_url: str | None = None,
        poll_interval: float = 1.0,
        timeout: float = 300.0,
        max_transient_retries: int = 3,
    ):
        self.base_url = (base_url or os.environ.get("AEVAL_AGENT_URL", "")).rstrip("/")
        if not self.base_url:
            raise ValueError(
                "HTTP agent endpoint missing — set AEVAL_AGENT_URL "
                "(e.g. http://127.0.0.1:8000) or pass base_url."
            )
        self.poll_interval = poll_interval
        self.timeout = timeout
        self.max_transient_retries = max_transient_retries

    async def run(self, task: EvalTask) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
        """Execute one trial: submit the prompt, poll to completion."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            run_id = await self._submit(client, task)
            return await self._poll(client, run_id)

    async def _submit(self, client: httpx.AsyncClient, task: EvalTask) -> str:
        last_error: Exception | None = None
        for _ in range(self.max_transient_retries + 1):
            try:
                resp = await client.post(
                    f"{self.base_url}/agent/run",
                    json={"prompt": task.prompt, "env": task.env},
                )
                resp.raise_for_status()
                return resp.json()["run_id"]
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_error = e
                await asyncio.sleep(1.0)
        raise TransientError(f"agent submit failed: {last_error}") from last_error

    async def _poll(
        self, client: httpx.AsyncClient, run_id: str
    ) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
        elapsed = 0.0
        while True:
            resp = await client.get(f"{self.base_url}/agent/runs/{run_id}")
            resp.raise_for_status()
            data = resp.json()

            if data["status"] == "completed":
                return data.get("trace_id", ""), data.get("transcript", []), data.get("outcome", {})
            if data["status"] == "failed":
                raise RuntimeError(f"agent run failed: {data.get('error', 'unknown')}")

            if elapsed >= self.timeout:
                raise TimeoutError(f"agent run {run_id} did not finish in {self.timeout}s")
            await asyncio.sleep(self.poll_interval)
            elapsed += self.poll_interval


def create_runner() -> HTTPAgentRunner:
    """Entry-point factory (agent_eval.runners group) or direct use."""
    return HTTPAgentRunner()
