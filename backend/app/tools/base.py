"""Tool system core types.

Port of src/server/tools/types.ts. See specs/01-core-entities.md §6 Tool.

  - ``ToolContext`` carries the per-call run context. The TS ``abortSignal``
    becomes an :class:`asyncio.Event` (``cancel_event``) — set when the run is
    aborted; tools that wait on user input race against it.
  - ``ToolResult`` mirrors the TS discriminated union ``{ok, value} | {ok, error}``.
  - ``ToolDef.parameters`` is a JSON Schema dict, used both for the LLM tool
    declaration and our own runtime validation.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolContext:
    conversation_id: str
    workspace_path: str
    agent_id: str
    run_id: str
    cancel_event: asyncio.Event


@dataclass
class ToolResult:
    ok: bool
    value: Any = None
    error: str | None = None


def ok(value: Any) -> ToolResult:
    return ToolResult(ok=True, value=value)


def err(error: str) -> ToolResult:
    return ToolResult(ok=False, error=error)


ToolHandler = Callable[[Any, ToolContext], Awaitable[ToolResult]]


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
