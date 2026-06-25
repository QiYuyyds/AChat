"""Agent fs_write approval store (review mode only).

Port of src/server/pending-writes.ts. Each pending write holds a resolver the
waiting tool call attaches; approve / reject / run-abort resolve it. Approving
(unless ``skip_write``) writes the file, then emits ``fs_write.resolved``.

Module-level singleton (mirrors the TS globalThis singleton). In-memory: a
restart drops all pending writes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from app.db.models import Workspace
from app.schemas.dispatch import PendingWrite
from app.schemas.events import FsWritePendingEvent, FsWriteResolvedEvent
from app.services.event_bus import event_bus
from app.services.fs_service import write_file_in_workspace
from app.utils.clock import now_ms
from app.utils.ids import new_pending_write_id

logger = logging.getLogger(__name__)

# decision -> {"applied": bool}
WriteResolver = Callable[[dict], None]


@dataclass
class _PendingEntry:
    write: PendingWrite
    workspace: Workspace
    skip_write: bool
    resolver: WriteResolver | None = field(default=None)


class PendingWritesStore:
    def __init__(self) -> None:
        self._map: dict[str, _PendingEntry] = {}

    def register(
        self,
        *,
        conversation_id: str,
        agent_id: str,
        run_id: str,
        path: str,
        absolute_path: str,
        old_content: str | None,
        new_content: str,
        workspace: Workspace,
        skip_write: bool = False,
    ) -> PendingWrite:
        created_at = now_ms()
        write = PendingWrite(
            id=new_pending_write_id(),
            conversation_id=conversation_id,
            agent_id=agent_id,
            run_id=run_id,
            path=path,
            absolute_path=absolute_path,
            old_content=old_content,
            new_content=new_content,
            created_at=created_at,
        )
        self._map[write.id] = _PendingEntry(
            write=write, workspace=workspace, skip_write=skip_write
        )

        event_bus.publish(
            FsWritePendingEvent(
                conversation_id=conversation_id,
                timestamp=created_at,
                pending_write=write,
            )
        )
        return write

    def attach_resolver(self, pending_id: str, resolver: WriteResolver) -> None:
        entry = self._map.get(pending_id)
        if entry is not None:
            entry.resolver = resolver

    def get(self, pending_id: str) -> PendingWrite | None:
        entry = self._map.get(pending_id)
        return entry.write if entry else None

    def list_by_conversation(self, conversation_id: str) -> list[PendingWrite]:
        writes = [
            e.write for e in self._map.values() if e.write.conversation_id == conversation_id
        ]
        writes.sort(key=lambda w: w.created_at)
        return writes

    def approve(self, pending_id: str) -> bool:
        entry = self._map.get(pending_id)
        if entry is None:
            return False
        if not entry.skip_write:
            try:
                write_file_in_workspace(
                    entry.workspace, entry.write.path, entry.write.new_content
                )
            except Exception:  # noqa: BLE001 - surface failure to the LLM, still close
                logger.exception("[pending_writes] approve write failed")
                self._finalize(pending_id, applied=False)
                return False
        self._finalize(pending_id, applied=True)
        return True

    def reject(self, pending_id: str) -> bool:
        if pending_id not in self._map:
            return False
        self._finalize(pending_id, applied=False)
        return True

    def cancel(self, pending_id: str) -> None:
        """Run-abort path: resolve as not-applied without emitting an SSE event."""
        entry = self._map.get(pending_id)
        if entry is None:
            return
        if entry.resolver is not None:
            entry.resolver({"applied": False})
        del self._map[pending_id]

    def _finalize(self, pending_id: str, *, applied: bool) -> None:
        entry = self._map.get(pending_id)
        if entry is None:
            return
        if entry.resolver is not None:
            entry.resolver({"applied": applied})
        del self._map[pending_id]
        event_bus.publish(
            FsWriteResolvedEvent(
                conversation_id=entry.write.conversation_id,
                timestamp=now_ms(),
                pending_id=pending_id,
                applied=applied,
            )
        )


pending_writes = PendingWritesStore()
