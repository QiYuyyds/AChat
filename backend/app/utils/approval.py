"""Shared helper to await a pending approval/answer with cancellation + timeout.

The TS tools awaited a Promise that the pending store resolved, racing it
against the run's AbortSignal. The Python equivalent races an
:class:`asyncio.Future` (set by the store's resolver) against the run's
``cancel_event`` — and now also against a configurable timeout. Used by
fs_write / fs_edit / ask_user / bash approval / MCP approval / dispatch plan
review / merge-conflict flows.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")


class ApprovalTimeout:
    """Singleton sentinel: the approval wait hit its timeout.

    Distinct from every ``cancelled_value`` so call sites can map it to a
    timeout tool error instead of an abort/rejection message. A timeout NEVER
    auto-approves — the entry is discarded via ``cancel``.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "<APPROVAL_TIMEOUT>"


APPROVAL_TIMEOUT = ApprovalTimeout()


async def await_pending_decision(
    *,
    attach_resolver: Callable[[Callable[[Any], None]], None],
    cancel: Callable[[], None],
    cancel_event: asyncio.Event,
    cancelled_value: T,
    timeout_s: float | None = None,
) -> Any | T:
    """Return the resolver's value, ``cancelled_value`` on run abort, or the
    :data:`APPROVAL_TIMEOUT` sentinel when no decision arrives in time.

    ``attach_resolver`` binds a one-arg callback to the pending entry; the store
    calls it (with the decision) on approve/reject/answer. ``cancel`` tears down
    the pending entry — on abort *and* on timeout, so the entry never outlives
    the wait. ``timeout_s=None`` keeps the legacy unbounded wait.
    """
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[Any] = loop.create_future()

    def _resolve(value: Any) -> None:
        if not fut.done():
            fut.set_result(value)

    attach_resolver(_resolve)

    if cancel_event.is_set():
        cancel()
        return cancelled_value

    waiter = asyncio.ensure_future(cancel_event.wait())
    try:
        await asyncio.wait(
            {fut, waiter}, timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        waiter.cancel()

    if fut.done():
        return fut.result()

    cancel()
    if cancel_event.is_set():
        return cancelled_value
    if timeout_s is not None:
        return APPROVAL_TIMEOUT
    return cancelled_value
