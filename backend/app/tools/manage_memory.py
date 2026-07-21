"""manage_memory tool — list / delete / consolidate / optimize memories.

Reuses MemoryService + LongTermMemory store. The optimize action executes an
LLM-generated plan (delete_ids + merge_groups + update_ids). All operations are
scoped by ToolContext.user_id.
"""

from __future__ import annotations

import time
from typing import Any

from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok
from app.tools.manage_base import emit_guide_side_effect

# ── Handler + sub-handlers ───────────────────────────────────────────────────


async def _manage_memory_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    action = args.get("action", "")
    user_id = ctx.user_id
    if user_id is None:
        return err("manage_memory requires a user context")

    memory_type = args.get("memory_type", "long_term")

    if action == "list":
        return await _list_memories(args, user_id, memory_type)
    elif action == "delete":
        return await _delete_memories(args, user_id, memory_type, ctx)
    elif action == "consolidate":
        return await _consolidate_memories(args, user_id, ctx)
    elif action == "optimize":
        return await _optimize_memories(args, user_id, ctx)
    else:
        return err(f"Unknown action: {action}")


async def _list_memories(args: dict[str, Any], user_id: str, memory_type: str) -> ToolResult:
    if memory_type == "long_term":
        return await _list_ltm(args, user_id)
    elif memory_type == "preference":
        return await _list_preferences(user_id)
    else:
        return err(f"List not supported for memory_type: {memory_type}")


async def _list_ltm(args: dict[str, Any], user_id: str) -> ToolResult:
    from sqlalchemy import select

    from app.db.engine import get_db
    from app.db.models import LongTermMemory

    limit = min(args.get("limit", 20), 100)
    category = args.get("category")
    agent_id = args.get("agent_id")

    async with get_db() as db:
        stmt = select(LongTermMemory).where(LongTermMemory.user_id == user_id)
        if category:
            stmt = stmt.where(LongTermMemory.category == category)
        if agent_id:
            stmt = stmt.where(LongTermMemory.agent_id == agent_id)
        stmt = stmt.order_by(LongTermMemory.id.desc()).limit(limit)
        result = await db.execute(stmt)
        rows = result.scalars().all()

    items = [
        {
            "id": r.id,
            "content": r.content,
            "importance": r.importance,
            "category": r.category,
            "tags": list(r.tags) if r.tags else [],
            "scope": r.scope,
            "agentId": r.agent_id,
            "createdAt": r.created_at,
            "lastAccessed": r.last_accessed,
        }
        for r in rows
    ]
    return ok({"items": items, "total": len(items)})


async def _list_preferences(user_id: str) -> ToolResult:
    from sqlalchemy import select

    from app.db.engine import get_db
    from app.db.models import UserPreference

    async with get_db() as db:
        result = await db.execute(
            select(UserPreference).where(UserPreference.user_id == user_id)
        )
        rows = result.scalars().all()

    items = [
        {"key": r.key, "value": r.value, "source": r.source, "updatedAt": r.updated_at}
        for r in rows
    ]
    return ok({"items": items, "total": len(items)})


async def _delete_memories(
    args: dict[str, Any], user_id: str, memory_type: str, ctx: ToolContext
) -> ToolResult:
    if not args.get("confirm", False):
        return err("删除操作需要先通过 ask_user 向用户确认，并传 confirm=true")

    memory_ids = args.get("memory_ids", [])
    if not memory_ids:
        return err("memory_ids is required for delete action")

    if memory_type == "long_term":
        return await _delete_ltm(memory_ids, user_id, ctx)
    elif memory_type == "preference":
        return await _delete_preferences(memory_ids, user_id, ctx)
    else:
        return err(f"Delete not supported for memory_type: {memory_type}")


async def _delete_ltm(memory_ids: list[str], user_id: str, ctx: ToolContext) -> ToolResult:
    from app.db.engine import get_db
    from app.db.models import LongTermMemory

    deleted_count = 0
    async with get_db() as db:
        for mid_str in memory_ids:
            try:
                mid = int(mid_str)
            except (ValueError, TypeError):
                continue
            row = await db.get(LongTermMemory, mid)
            if row is not None and row.user_id == user_id:
                await db.delete(row)
                deleted_count += 1

    emit_guide_side_effect(ctx=ctx, target="memory", action="delete")
    return ok({"deleted": deleted_count, "message": f"已删除 {deleted_count} 条长期记忆"})


async def _delete_preferences(keys: list[str], user_id: str, ctx: ToolContext) -> ToolResult:
    from sqlalchemy import delete as sql_delete

    from app.db.engine import get_db
    from app.db.models import UserPreference

    async with get_db() as db:
        result = await db.execute(
            sql_delete(UserPreference)
            .where(UserPreference.user_id == user_id)
            .where(UserPreference.key.in_(keys))
        )
        deleted_count = result.rowcount or 0

    emit_guide_side_effect(ctx=ctx, target="memory", action="delete")
    return ok({"deleted": deleted_count, "message": f"已删除 {deleted_count} 条偏好"})


async def _consolidate_memories(args: dict[str, Any], user_id: str, ctx: ToolContext) -> ToolResult:
    from app.main import _memory_service

    svc = _memory_service
    if svc is None or svc.ltm is None:
        return err("MemoryService not initialized")

    result = await svc.ltm.consolidate()
    emit_guide_side_effect(ctx=ctx, target="memory", action="update")
    return ok({
        "decayed": result.decayed,
        "merged": result.merged,
        "expired": result.expired,
        "message": f"固化完成：衰减 {result.decayed}，合并 {result.merged}，过期 {result.expired}",
    })


