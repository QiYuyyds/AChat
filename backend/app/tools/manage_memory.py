"""manage_memory tool — list / delete memory files + list / delete preferences.

File-native memory system uses Markdown files (daily/ and digest/).
Preference management (PG KV table) is preserved unchanged.
"""

from __future__ import annotations

from typing import Any

from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok
from app.tools.manage_base import emit_guide_side_effect


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
        return await _trigger_auto_dream(ctx)
    else:
        return err(f"Unknown action: {action}")


async def _list_memories(args: dict[str, Any], user_id: str, memory_type: str) -> ToolResult:
    if memory_type == "long_term":
        return await _list_memory_files(args)
    elif memory_type == "preference":
        return await _list_preferences(user_id)
    else:
        return err(f"List not supported for memory_type: {memory_type}")


async def _list_memory_files(args: dict[str, Any]) -> ToolResult:
    """List memory files (digest + daily) from the file-native workspace."""
    try:
        from app.main import _memory_service  # type: ignore[attr-defined]
    except ImportError:
        return err("Memory service not available")

    if _memory_service is None:
        return err("Memory service not initialized")

    from app.memory.file_store.markdown_io import read_markdown

    bucket = args.get("category")  # reuse "category" param as bucket filter
    agent_id = args.get("agent_id")
    limit = min(args.get("limit", 50), 200)

    items: list[dict[str, Any]] = []

    for f in _memory_service.workspace.list_digest_files(bucket=bucket, agent_id=agent_id):
        mem = read_markdown(f)
        if mem is None:
            continue
        items.append({
            "path": str(f.relative_to(_memory_service.workspace.root)),
            "name": mem.frontmatter.name,
            "description": mem.frontmatter.description,
            "bucket": mem.frontmatter.bucket,
            "agentId": mem.frontmatter.agent_id,
            "tags": mem.frontmatter.tags,
            "importance": mem.frontmatter.importance,
            "createdAt": mem.frontmatter.created_at,
            "updatedAt": mem.frontmatter.updated_at,
            "bodyPreview": mem.body[:200],
        })

    for f in _memory_service.workspace.list_daily_files():
        mem = read_markdown(f)
        if mem is None:
            continue
        items.append({
            "path": str(f.relative_to(_memory_service.workspace.root)),
            "name": mem.frontmatter.name,
            "description": mem.frontmatter.description,
            "bucket": "daily",
            "agentId": mem.frontmatter.agent_id,
            "tags": mem.frontmatter.tags,
            "importance": mem.frontmatter.importance,
            "createdAt": mem.frontmatter.created_at,
            "updatedAt": mem.frontmatter.updated_at,
            "bodyPreview": mem.body[:200],
        })

    items = items[:limit]
    return ok({"items": items, "total": len(items)})


async def _list_preferences(user_id: str) -> ToolResult:
    from sqlalchemy import select

    from app.db.engine import get_remote_db
    from app.db.models import UserPreference

    async with get_remote_db() as db:
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
        return await _delete_memory_files(memory_ids, ctx)
    elif memory_type == "preference":
        return await _delete_preferences(memory_ids, user_id, ctx)
    else:
        return err(f"Delete not supported for memory_type: {memory_type}")


async def _delete_memory_files(paths: list[str], ctx: ToolContext) -> ToolResult:
    """Delete memory files by their relative paths."""
    try:
        from app.main import _memory_service  # type: ignore[attr-defined]
    except ImportError:
        return err("Memory service not available")

    if _memory_service is None:
        return err("Memory service not initialized")

    from app.memory.file_store.markdown_io import delete_markdown

    deleted_count = 0
    root = _memory_service.workspace.root

    for path_str in paths:
        filepath = root / path_str
        try:
            filepath.resolve().relative_to(root.resolve())
        except ValueError:
            continue
        if delete_markdown(filepath):
            _memory_service.auto_index.remove_file(filepath)
            deleted_count += 1

    emit_guide_side_effect(ctx=ctx, target="memory", action="delete")
    return ok({"deleted": deleted_count, "message": f"已删除 {deleted_count} 条记忆文件"})


async def _delete_preferences(keys: list[str], user_id: str, ctx: ToolContext) -> ToolResult:
    from sqlalchemy import delete as sql_delete

    from app.db.engine import get_remote_db
    from app.db.models import UserPreference

    async with get_remote_db() as db:
        result = await db.execute(
            sql_delete(UserPreference)
            .where(UserPreference.user_id == user_id)
            .where(UserPreference.key.in_(keys))
        )
        deleted_count = result.rowcount or 0

    from app.infra.cache_helpers import invalidate_user_preferences_cache
    await invalidate_user_preferences_cache(user_id)
    emit_guide_side_effect(ctx=ctx, target="memory", action="delete")
    return ok({"deleted": deleted_count, "message": f"已删除 {deleted_count} 条偏好"})


async def _trigger_auto_dream(ctx: ToolContext) -> ToolResult:
    """Trigger the auto_dream refinement pipeline (replaces old consolidate)."""
    try:
        from app.main import _memory_service  # type: ignore[attr-defined]
    except ImportError:
        return err("Memory service not available")

    if _memory_service is None:
        return err("Memory service not initialized")

    result = await _memory_service.trigger_auto_dream()
    emit_guide_side_effect(ctx=ctx, target="memory", action="update")
    return ok({
        "result": result,
        "message": "记忆精炼 (auto_dream) 已触发",
    })


manage_memory_tool = ToolDef(
    name="manage_memory",
    description=(
        "管理记忆：记忆文件 / 偏好的查看、删除、精炼。"
        "action: list | delete | consolidate。"
        "memory_type: long_term（记忆文件）| preference（偏好）。"
        "consolidate 触发 auto_dream 精炼流水线（extract → integrate → topics）。"
        "delete 操作需要先通过 ask_user 向用户确认，并传 confirm=true。"
        "list long_term 返回记忆文件列表（digest + daily），每项含 path 字段用于删除。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "delete", "consolidate"],
            },
            "memory_type": {
                "type": "string",
                "enum": ["long_term", "preference"],
                "default": "long_term",
            },
            "category": {"type": "string", "description": "Filter by bucket (procedure/wiki/daily)"},
            "agent_id": {"type": "string"},
            "limit": {"type": "integer", "default": 50},
            "memory_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "For long_term: file paths; for preference: keys",
            },
            "confirm": {"type": "boolean", "default": False},
        },
        "required": ["action"],
    },
    handler=_manage_memory_handler,  # type: ignore[assignment]
)
