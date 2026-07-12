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
    hook_registry: Any = None  # HookRegistry | None, injected by AgentRunner
    # O8: stores the last post_tool_use HookResult so _run_react_loop can
    # check for inject actions without dispatching post_tool_use a second time.
    last_post_hook_result: Any = None
    # O8: tool_names for the current run, passed to HookContext for skill checks
    tool_names: list[str] | None = None
    # universal subagent dispatch: recursion depth (0 = top-level)
    dispatch_depth: int = 0
    # universal subagent dispatch: effective loop mode ("solo" / "coordinated" / "subagent")
    dispatch_mode: str = "solo"


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
