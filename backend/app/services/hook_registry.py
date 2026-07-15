"""HookRegistry — lifecycle hook registration and dispatch.

Provides a registry for async handler functions that are invoked at specific
lifecycle points during an agent run. Handlers are registered at startup and
dispatched in priority order (lower number = earlier execution).

See openspec/changes/p1-react-loop-hooks/specs/lifecycle-hooks/spec.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class HookEvent(str, Enum):
    """Lifecycle event types for hook dispatch."""

    PRE_TURN = "pre_turn"
    POST_TURN = "post_turn"
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    ON_STOP = "on_stop"
    ON_ERROR = "on_error"
    ON_RUN_START = "on_run_start"
    ON_RUN_END = "on_run_end"
    ON_MESSAGE_END = "on_message_end"
    ON_TASK_VERIFIED = "on_task_verified"


@dataclass
class HookContext:
    """Carries event-specific data to hook handlers."""

    event: HookEvent
    run_id: str
    agent_id: str
    conversation_id: str
    turn_number: int = 0
    # pre_tool_use / post_tool_use
    tool_name: str | None = None
    args: Any = None
    call_id: str | None = None
    # post_tool_use
    result: Any = None
    is_error: bool = False
    # post_turn
    message_id: str | None = None
    tool_calls: list[dict] | None = None
    finish_reason: str | None = None
    usage: Any = None
    # post_turn: full messages list for checkpoint saving
    messages: list[dict] | None = None
    # on_error
    error: str | None = None
    # inject action
    injected_events: list[Any] = field(default_factory=list)
    # on_task_verified
    task_id: str | None = None
    task_kind: str | None = None
    verification_result: str | None = None  # "passed" | "failed"
    failure_reason: str | None = None
    # tool_names for the current run (for skill_auto_activator load_skill check)
    tool_names: list[str] | None = None


@dataclass
class HookResult:
    """Control-flow action returned by a hook handler."""

    action: str  # "allow" | "deny" | "modify" | "inject"
    data: Any = None  # deny: reason; modify: modified_data; inject: events


@dataclass
class HookEntry:
    """A registered hook with priority and name."""

    handler: Any  # Callable[[HookContext], Awaitable[HookResult | None]]
    priority: int
    name: str


class HookRegistry:
    """Registers and dispatches lifecycle hooks.

    Handlers are registered at startup (synchronous registration). Dispatch is
    async — handlers may involve IO (DB writes, LLM calls). On handler exception,
    the error is logged and the default 'allow' action is used.
    """

    def __init__(self) -> None:
        self._hooks: dict[HookEvent, list[HookEntry]] = {}

    def register(
        self,
        event: HookEvent,
        handler: Any,
        priority: int = 10,
        name: str = "",
    ) -> None:
        """Register a handler for an event at the given priority (lower = earlier)."""
        entry = HookEntry(handler=handler, priority=priority, name=name or handler.__name__)
        self._hooks.setdefault(event, []).append(entry)

    async def dispatch(self, context: HookContext) -> HookResult:
        """Dispatch all handlers for the context's event in priority order.

        Returns the effective HookResult (last non-allow result wins for deny;
        for modify, the latest modified data is used). If no handlers are
        registered, returns HookResult(action="allow").
        """
        entries = self._hooks.get(context.event, [])
        if not entries:
            return HookResult(action="allow")

        effective = HookResult(action="allow")
        for entry in sorted(entries, key=lambda e: e.priority):
            try:
                result = await entry.handler(context)
                if result is None:
                    continue
                if result.action == "deny":
                    return result
                if result.action == "modify" or result.action == "inject":
                    effective = result
                # allow: keep current effective result
            except Exception:
                logger.warning(
                    "Hook '%s' for %s raised an exception",
                    entry.name,
                    context.event.value,
                    exc_info=True,
                )
        return effective

    def has_handlers(self, event: HookEvent) -> bool:
        """Check if any handlers are registered for the event."""
        return bool(self._hooks.get(event))
