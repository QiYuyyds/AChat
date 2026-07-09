"""Pending MCP call approval store (ask-trust servers only).

Mirrors the pending_writes / pending_bash_commands pattern: each pending MCP
call holds a resolver that the waiting tool call attaches; approve / reject /
run-abort resolve it. Per-tool-per-conversation approval: after approval, the
same tool is exempt for the remainder of that conversation.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from app.schemas.events import McpCallPendingEvent, McpCallResolvedEvent, PendingMcpCall
from app.services.event_bus import event_bus
from app.utils.clock import now_ms
from app.utils.ids import new_pending_mcp_call_id

logger = logging.getLogger(__name__)

# decision -> {"approved": bool}
McpCallResolver = Callable[[dict], None]


@dataclass
class _PendingEntry:
    call: PendingMcpCall
    resolver: McpCallResolver | None = field(default=None)


class PendingMcpCallsStore:
    def __init__(self) -> None:
        self._map: dict[str, _PendingEntry] = {}
        # Per-conversation approved/rejected tool names: conversation_id → set
        self._approved: dict[str, set[str]] = {}
        self._rejected: dict[str, set[str]] = {}

    def register(
        self,
        *,
        conversation_id: str,
        agent_id: str,
        run_id: str,
        tool_name: str,
        args: dict,
        server_trust: str,
    ) -> PendingMcpCall:
        created_at = now_ms()
        call = PendingMcpCall(
            id=new_pending_mcp_call_id(),
            conversation_id=conversation_id,
            agent_id=agent_id,
            run_id=run_id,
            tool_name=tool_name,
            args=args,
            server_trust=server_trust,
            created_at=created_at,
        )
        self._map[call.id] = _PendingEntry(call=call)

        event_bus.publish(
            McpCallPendingEvent(
                conversation_id=conversation_id,
                timestamp=created_at,
                pending_call=call,
            )
        )
        return call

    def attach_resolver(self, pending_id: str, resolver: McpCallResolver) -> None:
        entry = self._map.get(pending_id)
        if entry is not None:
            entry.resolver = resolver

    def get(self, pending_id: str) -> PendingMcpCall | None:
        entry = self._map.get(pending_id)
        return entry.call if entry else None

    def list_by_conversation(self, conversation_id: str) -> list[PendingMcpCall]:
        calls = [
            e.call for e in self._map.values()
            if e.call.conversation_id == conversation_id
        ]
        calls.sort(key=lambda c: c.created_at)
        return calls

    def is_approved(self, conversation_id: str, tool_name: str) -> bool:
        return tool_name in self._approved.get(conversation_id, set())

    def is_rejected(self, conversation_id: str, tool_name: str) -> bool:
        return tool_name in self._rejected.get(conversation_id, set())

    def approve(self, pending_id: str) -> bool:
        entry = self._map.get(pending_id)
        if entry is None:
            return False
        conv_id = entry.call.conversation_id
        tool_name = entry.call.tool_name
        self._approved.setdefault(conv_id, set()).add(tool_name)
        self._finalize(pending_id, approved=True)
        return True

    def reject(self, pending_id: str) -> bool:
        entry = self._map.get(pending_id)
        if entry is None:
            return False
        conv_id = entry.call.conversation_id
        tool_name = entry.call.tool_name
        self._rejected.setdefault(conv_id, set()).add(tool_name)
        self._finalize(pending_id, approved=False)
        return True

    def cancel(self, pending_id: str) -> None:
        """Run-abort path: resolve as not-approved without emitting an SSE event."""
        entry = self._map.get(pending_id)
        if entry is None:
            return
        if entry.resolver is not None:
            entry.resolver({"approved": False})
        del self._map[pending_id]

    def _finalize(self, pending_id: str, *, approved: bool) -> None:
        entry = self._map.get(pending_id)
        if entry is None:
            return
        if entry.resolver is not None:
            entry.resolver({"approved": approved})
        del self._map[pending_id]
        event_bus.publish(
            McpCallResolvedEvent(
                conversation_id=entry.call.conversation_id,
                timestamp=now_ms(),
                pending_id=pending_id,
                approved=approved,
            )
        )


pending_mcp_calls = PendingMcpCallsStore()
