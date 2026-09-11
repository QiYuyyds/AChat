"""bash command approval store.

Port of src/server/pending-bash-commands.ts. Commands classified as needing
approval park here and emit ``bash_command.pending``; approve / reject / abort
resolve the awaiting tool and emit ``bash_command.resolved``. Singleton,
in-memory. The shared entry-map / resolver skeleton lives in
:mod:`app.services.pending_store_base`.
"""

from __future__ import annotations

from collections.abc import Callable

from app.schemas.dispatch import PendingBashCommand
from app.schemas.events import BashCommandPendingEvent, BashCommandResolvedEvent
from app.services.pending_store_base import BasePendingEntry, PendingStoreBase
from app.utils.clock import now_ms
from app.utils.ids import new_pending_bash_command_id

# decision -> {"approved": bool}
BashResolver = Callable[[dict], None]


class PendingBashCommandsStore(PendingStoreBase):
    def register(
        self,
        *,
        conversation_id: str,
        agent_id: str,
        run_id: str,
        command: str,
        cwd: str,
        reason: str,
        user_id: str | None = None,
    ) -> PendingBashCommand:
        created_at = now_ms()
        cmd = PendingBashCommand(
            id=new_pending_bash_command_id(),
            conversation_id=conversation_id,
            agent_id=agent_id,
            run_id=run_id,
            command=command,
            cwd=cwd,
            reason=reason,
            created_at=created_at,
        )
        self.register_entry(
            BasePendingEntry(payload=cmd, user_id=user_id),
            BashCommandPendingEvent(
                conversation_id=conversation_id,
                timestamp=created_at,
                pending_command=cmd,
            ),
        )
        return cmd

    def approve(self, pending_id: str) -> bool:
        if pending_id not in self._map:
            return False
        self._finalize(
            pending_id,
            resolver_payload={"approved": True},
            resolved_event=self._resolved_event(pending_id, approved=True),
        )
        return True

    def reject(self, pending_id: str) -> bool:
        if pending_id not in self._map:
            return False
        self._finalize(
            pending_id,
            resolver_payload={"approved": False},
            resolved_event=self._resolved_event(pending_id, approved=False),
        )
        return True

    def cancel(self, pending_id: str) -> None:
        # 分歧登记（design D3，待产品决策）：bash 的 cancel 会发 resolved SSE，
        # 与 writes / questions / mcp-calls / merge-conflicts 的静默 cancel 不同。
        # 此处用 emit_event=True 保留现状，不在此顺手统一。
        if pending_id not in self._map:
            return
        self._cancel(
            pending_id,
            resolver_payload={"approved": False},
            emit_event=True,
            resolved_event=self._resolved_event(pending_id, approved=False),
        )

    def _resolved_event(self, pending_id: str, *, approved: bool) -> BashCommandResolvedEvent:
        entry = self._map[pending_id]
        return BashCommandResolvedEvent(
            conversation_id=entry.payload.conversation_id,
            timestamp=now_ms(),
            pending_id=pending_id,
            approved=approved,
        )


pending_bash_commands = PendingBashCommandsStore()
