"""Memory management API — file-native memory + PG-backed preferences + session memory.

Memory files (daily/ and digest/) are managed via file operations.
Preferences (PG KV table) and session memory (context summaries) are preserved.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import and_, desc, select

from app.auth.dependencies import get_current_user
from app.auth.ownership import verify_conversation_ownership
from app.db.engine import get_local_db, get_remote_db
from app.db.models import ContextSummary, Conversation, User, UserPreference

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_memory_service():
    """Get the module-level MemoryService instance (wired at app startup)."""
    from app.main import _memory_service  # type: ignore[attr-defined]
    return _memory_service


# ─── Pydantic request models ───────────────────────────────────────────────


class MemoryFileWriteRequest(BaseModel):
    name: str
    body: str = ""
    description: str = ""
    agent_id: str | None = None
    tags: list[str] = []
    importance: float = 0.5
    bucket: str = "wiki"


class PreferenceUpdateRequest(BaseModel):
    value: str


# ─── Memory file endpoints ─────────────────────────────────────────────────


@router.get("/api/memory/files")
async def list_memory_files(
    bucket: str | None = None,
    agent_id: str | None = None,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """List memory files (daily + digest), optionally filtered by bucket/agent."""
    svc = _get_memory_service()
    if svc is None:
        return JSONResponse(
            status_code=503,
            content={"error": "MemoryService not initialized"},
        )

    files: list[dict[str, Any]] = []

    # List digest files
    from app.memory.file_store.markdown_io import read_markdown
    for f in svc.workspace.list_digest_files(bucket=bucket, agent_id=agent_id):
        mem = read_markdown(f)
        if mem:
            files.append({
                "path": str(f.relative_to(svc.workspace.root)),
                "name": mem.frontmatter.name,
                "description": mem.frontmatter.description,
                "bucket": mem.frontmatter.bucket,
                "agentId": mem.frontmatter.agent_id,
                "tags": mem.frontmatter.tags,
                "importance": mem.frontmatter.importance,
                "createdAt": mem.frontmatter.created_at,
                "updatedAt": mem.frontmatter.updated_at,
                "source": mem.frontmatter.source,
                "bodyPreview": mem.body[:200],
            })

    # Also list daily files
    for f in svc.workspace.list_daily_files():
        mem = read_markdown(f)
        if mem:
            files.append({
                "path": str(f.relative_to(svc.workspace.root)),
                "name": mem.frontmatter.name,
                "description": mem.frontmatter.description,
                "bucket": "daily",
                "agentId": mem.frontmatter.agent_id,
                "tags": mem.frontmatter.tags,
                "importance": mem.frontmatter.importance,
                "createdAt": mem.frontmatter.created_at,
                "updatedAt": mem.frontmatter.updated_at,
                "source": mem.frontmatter.source,
                "bodyPreview": mem.body[:200],
            })

    return JSONResponse({"items": files, "total": len(files)})


@router.get("/api/memory/files/{path:path}")
async def read_memory_file(
    path: str,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Read a memory file by its relative path."""
    svc = _get_memory_service()
    if svc is None:
        return JSONResponse(status_code=503, content={"error": "MemoryService not initialized"})

    from app.memory.file_store.markdown_io import read_markdown

    filepath = svc.workspace.root / path
    # Security: ensure path is within workspace
    try:
        filepath.resolve().relative_to(svc.workspace.root.resolve())
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "Invalid path"})

    mem = read_markdown(filepath)
    if mem is None:
        return JSONResponse(status_code=404, content={"error": f"File not found: {path}"})

    return JSONResponse({
        "path": path,
        "name": mem.frontmatter.name,
        "description": mem.frontmatter.description,
        "agentId": mem.frontmatter.agent_id,
        "tags": mem.frontmatter.tags,
        "importance": mem.frontmatter.importance,
        "bucket": mem.frontmatter.bucket,
        "createdAt": mem.frontmatter.created_at,
        "updatedAt": mem.frontmatter.updated_at,
        "source": mem.frontmatter.source,
        "body": mem.body,
    })


@router.put("/api/memory/files/{path:path}")
async def write_memory_file(
    path: str,
    body: MemoryFileWriteRequest,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Create or update a memory file."""
    svc = _get_memory_service()
    if svc is None:
        return JSONResponse(status_code=503, content={"error": "MemoryService not initialized"})

    from app.memory.file_store.frontmatter import MemoryFrontmatter
    from app.memory.file_store.markdown_io import write_markdown

    filepath = svc.workspace.root / path
    try:
        filepath.resolve().relative_to(svc.workspace.root.resolve())
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "Invalid path"})

    fm = MemoryFrontmatter(
        name=body.name,
        description=body.description,
        agent_id=body.agent_id,
        tags=body.tags,
        importance=body.importance,
        bucket=body.bucket,
    )
    write_markdown(filepath, fm, body.body)

    # Reindex the file
    svc.auto_index.index_file(filepath)

    return JSONResponse({"ok": True, "path": path})


@router.delete("/api/memory/files/{path:path}")
async def delete_memory_file(
    path: str,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Delete a memory file."""
    svc = _get_memory_service()
    if svc is None:
        return JSONResponse(status_code=503, content={"error": "MemoryService not initialized"})

    from app.memory.file_store.markdown_io import delete_markdown

    filepath = svc.workspace.root / path
    try:
        filepath.resolve().relative_to(svc.workspace.root.resolve())
    except ValueError:
        return JSONResponse(status_code=400, content={"error": "Invalid path"})

    deleted = delete_markdown(filepath)
    if not deleted:
        return JSONResponse(status_code=404, content={"error": f"File not found: {path}"})

    # Remove from index
    svc.auto_index.remove_file(filepath)

    return JSONResponse({"ok": True})


@router.get("/api/memory/search")
async def search_memory(
    query: str,
    top_k: int = 10,
    agent_id: str | None = None,
    bucket: str | None = None,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Search memory files using hybrid BM25 + wikilink search."""
    svc = _get_memory_service()
    if svc is None:
        return JSONResponse(status_code=503, content={"error": "MemoryService not initialized"})

    results = await svc.recall(query, top_k=top_k, agent_id=agent_id or "")
    return JSONResponse({
        "items": [
            {
                "path": r.path,
                "name": r.name,
                "content": r.content[:500],
                "score": r.score,
                "source": r.source,
                "frontmatter": r.frontmatter,
            }
            for r in results
        ],
        "total": len(results),
    })


# ─── Proactive & auto_dream endpoints ──────────────────────────────────────


@router.get("/api/memory/proactive")
async def get_proactive_topics(
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Get today's proactive interest topics."""
    svc = _get_memory_service()
    if svc is None:
        return JSONResponse(status_code=503, content={"error": "MemoryService not initialized"})

    topics = svc.proactive.get_topics()
    return JSONResponse({"topics": topics, "total": len(topics)})


@router.post("/api/memory/auto-dream")
async def trigger_auto_dream(
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Manually trigger the auto_dream refinement pipeline."""
    svc = _get_memory_service()
    if svc is None:
        return JSONResponse(status_code=503, content={"error": "MemoryService not initialized"})

    result = await svc.trigger_auto_dream()
    return JSONResponse({"ok": True, "result": result})


# ─── Preference endpoints (preserved unchanged) ────────────────────────────


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


# ─── Session Memory endpoints (preserved unchanged) ────────────────────────


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
