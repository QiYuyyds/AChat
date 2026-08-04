"""Memory management API — user-facing CRUD for LTM, Preferences, and Session Memory.

Provides transparency: users can view, edit, and delete the memories that
agents have stored. All endpoints are read/write to PostgreSQL and sync the
in-memory caches. Embeddings are never exposed in API responses.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import String, cast, select

from app.auth.dependencies import get_current_user
from app.auth.ownership import verify_conversation_ownership
from app.db.engine import get_local_db, get_remote_db
from app.db.models import ContextSummary, Conversation, LongTermMemory, User, UserPreference
from app.memory.consolidation import Item

logger = logging.getLogger(__name__)

router = APIRouter()


# ─── Helpers ───────────────────────────────────────────────────────────────


def _get_memory_service():
    """Get the module-level MemoryService instance (wired at app startup)."""
    from app.main import _memory_service  # type: ignore[attr-defined]
    return _memory_service


def _ltm_row_to_dict(row: LongTermMemory) -> dict[str, Any]:
    """Serialize a LongTermMemory row to a dict, excluding embedding."""
    return {
        "id": row.id,
        "content": row.content,
        "importance": row.importance,
        "category": row.category or "",
        "tags": list(row.tags) if row.tags else [],
        "scope": getattr(row, "scope", None) or "global",
        "agentId": row.agent_id or "",
        "createdAt": row.created_at,
        "lastAccessed": row.last_accessed,
        "summary": getattr(row, "summary", "") or "",
        "keywords": list(row.keywords) if getattr(row, "keywords", None) else [],
        "contentScope": getattr(row, "content_scope", "") or "",
    }


def _ltm_item_to_dict(item: Item) -> dict[str, Any]:
    """Serialize an in-memory Item to a dict, excluding embedding."""
    return {
        "id": item.id,
        "content": item.content,
        "importance": item.importance,
        "category": item.category or "",
        "tags": list(item.tags) if item.tags else [],
        "scope": item.scope,
        "agentId": item.agent_id or "",
        "createdAt": item.created_at,
        "lastAccessed": item.last_accessed,
        "summary": item.summary,
        "keywords": list(item.keywords) if item.keywords else [],
        "contentScope": item.content_scope,
    }


# ─── Pydantic request models ───────────────────────────────────────────────


class LTMUpdateRequest(BaseModel):
    content: str | None = None
    importance: float | None = None
    category: str | None = None
    tags: list[str] | None = None
    summary: str | None = None
    keywords: list[str] | None = None
    contentScope: str | None = None


class PreferenceUpdateRequest(BaseModel):
    value: str


# ─── LTM endpoints ─────────────────────────────────────────────────────────


@router.get("/api/memory/long-term")
async def list_ltm_memories(
    agent_id: str | None = None,
    category: str | None = None,
    tag: str | None = None,
    page: int = 1,
    size: int = 20,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """List long-term memory entries with optional filtering and pagination.

    Embeddings are never included in the response.
    Tries the in-memory LongTerm cache first (via MemoryService);
    falls back to direct PG query when MemoryService is not initialized.
    """
    page = max(1, page)
    size = max(1, min(100, size))

    # Try MemoryService (in-memory cache) first
    svc = _get_memory_service()
    if svc is not None and svc.ltm is not None:
        items, total = await svc.ltm.list_items(
            agent_id=agent_id,
            category=category,
            tag=tag,
            page=page,
            size=size,
            user_id=user.id,
        )
        return JSONResponse({
            "items": [_ltm_item_to_dict(it) for it in items],
            "total": total,
            "page": page,
            "size": size,
        })

    # Fallback: direct PG query (MemoryService not initialized)
    async with get_remote_db() as session:
        stmt = select(LongTermMemory).where(
            LongTermMemory.user_id == user.id
        ).order_by(LongTermMemory.id)

        if agent_id:
            stmt = stmt.where(
                LongTermMemory.scope == "agent",
                LongTermMemory.agent_id == agent_id,
            )

        if category:
            stmt = stmt.where(LongTermMemory.category == category)

        # tag filter: cast JSON to text and LIKE-match (works with both PG JSONB and SQLite JSON)
        if tag:
            stmt = stmt.where(cast(LongTermMemory.tags, String).like(f'%"{tag}"%'))

        # count total
        from sqlalchemy import func

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total_result = await session.execute(count_stmt)
        total = total_result.scalar() or 0

        # paginate
        stmt = stmt.offset((page - 1) * size).limit(size)
        result = await session.execute(stmt)
        rows = result.scalars().all()

    items = [_ltm_row_to_dict(r) for r in rows]
    return JSONResponse({
        "items": items,
        "total": total,
        "page": page,
        "size": size,
    })


@router.put("/api/memory/long-term/{memory_id}")
async def update_ltm_memory(
    memory_id: int,
    body: LTMUpdateRequest,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Edit a single LTM item's content/importance/category/tags.

    When content changes, an async task is triggered to recompute the embedding.
    """
    svc = _get_memory_service()
    if svc is None or svc.ltm is None:
        return JSONResponse(
            status_code=503,
            content={"error": "MemoryService not initialized"},
        )

    old_content: str | None = None
    old_summary: str | None = None
    async with get_remote_db() as session:
        row = await session.get(LongTermMemory, memory_id)
        if row is None or row.user_id != user.id:
            return JSONResponse(
                status_code=404,
                content={"error": f"Memory {memory_id} not found"},
            )
        old_content = row.content
        old_summary = getattr(row, "summary", "") or ""

    updated = await svc.ltm.update_item(
        memory_id=memory_id,
        content=body.content,
        importance=body.importance,
        category=body.category,
        tags=body.tags,
        summary=body.summary,
        keywords=body.keywords,
        content_scope=body.contentScope,
    )
    if updated is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"Memory {memory_id} not found in memory"},
        )

    # summary changed → recompute embedding using new summary (embedding is
    # summary-based per the dual-path retrieval policy); otherwise content
    # changed → recompute using content (backward compat).
    if body.summary is not None and body.summary != old_summary:
        asyncio.create_task(_recompute_embedding(svc, memory_id, body.summary))
    elif body.content is not None and body.content != old_content:
        asyncio.create_task(_recompute_embedding(svc, memory_id, body.content))

    # sync graph memory node content (if Neo4j available)
    if svc.graph_memory is not None:
        try:
            await svc.graph_memory.update_node(updated)
        except Exception as e:
            logger.warning("GraphMemory node update failed (id=%s): %s", memory_id, e)

    return JSONResponse({"ok": True})


