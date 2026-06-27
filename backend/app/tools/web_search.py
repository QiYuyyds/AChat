"""web_search tool — query the public web via the Tavily Search API.

Opt-in per agent (strategy B): an agent gains web search only when ``web_search``
is in its ``tool_names``; it is never auto-injected. Custom agents only — SDK
adapters force ``tool_names`` empty and use their own tool sets.

Key comes from ``TAVILY_API_KEY`` (config / .env env-fallback). Read-only external
call: no approval gate (parity with rag_search). The request races the run cancel
signal and a timeout, and results are bounded to protect the model context.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.config import get_settings
from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok

_TAVILY_URL = "https://api.tavily.com/search"
_TIMEOUT_SECONDS = 15.0
_MAX_RESULTS = 5
_CONTENT_MAX_CHARS = 2000


class _Args(BaseModel):
    query: str = Field(min_length=1)


_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["query"],
    "properties": {
        "query": {
            "type": "string",
            "description": "The search query. Use natural-language questions or keywords.",
        },
    },
}


async def _tavily_request(api_key: str, query: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        resp = await client.post(
            _TAVILY_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "query": query,
                "max_results": _MAX_RESULTS,
                "include_answer": True,
            },
        )
        resp.raise_for_status()
        return resp.json()


async def _handler(args: Any, ctx: ToolContext) -> ToolResult:
    try:
        parsed = _Args.model_validate(args)
    except ValidationError as e:
        return err(f"Invalid args: {e}")

    api_key = get_settings().tavily_api_key
    if not api_key:
        return err("Tavily API key not configured (set TAVILY_API_KEY in the environment)")

    request_task = asyncio.ensure_future(_tavily_request(api_key, parsed.query))
    cancel_task = asyncio.ensure_future(ctx.cancel_event.wait())
    try:
        done, _ = await asyncio.wait(
            {request_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        # Always drop the waiters we own so nothing leaks — covers both the
        # cooperative cancel_event path and an external task.cancel() (CancelledError).
        cancel_task.cancel()
        if not request_task.done():
            request_task.cancel()

    if request_task not in done:
        return err("web_search cancelled")

    try:
        data = request_task.result()
    except httpx.HTTPStatusError as e:
        return err(f"Tavily request failed: HTTP {e.response.status_code}")
    except httpx.HTTPError as e:
        return err(f"Tavily request failed: {e}")
    except Exception as e:  # noqa: BLE001 - surface any failure to the LLM
        return err(f"web_search failed: {e}")

    raw_results = data.get("results") or []
    results = [
        {
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "content": (r.get("content") or "")[:_CONTENT_MAX_CHARS],
            "score": r.get("score"),
        }
        for r in raw_results[:_MAX_RESULTS]
    ]
    return ok({"answer": data.get("answer"), "results": results})


web_search_tool = ToolDef(
    name="web_search",
    description=(
        "Search the public web for current information using Tavily. Use this when you "
        "need up-to-date facts, news, documentation, or anything not in the workspace or "
        "knowledge base. Returns a synthesized answer (when available) and up to 5 results "
        "with title, URL, and a content snippet."
    ),
    parameters=_PARAMETERS,
    handler=_handler,
)
