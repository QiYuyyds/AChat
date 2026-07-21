"""Shared helpers for guide agent management tools."""

from __future__ import annotations

import time
from typing import Any

from app.schemas.events import GuideSideEffectEvent
from app.services.event_bus import event_bus
from app.tools.base import ToolContext


def emit_guide_side_effect(
    *,
    ctx: ToolContext,
    target: str,
    action: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Emit a guide_side_effect SSE event to the guide conversation's SSE bucket.

    The event is published with ctx.user_id so only the owning user's SSE
    subscriber receives it.
    """
    event = GuideSideEffectEvent(
        conversationId=ctx.conversation_id,
        timestamp=int(time.time() * 1000),
        target=target,
        action=action,
        payload=payload,
    )
    event_bus.publish(event, user_id=ctx.user_id)