@router.delete("/api/memory/long-term/{memory_id}")
async def delete_ltm_memory(
    memory_id: int,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Delete a single LTM item from PG, in-memory, and GraphMemory."""
    svc = _get_memory_service()
    if svc is None or svc.ltm is None:
        return JSONResponse(
            status_code=503,
            content={"error": "MemoryService not initialized"},
        )

    deleted = await svc.ltm.delete_item(memory_id)
    if not deleted:
        return JSONResponse(
            status_code=404,
            content={"error": f"Memory {memory_id} not found"},
        )

    return JSONResponse({"ok": True})


# ─── Preference endpoints ──────────────────────────────────────────────────


@router.get("/api/memory/preferences")
async def list_preferences(
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """List all user preference key-value pairs."""
    async with get_remote_db() as session:
        stmt = select(UserPreference).where(UserPreference.user_id == user.id)
        result = await session.execute(stmt)
        rows = result.scalars().all()

    return JSONResponse({
        "items": [
            {"key": r.key, "value": r.value}
            for r in rows
        ],
        "total": len(rows),
    })


@router.put("/api/memory/preferences/{key}")
async def update_preference(
    key: str,
    body: PreferenceUpdateRequest,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Edit a preference value (upsert)."""
    import time as _time
    async with get_remote_db() as session:
        existing = await session.get(
            UserPreference, {"user_id": user.id, "key": key}
        )
        if existing:
            existing.value = body.value
            existing.updated_at = _time.time()
        else:
            session.add(UserPreference(
                user_id=user.id,
                key=key,
                value=body.value,
                updated_at=_time.time(),
            ))
    from app.infra.cache_helpers import invalidate_user_preferences_cache
    await invalidate_user_preferences_cache(user.id)
    return JSONResponse({"ok": True})


@router.delete("/api/memory/preferences/{key}")
async def delete_preference(
    key: str,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Delete a preference key."""
    from sqlalchemy import delete as sa_delete
    async with get_remote_db() as session:
        result = await session.execute(
            sa_delete(UserPreference).where(
                (UserPreference.user_id == user.id)
                & (UserPreference.key == key)
            )
        )
        deleted = (result.rowcount or 0) > 0

    if not deleted:
        return JSONResponse(
            status_code=404,
            content={"error": f"Preference '{key}' not found"},
        )

    from app.infra.cache_helpers import invalidate_user_preferences_cache
    await invalidate_user_preferences_cache(user.id)
    return JSONResponse({"ok": True})


# ─── Session Memory endpoints ──────────────────────────────────────────────


@router.get("/api/memory/session/{conversation_id}")
async def get_session_memory(
    conversation_id: str,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Return the Session Memory text for a given conversation (read-only)."""
    svc = _get_memory_service()
    if svc is None or svc.session_memory is None:
        return JSONResponse(
            status_code=503,
            content={"error": "MemoryService not initialized"},
        )

    await verify_conversation_ownership(conversation_id, user.id)
    record = await svc.session_memory.get(conversation_id)
    if record is None:
        return JSONResponse(
            status_code=404,
            content={"error": f"No session memory for conversation {conversation_id}"},
        )

    # Fetch conversation title for display
    title = ""
    async with get_local_db() as session:
        conv = await session.get(Conversation, conversation_id)
        if conv:
            title = conv.title

    return JSONResponse({
        "conversationId": conversation_id,
        "title": title,
        "summary": record.summary,
        "coversUpTo": record.covers_up_to,
    })


@router.get("/api/memory/sessions")
async def list_session_memories(
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """List all conversations that have session memory summaries."""
    async with get_local_db() as session:
        stmt = (
            select(
                ContextSummary.conversation_id,
                ContextSummary.summary,
                ContextSummary.covers_up_to,
                ContextSummary.created_at,
            )
            .where(ContextSummary.summary_type == "session")
            .order_by(ContextSummary.created_at.desc())
        )
        result = await session.execute(stmt)
        rows = result.all()

    # Batch fetch conversation titles
    conv_ids = [r[0] for r in rows]
    titles: dict[str, str] = {}
    if conv_ids:
        async with get_local_db() as session:
            stmt = select(Conversation.id, Conversation.title).where(
                Conversation.id.in_(conv_ids)
            )
            result = await session.execute(stmt)
            for cid, title in result.all():
                titles[cid] = title

    items = [
        {
            "conversationId": r[0],
            "title": titles.get(r[0], ""),
            "summary": r[1],
            "coversUpTo": r[2],
            "createdAt": r[3],
        }
        for r in rows
    ]
    return JSONResponse({"items": items, "total": len(items)})


# ─── Embedding recomputation (async background) ────────────────────────────


async def _recompute_embedding(svc, memory_id: int, content: str) -> None:
    """Recompute embedding for a memory item and persist it.

    Runs as a fire-and-forget background task. Uses the MemoryService's
    embed_fn if available. Also syncs the updated content to GraphMemory.
    """
    embed_fn = getattr(svc, "_embed_fn", None)
    if embed_fn is None:
        logger.debug("Skipping embedding recompute: no embed_fn available")
        return

    try:
        embedding = await asyncio.to_thread(embed_fn, content)
        await svc.ltm.update_embedding(memory_id, embedding)
        logger.info("Embedding recomputed for memory %d", memory_id)
    except Exception as e:
        logger.warning("Embedding recompute failed (id=%s): %s", memory_id, e)
