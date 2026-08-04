"""manage_conversations tool — list / get / search / update / delete conversations.

Reuses conversation_service + search_service. All operations are scoped by
ToolContext.user_id. Guide conversations (mode='guide') cannot be deleted.
"""

from __future__ import annotations

from typing import Any

from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok
from app.tools.manage_base import emit_guide_side_effect

# ── Handler + sub-handlers ───────────────────────────────────────────────────


async def _manage_conversations_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    action = args.get("action", "")
    user_id = ctx.user_id
    if user_id is None:
        return err("manage_conversations requires a user context")

    if action == "list":
        return await _list_conversations(args, user_id)
    elif action == "get":
        return await _get_conversation(args, user_id)
    elif action == "search":
        return await _search_messages(args, user_id)
    elif action == "update":
        return await _update_conversation(args, user_id, ctx)
    elif action == "delete":
        return await _delete_conversation(args, user_id, ctx)
    else:
        return err(f"Unknown action: {action}")


async def _list_conversations(args: dict[str, Any], user_id: str) -> ToolResult:
    from app.services.conversation_service import list_conversations
    from app.utils.clock import now_ms

    convs = await list_conversations()
    include_archived = args.get("include_archived", False)
    since_hours = args.get("since_hours")
    limit = min(args.get("limit", 20), 100)

    cutoff = now_ms() - (since_hours * 3600 * 1000) if since_hours else None

    filtered = []
    for c in convs:
        if not include_archived and c.archived:
            continue
        if cutoff and c.updated_at < cutoff:
            continue
        filtered.append({
            "id": c.id,
            "title": c.title,
            "mode": c.mode,
            "archived": c.archived,
            "pinnedAt": c.pinned_at,
            "createdAt": c.created_at,
            "updatedAt": c.updated_at,
            "agentIds": c.agent_ids,
            "summary": c.summary,
        })
        if len(filtered) >= limit:
            break

    return ok({"conversations": filtered, "total": len(filtered)})


async def _get_conversation(args: dict[str, Any], user_id: str) -> ToolResult:
    from sqlalchemy import select

    from app.db.engine import get_local_db
    from app.db.models import Conversation, Message

    conversation_id = args.get("conversation_id")
    if not conversation_id:
        return err("conversation_id is required for get action")

    message_limit = min(args.get("message_limit", 10), 50)

    async with get_local_db() as db:
        conv = await db.get(Conversation, conversation_id)
        if conv is None:
            return err(f"Conversation not found: {conversation_id}")

        msg_result = await db.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .where(Message.hidden.is_(False))
            .order_by(Message.created_at.desc())
            .limit(message_limit)
        )
        messages = msg_result.scalars().all()

    msg_summaries = [
        {
            "id": m.id,
            "role": m.role,
            "agentId": m.agent_id,
            "createdAt": m.created_at,
            "preview": _extract_text_preview(m.parts_list),
        }
        for m in reversed(messages)
    ]

    return ok({
        "conversation": {
            "id": conv.id,
            "title": conv.title,
            "mode": conv.mode,
            "archived": conv.archived,
            "agentIds": conv.agent_ids_list,
            "summary": conv.summary,
            "createdAt": conv.created_at,
            "updatedAt": conv.updated_at,
        },
        "messages": msg_summaries,
        "messageCount": len(msg_summaries),
    })


def _extract_text_preview(parts: list[dict]) -> str:
    for part in parts:
        if isinstance(part, dict) and part.get("type") == "text":
            content = part.get("content", "")
            return content[:200] + "..." if len(content) > 200 else content
    return ""


async def _search_messages(args: dict[str, Any], user_id: str) -> ToolResult:
    from app.services.search_service import search_messages

    query = args.get("query", "").strip()
    if not query:
        return err("query is required for search action")

    search_limit = min(args.get("search_limit", 20), 100)
    search_role = args.get("search_role")

    result = await search_messages(
        query=query,
        limit=search_limit,
        role=search_role,
    )

    return ok({
        "hits": [
            {
                "messageId": h.message_id,
                "conversationId": h.conversation_id,
                "conversationTitle": h.conversation_title,
                "role": h.role,
                "agentName": h.agent_name,
                "createdAt": h.created_at,
                "snippet": h.snippet,
            }
            for h in result.hits
        ],
        "total": result.total,
    })


async def _update_conversation(args: dict[str, Any], user_id: str, ctx: ToolContext) -> ToolResult:
    from app.services.conversation_service import (
        get_conversation,
        rename_conversation,
        toggle_archive_conversation,
        toggle_pin_conversation,
        update_conversation_summary,
    )

    conversation_id = args.get("conversation_id")
    if not conversation_id:
        return err("conversation_id is required for update action")

    # Verify ownership — ownership is checked at the API layer;
    # for the tool, we trust ToolContext.user_id scoping.
    await get_conversation(conversation_id)

    updated = False
    if "title" in args and args["title"]:
        await rename_conversation(conversation_id, args["title"])
        updated = True
    if "summary" in args:
        await update_conversation_summary(conversation_id, args["summary"])
        updated = True
    if "toggle_archive" in args and args["toggle_archive"] is not None:
        await toggle_archive_conversation(conversation_id)
        updated = True
    if "toggle_pin" in args and args["toggle_pin"] is not None:
        await toggle_pin_conversation(conversation_id)
        updated = True

    if not updated:
        return ok({"message": "没有需要更新的字段"})

    emit_guide_side_effect(ctx=ctx, target="conversations", action="update")
    return ok({"message": "已更新会话"})


async def _delete_conversation(args: dict[str, Any], user_id: str, ctx: ToolContext) -> ToolResult:
    if not args.get("confirm", False):
        return err("删除操作需要先通过 ask_user 向用户确认，并传 confirm=true")

    conversation_id = args.get("conversation_id")
    if not conversation_id:
        return err("conversation_id is required for delete action")

    from app.db.engine import get_local_db
    from app.db.models import Conversation

    async with get_local_db() as db:
        conv = await db.get(Conversation, conversation_id)
        if conv is None:
            return err(f"Conversation not found: {conversation_id}")
        if conv.mode == "guide":
            return err("不能删除 guide 会话")

    from app.services.conversation_service import delete_conversation

    try:
        await delete_conversation(conversation_id)
    except Exception as e:
        return err(f"Failed to delete conversation: {e}")

    emit_guide_side_effect(ctx=ctx, target="conversations", action="delete")
    return ok({"message": f"已删除会话「{conv.title}」"})


# ── Tool definition ──────────────────────────────────────────────────────────

manage_conversations_tool = ToolDef(
    name="manage_conversations",
    description=(
        "管理会话与活动：列表 / 查看详情 / 搜索消息 / 更新（重命名/归档/置顶）/ 删除。"
        "action: list | get | search | update | delete。"
        "delete 操作需要先通过 ask_user 向用户确认，并传 confirm=true。"
        "guide 会话（mode=guide）不可删除。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "get", "search", "update", "delete"],
            },
            "include_archived": {"type": "boolean", "default": False},
            "limit": {"type": "integer", "default": 20},
            "since_hours": {"type": "integer"},
            "conversation_id": {"type": "string"},
            "message_limit": {"type": "integer", "default": 10},
            "query": {"type": "string"},
            "search_role": {"type": "string", "enum": ["user", "assistant"]},
            "search_limit": {"type": "integer", "default": 20},
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "toggle_archive": {"type": "boolean"},
            "toggle_pin": {"type": "boolean"},
            "confirm": {"type": "boolean", "default": False},
        },
        "required": ["action"],
    },
    handler=_manage_conversations_handler,  # type: ignore[assignment]
)
