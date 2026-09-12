"""Shared skeleton for the six pending-approval stores.

writes / questions / bash-commands / dispatch-plans / mcp-calls /
merge-conflicts were the same template six times over — an entry map, register /
attach_resolver / get / list_by_conversation, a finalize that fires the resolver
and publishes the resolved event, and a run-abort cancel. This module is that
template once (OpenSpec change ``generalize-pending-store``); the six
``app/services/pending_*.py`` modules keep only their payload construction,
event construction, decision methods and store-specific state.

Two shapes the base deliberately absorbs:

- **Resolver timing (design D2)**: ``_finalize`` fires the attached resolver
  only when ``resolver_payload`` is passed (writes / bash / mcp / questions /
  merge shape). Dispatch-plans resolves the waiter itself and calls
  ``_finalize`` payload-less, which just drops the entry and publishes the event.
- **Cancel SSE divergence (design D3, 待产品决策)**: bash-commands and
  dispatch-plans ``cancel`` emit a resolved SSE event today while the other
  four stores cancel silently. ``cancel(emit_event=...)`` preserves that split
  instead of unifying it — unifying is a product decision, not a refactor.

All stores stay in-memory singletons: a restart drops every pending entry.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.services.event_bus import event_bus

# Sentinel: "no resolver payload supplied" — distinct from an explicit ``None``
# (questions' cancel resolves with ``None`` on purpose).
_UNSET = object()


@dataclass
class BasePendingEntry:
    """A parked approval item: its payload plus the waiting tool's resolver."""

    payload: Any
    # kw_only so subclasses can append required fields after these defaults.
    user_id: str | None = field(default=None, kw_only=True)
    resolver: Callable[[Any], None] | None = field(default=None, kw_only=True)


class PendingStoreBase:
    """In-memory registry of pending items awaiting a user decision."""

    def __init__(self) -> None:
        # Values are the stores' own entry types (all BasePendingEntry
        # subclasses); kept as Any per design D1's accepted payload: Any
        # trade-off — subclasses annotate their public methods concretely.
        self._map: dict[str, Any] = {}

    # ── registration ─────────────────────────────────────────────────────────
    def register_entry(self, entry: BasePendingEntry, pending_event: Any) -> Any:
        """Park the entry, publish its pending event, return the payload."""
        self._map[entry.payload.id] = entry
        event_bus.publish(pending_event, user_id=entry.user_id)
        return entry.payload

    # ── lookups ──────────────────────────────────────────────────────────────
    def attach_resolver(self, pending_id: str, resolver: Callable[[Any], None]) -> None:
        """Bind the waiting tool call's resolver to a parked entry."""
        entry = self._map.get(pending_id)
        if entry is not None:
            entry.resolver = resolver

    def get(self, pending_id: str) -> Any:
        entry = self._map.get(pending_id)
        return entry.payload if entry else None

    def list_by_conversation(self, conversation_id: str) -> list[Any]:
        items = [
            entry.payload
            for entry in self._map.values()
            if entry.payload.conversation_id == conversation_id
        ]
        items.sort(key=lambda item: item.created_at)
        return items

    # ── resolution ───────────────────────────────────────────────────────────
    def _finalize(
        self,
        pending_id: str,
        *,
        resolved_event: Any,
        resolver_payload: Any = _UNSET,
    ) -> None:
        """Drop the entry and publish its resolved event.

        With ``resolver_payload`` the attached resolver fires before the entry
        is dropped (writes / bash / mcp / questions / merge shape); without it
        only the event is published — the decision method already resolved the
        waiter itself (dispatch-plans shape, design D2).
        """
        entry = self._map.get(pending_id)
        if entry is None:
            return
        if resolver_payload is not _UNSET and entry.resolver is not None:
            entry.resolver(resolver_payload)
        del self._map[pending_id]
        event_bus.publish(resolved_event, user_id=entry.user_id)

    def _cancel(
        self,
        pending_id: str,
        *,
        resolver_payload: Any,
        emit_event: bool = False,
        resolved_event: Any = None,
    ) -> None:
        """Run-abort path: fire the resolver, drop the entry — no user decision.

        ``resolver_payload`` is the decision handed to the waiting tool (e.g.
        ``{"applied": False}``; questions passes ``None`` on purpose). The
        ``emit_event`` flag keeps today's divergence (design D3, 待产品决策):
        bash-commands and dispatch-plans cancel publish a resolved SSE event,
        the other four stores stay silent. Unifying that split is a product
        decision, deliberately out of scope for this refactor.
        """
        entry = self._map.get(pending_id)
        if entry is None:
            return
        if entry.resolver is not None:
            entry.resolver(resolver_payload)
        del self._map[pending_id]
        if emit_event:
            event_bus.publish(resolved_event, user_id=entry.user_id)