async def _optimize_memories(args: dict[str, Any], user_id: str, ctx: ToolContext) -> ToolResult:
    if not args.get("confirm", False):
        return err(
            "记忆整理优化是批量破坏性操作，需要先通过 ask_user 向用户确认，并传 confirm=true"
        )

    plan = args.get("plan")
    if not plan or not isinstance(plan, dict):
        return err("plan is required for optimize action")

    delete_ids: list = plan.get("delete_ids", [])
    merge_groups: list = plan.get("merge_groups", [])
    update_ids: list = plan.get("update_ids", [])

    from app.db.engine import get_db
    from app.db.models import LongTermMemory

    deleted_count = 0
    merged_count = 0
    updated_count = 0

    # ── Phase 1: Delete ──
    all_delete_ids: list[int] = []
    for did in delete_ids:
        try:
            all_delete_ids.append(int(did))
        except (ValueError, TypeError):
            continue
    for group in merge_groups:
        for sid in group.get("source_ids", []):
            try:
                all_delete_ids.append(int(sid))
            except (ValueError, TypeError):
                continue

    if all_delete_ids:
        async with get_db() as db:
            for mid in all_delete_ids:
                row = await db.get(LongTermMemory, mid)
                if row is not None and row.user_id == user_id:
                    await db.delete(row)
                    deleted_count += 1

    # ── Phase 2: Merge (create new + embedding) ──
    from app.main import _memory_service

    for group in merge_groups:
        merged_content = group.get("merged_content", "")
        if not merged_content:
            continue

        if _memory_service and _memory_service.ltm:
            try:
                await _memory_service.ltm.add(
                    content=merged_content,
                    importance=group.get("merged_importance", 0.6),
                    category=group.get("merged_category", ""),
                    tags=group.get("merged_tags", []),
                    user_id=user_id,
                )
                merged_count += 1
            except Exception:
                # Fallback: create without embedding
                await _create_ltm_fallback(group, user_id)
                merged_count += 1
        else:
            await _create_ltm_fallback(group, user_id)
            merged_count += 1

    # ── Phase 3: Update attributes ──
    for upd in update_ids:
        try:
            mid = int(upd.get("id", ""))
        except (ValueError, TypeError):
            continue

        async with get_db() as db:
            row = await db.get(LongTermMemory, mid)
            if row is None or row.user_id != user_id:
                continue
            if "importance" in upd:
                row.importance = upd["importance"]
            if "category" in upd:
                row.category = upd["category"]
            if "tags" in upd:
                row.tags = upd["tags"]
            updated_count += 1

    net_change = deleted_count - merged_count
    emit_guide_side_effect(ctx=ctx, target="memory", action="update")
    return ok({
        "deleted": deleted_count,
        "merged": merged_count,
        "updated": updated_count,
        "net_change": net_change,
        "message": (
            f"整理完成：删除 {deleted_count} 条，合并 {merged_count} 组，"
            f"更新 {updated_count} 条，净减 {net_change} 条"
        ),
    })


async def _create_ltm_fallback(group: dict[str, Any], user_id: str) -> None:
    """Create a LongTermMemory row without embedding (fallback)."""
    from app.db.engine import get_db
    from app.db.models import LongTermMemory

    merged_content = group.get("merged_content", "")
    now = time.time()
    new_mem = LongTermMemory(
        content=merged_content,
        importance=group.get("merged_importance", 0.6),
        embedding=None,
        created_at=now,
        last_accessed=now,
        category=group.get("merged_category", ""),
        tags=group.get("merged_tags", []),
        scope="global",
        user_id=user_id,
    )
    async with get_db() as db:
        db.add(new_mem)


# ── Tool definition ──────────────────────────────────────────────────────────

manage_memory_tool = ToolDef(
    name="manage_memory",
    description=(
        "管理记忆：长期记忆 / 偏好 / 会话记忆的查看、删除、固化、智能整理优化。"
        "action: list | delete | consolidate | optimize。"
        "memory_type: long_term | preference | session。"
        "delete 和 optimize 操作需要先通过 ask_user 向用户确认，并传 confirm=true。"
        "optimize 接收一个 plan（delete_ids + merge_groups + update_ids），"
        "由小A LLM 分析后生成，需用户确认才执行。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "delete", "consolidate", "optimize"],
            },
            "memory_type": {
                "type": "string",
                "enum": ["long_term", "preference", "session"],
                "default": "long_term",
            },
            "category": {"type": "string"},
            "tag": {"type": "string"},
            "agent_id": {"type": "string"},
            "limit": {"type": "integer", "default": 20},
            "memory_ids": {"type": "array", "items": {"type": "string"}},
            "confirm": {"type": "boolean", "default": False},
            "conversation_id": {"type": "string"},
            "plan": {
                "type": "object",
                "properties": {
                    "delete_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "merge_groups": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "source_ids": {"type": "array", "items": {"type": "string"}},
                                "merged_content": {"type": "string"},
                                "merged_category": {"type": "string"},
                                "merged_tags": {"type": "array", "items": {"type": "string"}},
                                "merged_importance": {"type": "number"},
                            },
                        },
                    },
                    "update_ids": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "importance": {"type": "number"},
                                "category": {"type": "string"},
                                "tags": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                    },
                },
            },
        },
        "required": ["action"],
    },
    handler=_manage_memory_handler,  # type: ignore[assignment]
)
