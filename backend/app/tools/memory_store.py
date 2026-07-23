"""memory_store tool — Agent-initiated long-term memory write.

Agents with ``memory_enabled=true`` receive this tool. It enforces three
layers of protection against abuse:

1. **Hard constraints (handler)**: category whitelist, importance floor,
   content length, per-run rate limiting (max 3 writes/run).
2. **Soft constraints (tool description)**: guidance on what is worth storing.
3. **Post-write cleanup (existing)**: ``store_classified`` cosine dedup +
   Consolidation decay/dedup/expire.

Writes go through ``LongTerm.store_classified()`` so they share the same
dedup logic as the background ``extract_ltm_memories`` path.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok
from app.tools.rate_limiter import _rate_limiter

logger = logging.getLogger(__name__)

_VALID_CATEGORIES = ("fact", "policy", "tool_failure")

_SLOT_BY_CATEGORY: dict[str, str] = {
    "fact": "recall_memory",
    "policy": "constraints",
    "tool_failure": "tool_state",
}

MAX_WRITES_PER_RUN = 3
MAX_CONTENT_LENGTH = 500


async def memory_store_handler(args: Any, ctx: ToolContext) -> ToolResult:
    """Store a long-term memory item with validation and rate limiting."""
    if not isinstance(args, dict):
        return err("memory_store requires a dict of arguments")

    # ── Defense 1: category whitelist ──────────────────────────────
    category = args.get("category", "")
    if category not in _VALID_CATEGORIES:
        return err(
            f"category must be one of: {', '.join(_VALID_CATEGORIES)}"
        )

    # ── Defense 2: importance floor ────────────────────────────────
    try:
        importance = float(args.get("importance", 0))
    except (TypeError, ValueError):
        return err("importance must be a number between 0.3 and 1.0")
    if importance < 0.3:
        return err("importance must be >= 0.3")
    if importance > 1.0:
        return err("importance must be <= 1.0")

    # ── Content length validation ──────────────────────────────────
    content = str(args.get("content", "")).strip()
    if not content or len(content) > MAX_CONTENT_LENGTH:
        return err(f"content must be 1-{MAX_CONTENT_LENGTH} characters")

    # ── Defense 3: per-run rate limiting ───────────────────────────
    rate_key = f"mem_writes:{ctx.agent_id}:{ctx.run_id}"
    count = await _rate_limiter.incr(rate_key, ttl=300)
    if count > MAX_WRITES_PER_RUN:
        return err(
            f"memory_store rate limit: max {MAX_WRITES_PER_RUN} writes per agent run"
        )

    # ── Access memory service ──────────────────────────────────────
    try:
        from app.main import _memory_service  # type: ignore[attr-defined]
    except ImportError:
        return err("Memory service not available")

    if _memory_service is None:
        return err("Memory service not initialized")

    ltm = _memory_service.ltm
    embed_fn = _memory_service._embed_fn  # noqa: SLF001

    # Compute embedding off the event loop (embed_fn is a blocking client)
    emb = None
    if embed_fn:
        try:
            emb = await asyncio.to_thread(embed_fn, content)
        except Exception as e:
            logger.warning("memory_store embedding failed: %s", e)

    # ── Store via store_classified (cosine dedup) ──────────────────
    tags = args.get("tags", [])
    if not isinstance(tags, list):
        tags = []

    slot_hint = _SLOT_BY_CATEGORY.get(category, "")

    try:
        inserted = await ltm.store_classified(
            content=content,
            importance=importance,
            emb=emb,
            category=category,
            tags=tags,
            slot_hint=slot_hint,
            scope="agent",
            agent_id=ctx.agent_id,
        )
    except Exception as e:
        logger.warning("memory_store store_classified failed: %s", e)
        return err(f"memory_store failed: {e}")

    # ── Return agent memory count (soft constraint feedback) ───────
    agent_mem_count = sum(
        1 for it in ltm.items
        if it.scope == "agent" and it.agent_id == ctx.agent_id
    )

    return ok({
        "stored": inserted,
        "agent_memory_count": agent_mem_count,
    })


memory_store_tool = ToolDef(
    name="memory_store",
    description=(
        "Store a long-term memory that will persist across conversations. "
        "ONLY store facts that are: "
        "(1) long-lived and stable (tech stack, project constraints), "
        "(2) affect future tasks (deployment failures, API quirks), "
        "(3) have long-term learning value. "
        "DO NOT store: temporary conversation details, "
        "information derivable from code, single-use operation results, "
        "or anything already in the agent's system prompt."
    ),
    parameters={
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": (
                    "Self-contained memory content. "
                    "Must be understandable without conversation context. "
                    "Example: 'User project uses React 19 + Next.js 16 with App Router'"
                ),
            },
            "category": {
                "type": "string",
                "enum": ["fact", "policy", "tool_failure"],
                "description": (
                    "fact=objective fact about user/project/environment, "
                    "policy=constraint or rule to follow, "
                    "tool_failure=lesson learned from a tool failure"
                ),
            },
            "importance": {
                "type": "number",
                "minimum": 0.3,
                "maximum": 1.0,
                "description": "0.3=minor, 0.5=normal, 0.8=critical, 1.0=identity-level",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional tags for filtering during recall.",
            },
        },
        "required": ["content", "category", "importance"],
    },
    handler=memory_store_handler,
)
