"""manage_mcp tool — list / create / update / delete MCP server configs.

Reuses McpServer model directly for CRUD. All operations are scoped by
ToolContext.user_id.
"""

from __future__ import annotations

from typing import Any

from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok
from app.tools.manage_base import emit_guide_side_effect

# ── Helpers ──────────────────────────────────────────────────────────────────


def _serialize_server(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "transport": row.transport,
        "command": row.command,
        "args": list(row.args) if row.args else [],
        "url": row.url,
        "enabled": row.enabled,
        "trust": row.trust,
        "createdAt": row.created_at,
    }


# ── Handler + sub-handlers ───────────────────────────────────────────────────


async def _manage_mcp_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    action = args.get("action", "")
    user_id = ctx.user_id
    if user_id is None:
        return err("manage_mcp requires a user context")

    if action == "list":
        return await _list_mcp(user_id)
    elif action == "create":
        return await _create_mcp(args, user_id, ctx)
    elif action == "update":
        return await _update_mcp(args, user_id, ctx)
    elif action == "delete":
        return await _delete_mcp(args, user_id, ctx)
    else:
        return err(f"Unknown action: {action}")


async def _list_mcp(user_id: str) -> ToolResult:
    from sqlalchemy import select

    from app.db.engine import get_db
    from app.db.models import McpServer

    async with get_db() as db:
        result = await db.execute(
            select(McpServer)
            .where(McpServer.user_id == user_id)
            .order_by(McpServer.created_at)
        )
        rows = result.scalars().all()

    return ok({"servers": [_serialize_server(r) for r in rows]})


async def _create_mcp(args: dict[str, Any], user_id: str, ctx: ToolContext) -> ToolResult:
    from sqlalchemy import select

    from app.db.engine import get_db
    from app.db.models import McpServer
    from app.utils.clock import now_ms
    from app.utils.ids import new_mcp_server_id

    name = args.get("name", "").strip()
    if not name:
        return err("name is required for create action")

    transport = args.get("transport", "stdio")
    command = args.get("command")
    server_args = args.get("args", [])
    url = args.get("url")

    if transport == "stdio" and not command:
        return err("stdio transport requires a command")

    async with get_db() as db:
        existing = await db.execute(select(McpServer).where(McpServer.name == name))
        if existing.scalar_one_or_none() is not None:
            return err(f"MCP server with name '{name}' already exists")

        server = McpServer(
            id=new_mcp_server_id(),
            user_id=user_id,
            name=name,
            transport=transport,
            command=command,
            args=server_args,
            env=args.get("env"),
            url=url,
            headers=args.get("headers"),
            trust="ask",
            enabled=True,
            created_at=now_ms(),
        )
        db.add(server)
        await db.flush()
        result = _serialize_server(server)

    emit_guide_side_effect(ctx=ctx, target="mcp", action="create")
    return ok({"server": result, "message": f"已创建 MCP Server「{name}」"})


async def _update_mcp(args: dict[str, Any], user_id: str, ctx: ToolContext) -> ToolResult:
    from app.db.engine import get_db
    from app.db.models import McpServer

    server_id = args.get("server_id")
    if not server_id:
        return err("server_id is required for update action")

    async with get_db() as db:
        server = await db.get(McpServer, server_id)
        if server is None or server.user_id != user_id:
            return err(f"MCP server not found: {server_id}")

        if "name" in args:
            server.name = args["name"]
        if "transport" in args:
            server.transport = args["transport"]
        if "command" in args:
            server.command = args["command"]
        if "args" in args:
            server.args = args["args"]
        if "url" in args:
            server.url = args["url"]
        if "env" in args:
            server.env = args["env"]
        if "headers" in args:
            server.headers = args["headers"]
        if "enabled" in args:
            server.enabled = args["enabled"]

        await db.flush()
        result = _serialize_server(server)

    emit_guide_side_effect(ctx=ctx, target="mcp", action="update")
    return ok({"server": result, "message": f"已更新 MCP Server「{result['name']}」"})


async def _delete_mcp(args: dict[str, Any], user_id: str, ctx: ToolContext) -> ToolResult:
    if not args.get("confirm", False):
        return err("删除操作需要先通过 ask_user 向用户确认，并传 confirm=true")

    server_id = args.get("server_id")
    if not server_id:
        return err("server_id is required for delete action")

    from app.db.engine import get_db
    from app.db.models import McpServer

    async with get_db() as db:
        server = await db.get(McpServer, server_id)
        if server is None or server.user_id != user_id:
            return err(f"MCP server not found: {server_id}")
        server_name = server.name
        await db.delete(server)

    emit_guide_side_effect(ctx=ctx, target="mcp", action="delete")
    return ok({"message": f"已删除 MCP Server「{server_name}」"})


# ── Tool definition ──────────────────────────────────────────────────────────

manage_mcp_tool = ToolDef(
    name="manage_mcp",
    description=(
        "管理 MCP Server 配置：列表 / 创建 / 修改 / 删除。"
        "action: list | create | update | delete。"
        "delete 操作需要先通过 ask_user 向用户确认，并传 confirm=true。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "create", "update", "delete"],
            },
            "name": {"type": "string"},
            "transport": {"type": "string", "enum": ["stdio", "sse", "streamable_http"]},
            "command": {"type": "string"},
            "args": {"type": "array", "items": {"type": "string"}},
            "url": {"type": "string"},
            "env": {"type": "object"},
            "headers": {"type": "object"},
            "server_id": {"type": "string"},
            "confirm": {"type": "boolean", "default": False},
        },
        "required": ["action"],
    },
    handler=_manage_mcp_handler,  # type: ignore[assignment]
)
