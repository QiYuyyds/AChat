"""Shared helper to await a pending approval/answer with cancellation.

The TS tools awaited a Promise that the pending store resolved, racing it
against the run's AbortSignal. The Python equivalent races an
:class:`asyncio.Future` (set by the store's resolver) against the run's
``cancel_event``. Used by fs_write / ask_user / bash approval flows.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")


async def await_pending_decision(
    *,
    attach_resolver: Callable[[Callable[[Any], None]], None],
    cancel: Callable[[], None],
    cancel_event: asyncio.Event,
    cancelled_value: T,
) -> Any | T:
    """Return the resolver's value, or ``cancelled_value`` if the run aborts.

    ``attach_resolver`` binds a one-arg callback to the pending entry; the store
    calls it (with the decision) on approve/reject/answer. ``cancel`` tears down
    the pending entry on abort.
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
        await asyncio.wait({fut, waiter}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        waiter.cancel()

    if fut.done():
        return fut.result()

    cancel()
    return cancelled_value
