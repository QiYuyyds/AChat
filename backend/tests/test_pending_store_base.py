"""Unit tests for the shared PendingStoreBase skeleton (generalize-pending-store).

Drives the base class directly through a minimal entry factory; the per-store
semantics (event payloads, decision methods) are covered by the pending API and
tools suites. Event objects are arbitrary sentinels — the base only routes them
through the event bus, never inspects them.
"""

import asyncio
from dataclasses import dataclass

from app.services.event_bus import event_bus
from app.services.pending_store_base import BasePendingEntry, PendingStoreBase


@dataclass
class _FakePayload:
    id: str
    conversation_id: str
    created_at: int


def _entry(
    payload_id: str,
    *,
    conversation_id: str = "conv_x",
    created_at: int = 0,
    resolver=None,
) -> BasePendingEntry:
    return BasePendingEntry(
        payload=_FakePayload(
            id=payload_id, conversation_id=conversation_id, created_at=created_at
        ),
        resolver=resolver,
    )


async def test_register_parks_entry_publishes_pending_event():
    store = PendingStoreBase()
    pending_event = object()
    async with event_bus.subscribe() as queue:
        payload = store.register_entry(_entry("p1"), pending_event)
        assert payload.id == "p1"
        assert store.get("p1") is payload
        received = await asyncio.wait_for(queue.get(), timeout=1)
        assert received is pending_event


async def test_finalize_fires_resolver_before_drop_then_publishes():
    store = PendingStoreBase()
    seen: list[tuple[dict, bool]] = []

    def resolver(decision: dict) -> None:
        # The waiter must still be resolvable while it is being resolved —
        # locks the resolve-then-drop ordering the six stores shared.
        seen.append((decision, store.get("p1") is not None))

    store.register_entry(_entry("p1", resolver=resolver), object())
    resolved_event = object()
    async with event_bus.subscribe() as queue:
        store._finalize("p1", resolved_event=resolved_event, resolver_payload={"applied": True})
    assert seen == [({"applied": True}, True)]
    assert store.get("p1") is None
    received = await asyncio.wait_for(queue.get(), timeout=1)
    assert received is resolved_event


async def test_finalize_without_payload_skips_resolver():
    """Dispatch-plans shape: the decision method resolved the waiter itself."""
    store = PendingStoreBase()
    seen: list = []
    store.register_entry(_entry("p1", resolver=seen.append), object())
    resolved_event = object()
    async with event_bus.subscribe() as queue:
        store._finalize("p1", resolved_event=resolved_event)
    assert seen == []
    assert store.get("p1") is None
    received = await asyncio.wait_for(queue.get(), timeout=1)
    assert received is resolved_event


async def test_finalize_missing_id_is_noop():
    store = PendingStoreBase()
    store._finalize("missing", resolved_event=object(), resolver_payload={"applied": False})
    assert store.get("missing") is None


async def test_cancel_silent_by_default():
    store = PendingStoreBase()
    seen: list = []
    store.register_entry(_entry("p1", resolver=seen.append), object())
    async with event_bus.subscribe() as queue:
        store._cancel("p1", resolver_payload={"applied": False})
    assert seen == [{"applied": False}]
    assert store.get("p1") is None
    assert queue.empty()


async def test_cancel_emit_event_publishes_resolved_event():
    """Bash / dispatch-plans shape: cancel also emits the resolved SSE."""
    store = PendingStoreBase()
    seen: list = []
    store.register_entry(_entry("p1", resolver=seen.append), object())
    resolved_event = object()
    async with event_bus.subscribe() as queue:
        store._cancel(
            "p1",
            resolver_payload={"approved": False},
            emit_event=True,
            resolved_event=resolved_event,
        )
    assert seen == [{"approved": False}]
    assert store.get("p1") is None
    received = await asyncio.wait_for(queue.get(), timeout=1)
    assert received is resolved_event


async def test_cancel_missing_id_is_noop():
    store = PendingStoreBase()
    store._cancel("missing", resolver_payload=None)
    assert store.get("missing") is None


async def test_attach_resolver_binds_entry_and_ignores_unknown_id():
    store = PendingStoreBase()
    store.register_entry(_entry("p1"), object())
    seen: list = []
    store.attach_resolver("p1", seen.append)
    store.attach_resolver("missing", seen.append)  # must not raise
    store._finalize("p1", resolved_event=object(), resolver_payload={"ok": True})
    assert seen == [{"ok": True}]


async def test_list_by_conversation_filters_and_sorts_by_created_at():
    store = PendingStoreBase()
    store.register_entry(_entry("p2", conversation_id="conv_x", created_at=20), object())
    store.register_entry(_entry("p1", conversation_id="conv_x", created_at=10), object())
    store.register_entry(_entry("p3", conversation_id="conv_other", created_at=5), object())

    items = store.list_by_conversation("conv_x")
    assert [item.id for item in items] == ["p1", "p2"]
    assert [item.id for item in store.list_by_conversation("conv_other")] == ["p3"]
