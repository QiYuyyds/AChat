"""Merge conflict approval store (Layer 3 human approval).

Mirrors the pending_writes pattern: register a conflict, attach a resolver,
wait for the user's decision via the API. In-memory: a restart drops all
pending conflicts.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.events import MergeConflictPendingEvent, MergeConflictResolvedEvent
from app.services.event_bus import event_bus
from app.utils.clock import now_ms
from app.utils.ids import new_pending_merge_conflict_id

logger = logging.getLogger(__name__)

MergeConflictResolver = Callable[[dict[str, Any]], None]


class PendingMergeConflict(BaseModel):
    """A pending merge conflict awaiting user decision."""

    id: str
    conversation_id: str = Field(alias="conversationId")
    task_id: str = Field(alias="taskId")
    conflict_files: list[str] = Field(alias="conflictFiles")
    workspace_path: str = Field(alias="workspacePath")
    created_at: int = Field(alias="createdAt")

    model_config = {"populate_by_name": True}


@dataclass
class _PendingEntry:
    conflict: PendingMergeConflict
    resolver: MergeConflictResolver | None = field(default=None)
    user_id: str | None = None


class PendingMergeConflictsStore:
    def __init__(self) -> None:
        self._map: dict[str, _PendingEntry] = {}

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
        self._map[conflict.id] = _PendingEntry(
            conflict=conflict, user_id=user_id
        )

        event_bus.publish(
            MergeConflictPendingEvent(
                conversation_id=conversation_id,
                timestamp=created_at,
                pending_id=conflict.id,
                task_id=task_id,
                conflict_files=conflict_files,
                workspace_path=workspace_path,
            ),
            user_id=user_id,
        )
        return conflict

    def attach_resolver(
        self, pending_id: str, resolver: MergeConflictResolver
    ) -> None:
        entry = self._map.get(pending_id)
        if entry is not None:
            entry.resolver = resolver

    def get(self, pending_id: str) -> PendingMergeConflict | None:
        entry = self._map.get(pending_id)
        return entry.conflict if entry else None

    def list_by_conversation(self, conversation_id: str) -> list[PendingMergeConflict]:
        conflicts = [
            e.conflict
            for e in self._map.values()
            if e.conflict.conversation_id == conversation_id
        ]
        conflicts.sort(key=lambda c: c.created_at)
        return conflicts

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
        if entry.resolver is not None:
            entry.resolver(decision)
        del self._map[pending_id]
        event_bus.publish(
            MergeConflictResolvedEvent(
                conversation_id=entry.conflict.conversation_id,
                timestamp=now_ms(),
                pending_id=pending_id,
                resolution_strategy=decision.get("resolution_strategy", "manual"),
                resolved_files=decision.get("resolved_files", []),
            ),
            user_id=entry.user_id,
        )
        return True

    def cancel(self, pending_id: str) -> None:
        """Run-abort path: resolve as abandoned without emitting an SSE event."""
        entry = self._map.get(pending_id)
        if entry is None:
            return
        if entry.resolver is not None:
            entry.resolver({"action": "abandon", "resolution_strategy": "abandoned"})
        del self._map[pending_id]


pending_merge_conflicts = PendingMergeConflictsStore()
