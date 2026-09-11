"""Pending MCP call approval store (ask-trust servers only).

Mirrors the pending_writes / pending_bash_commands pattern: each pending MCP
call holds a resolver that the waiting tool call attaches; approve / reject /
run-abort resolve it. Per-tool-per-conversation approval: after approval, the
same tool is exempt for the remainder of that conversation. The shared
entry-map / resolver skeleton lives in
:mod:`app.services.pending_store_base`.
"""

from __future__ import annotations

from collections.abc import Callable

from app.schemas.events import McpCallPendingEvent, McpCallResolvedEvent, PendingMcpCall
from app.services.pending_store_base import BasePendingEntry, PendingStoreBase
from app.utils.clock import now_ms
from app.utils.ids import new_pending_mcp_call_id

# decision -> {"approved": bool}
McpCallResolver = Callable[[dict], None]


class PendingMcpCallsStore(PendingStoreBase):
    def __init__(self) -> None:
        super().__init__()
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
        user_id: str | None = None,
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
        self.register_entry(
            BasePendingEntry(payload=call, user_id=user_id),
            McpCallPendingEvent(
                conversation_id=conversation_id,
                timestamp=created_at,
                pending_call=call,
            ),
        )
        return call

    def is_approved(self, conversation_id: str, tool_name: str) -> bool:
        return tool_name in self._approved.get(conversation_id, set())

    def is_rejected(self, conversation_id: str, tool_name: str) -> bool:
        return tool_name in self._rejected.get(conversation_id, set())

    def approve(self, pending_id: str) -> bool:
        entry = self._map.get(pending_id)
        if entry is None:
            return False
        call = entry.payload
        # Decision memory is mcp-specific: record before finalizing so the
        # exemption survives the entry being dropped.
        self._approved.setdefault(call.conversation_id, set()).add(call.tool_name)
        self._finalize(
            pending_id,
            resolver_payload={"approved": True},
            resolved_event=self._resolved_event(pending_id, approved=True),
        )
        return True

    def reject(self, pending_id: str) -> bool:
        entry = self._map.get(pending_id)
        if entry is None:
            return False
        call = entry.payload
        self._rejected.setdefault(call.conversation_id, set()).add(call.tool_name)
        self._finalize(
            pending_id,
            resolver_payload={"approved": False},
            resolved_event=self._resolved_event(pending_id, approved=False),
        )
        return True

    def cancel(self, pending_id: str) -> None:
        """Run-abort path: resolve as not-approved without emitting an SSE event."""
        self._cancel(pending_id, resolver_payload={"approved": False})

    def _resolved_event(self, pending_id: str, *, approved: bool) -> McpCallResolvedEvent:
        entry = self._map[pending_id]
        return McpCallResolvedEvent(
            conversation_id=entry.payload.conversation_id,
            timestamp=now_ms(),
            pending_id=pending_id,
            approved=approved,
        )


pending_mcp_calls = PendingMcpCallsStore()
