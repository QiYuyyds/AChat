"""Merge conflict approval store (Layer 3 human approval).

Mirrors the pending_writes pattern: register a conflict, attach a resolver,
wait for the user's decision via the API. In-memory: a restart drops all
pending conflicts. The shared entry-map / resolver skeleton lives in
:mod:`app.services.pending_store_base`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.schemas.dispatch import PendingMergeConflict  # noqa: F401  re-export
from app.schemas.events import MergeConflictPendingEvent, MergeConflictResolvedEvent
from app.services.pending_store_base import BasePendingEntry, PendingStoreBase
from app.utils.clock import now_ms
from app.utils.ids import new_pending_merge_conflict_id

# decision -> {"action": ..., "file_contents": ...}
MergeConflictResolver = Callable[[dict[str, Any]], None]


class PendingMergeConflictsStore(PendingStoreBase):
    def register(
        self,
        *,
        conversation_id: str,
        task_id: str,
        conflict_files: list[str],
        workspace_path: str,
        user_id: str | None = None,
    ) -> PendingMergeConflict:
        created_at = now_ms()
        conflict = PendingMergeConflict(
            id=new_pending_merge_conflict_id(),
            conversation_id=conversation_id,
            task_id=task_id,
            conflict_files=conflict_files,
            workspace_path=workspace_path,
            created_at=created_at,
        )
        self.register_entry(
            BasePendingEntry(payload=conflict, user_id=user_id),
            MergeConflictPendingEvent(
                conversation_id=conversation_id,
                timestamp=created_at,
                pending_id=conflict.id,
                task_id=task_id,
                conflict_files=conflict_files,
                workspace_path=workspace_path,
            ),
        )
        return conflict

    def resolve(
        self,
        pending_id: str,
        decision: dict[str, Any],
    ) -> bool:
        """Resolve a pending merge conflict with the user's decision.

        decision keys: action ("ours"|"theirs"|"edit"|"abandon"),
        file_contents (dict[str, str] | None, only for "edit").
        """
        entry = self._map.get(pending_id)
        if entry is None:
            return False
        # The resolved event's fields are extracted from the decision dict —
        # this extraction stays here (subclass event construction), not in the
        # base finalize.
        self._finalize(
            pending_id,
            resolver_payload=decision,
            resolved_event=MergeConflictResolvedEvent(
                conversation_id=entry.payload.conversation_id,
                timestamp=now_ms(),
                pending_id=pending_id,
                resolution_strategy=decision.get("resolution_strategy", "manual"),
                resolved_files=decision.get("resolved_files", []),
            ),
        )
        return True

    def cancel(self, pending_id: str) -> None:
        """Run-abort path: resolve as abandoned without emitting an SSE event."""
        self._cancel(
            pending_id,
            resolver_payload={"action": "abandon", "resolution_strategy": "abandoned"},
        )


pending_merge_conflicts = PendingMergeConflictsStore()
