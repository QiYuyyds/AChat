"""bash command approval store.

Port of src/server/pending-bash-commands.ts. Commands classified as needing
approval park here and emit ``bash_command.pending``; approve / reject / abort
resolve the awaiting tool and emit ``bash_command.resolved``. Singleton,
in-memory.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.schemas.dispatch import PendingBashCommand
from app.schemas.events import BashCommandPendingEvent, BashCommandResolvedEvent
from app.services.event_bus import event_bus
from app.utils.clock import now_ms
from app.utils.ids import new_pending_bash_command_id

# decision -> {"approved": bool}
BashResolver = Callable[[dict], None]


@dataclass
class _PendingEntry:
    command: PendingBashCommand
    resolver: BashResolver | None = field(default=None)


class PendingBashCommandsStore:
    def __init__(self) -> None:
        self._map: dict[str, _PendingEntry] = {}

    def register(
        self,
        *,
        conversation_id: str,
        agent_id: str,
        run_id: str,
        command: str,
        cwd: str,
        reason: str,
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
        self._map[cmd.id] = _PendingEntry(command=cmd)
        event_bus.publish(
            BashCommandPendingEvent(
                conversation_id=conversation_id,
                timestamp=created_at,
                pending_command=cmd,
            )
        )
        return cmd

    def attach_resolver(self, pending_id: str, resolver: BashResolver) -> None:
        entry = self._map.get(pending_id)
        if entry is not None:
            entry.resolver = resolver

    def get(self, pending_id: str) -> PendingBashCommand | None:
        entry = self._map.get(pending_id)
        return entry.command if entry else None

    def list_by_conversation(self, conversation_id: str) -> list[PendingBashCommand]:
        cmds = [
            e.command
            for e in self._map.values()
            if e.command.conversation_id == conversation_id
        ]
        cmds.sort(key=lambda c: c.created_at)
        return cmds

    def approve(self, pending_id: str) -> bool:
        if pending_id not in self._map:
            return False
        self._finalize(pending_id, approved=True)
        return True

    def reject(self, pending_id: str) -> bool:
        if pending_id not in self._map:
            return False
        self._finalize(pending_id, approved=False)
        return True

    def cancel(self, pending_id: str) -> None:
        if pending_id not in self._map:
            return
        self._finalize(pending_id, approved=False)

    def _finalize(self, pending_id: str, *, approved: bool) -> None:
        entry = self._map.get(pending_id)
        if entry is None:
            return
        if entry.resolver is not None:
            entry.resolver({"approved": approved})
        del self._map[pending_id]
        event_bus.publish(
            BashCommandResolvedEvent(
                conversation_id=entry.command.conversation_id,
                timestamp=now_ms(),
                pending_id=pending_id,
                approved=approved,
            )
        )


pending_bash_commands = PendingBashCommandsStore()
