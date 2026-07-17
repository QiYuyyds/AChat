"""Lifecycle transitions and restart recovery for source intelligence."""

from __future__ import annotations

from typing import Any

from app.code_intelligence.metadata import (
    CodeIntelligenceMetadata,
    CodeIntelligenceStatus,
    MetadataStore,
)
from app.utils.clock import now_ms


class InvalidStateTransition(RuntimeError):
    """Raised when an operation does not apply to the current lifecycle state."""


ALLOWED_TRANSITIONS: dict[CodeIntelligenceStatus, set[CodeIntelligenceStatus]] = {
    "disabled": {"preparing_runtime"},
    "preparing_runtime": {"queued", "cancelling", "failed", "disabled"},
    "queued": {"indexing", "cancelling", "failed", "disabled"},
    "indexing": {"ready", "cancelling", "failed", "disabled"},
    "ready": {"syncing", "rebuilding", "disabled"},
    "syncing": {"ready", "cancelling", "failed", "disabled"},
    "rebuilding": {"ready", "cancelling", "failed", "disabled"},
    "cancelling": {"interrupted", "ready", "failed", "disabled"},
    "failed": {"preparing_runtime", "queued", "disabled"},
    "interrupted": {"preparing_runtime", "queued", "disabled"},
}

NONTERMINAL_STATES: frozenset[CodeIntelligenceStatus] = frozenset(
    {
        "preparing_runtime",
        "queued",
        "indexing",
        "syncing",
        "rebuilding",
        "cancelling",
    }
)

_UNSET = object()


def transition(
    store: MetadataStore,
    target: CodeIntelligenceStatus,
    *,
    phase: str | None | object = _UNSET,
    error: str | None | object = _UNSET,
    updates: dict[str, Any] | None = None,
) -> CodeIntelligenceMetadata:
    current = store.read()
    if target not in ALLOWED_TRANSITIONS[current.status]:
        raise InvalidStateTransition(
            f"Cannot transition source intelligence from {current.status} to {target}"
        )

    timestamp = now_ms()
    data = current.model_dump()
    data.update(updates or {})
    data.update(
        {
            "enabled": target != "disabled",
            "status": target,
            "updated_at": timestamp,
        }
    )
    if current.created_at is None:
        data["created_at"] = timestamp
    if target in {"indexing", "syncing", "rebuilding"}:
        data["started_at"] = timestamp
        data["completed_at"] = None
        data["progress_percent"] = 0
    if target in {"ready", "cancelling", "failed", "interrupted", "disabled"}:
        data["progress_percent"] = None
    if target == "ready":
        data["completed_at"] = timestamp
        data["last_sync_at"] = timestamp
        data["error"] = None
    if target == "disabled":
        data.update({"phase": None, "error": None, "started_at": None})
    if phase is not _UNSET:
        data["phase"] = phase
    if error is not _UNSET:
        data["error"] = error

    metadata = CodeIntelligenceMetadata.model_validate(data)
    store.write(metadata)
    return metadata


def recover_interrupted(
    store: MetadataStore,
    *,
    has_active_task: bool,
) -> CodeIntelligenceMetadata:
    current = store.read()
    if has_active_task or current.status not in NONTERMINAL_STATES:
        return current

    data = current.model_dump()
    data.update(
        {
            "enabled": True,
            "status": "interrupted",
            "phase": None,
            "updated_at": now_ms(),
            "error": "Source intelligence work was interrupted by application restart",
        }
    )
    recovered = CodeIntelligenceMetadata.model_validate(data)
    store.write(recovered)
    return recovered
