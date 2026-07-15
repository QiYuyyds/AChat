"""MCP server management API — CRUD + test connection.

MCP servers are globally defined in the ``mcp_servers`` table and referenced
by agents via ``mcp_server_ids``. Secrets in ``headers`` / ``env`` are masked
in list responses. Test connection establishes a temporary MCP connection,
calls ``listTools()``, returns the preview, and closes the connection.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth.dependencies import get_current_user
from app.db.engine import get_db
from app.db.models import Agent, McpServer, User
from app.infra.cache_helpers import invalidate_agent_cache
from app.mcp.client_manager import McpClientManager, McpServerConfig
from app.utils.clock import now_ms
from app.utils.ids import new_mcp_server_id

router = APIRouter()

# Server name must be lowercase alphanumeric + underscore (namespaced tool names
# rely on this to produce valid ``mcp__<serverName>__<toolName>`` identifiers).
_NAME_RE = re.compile(r"^[a-z0-9_]+$")


# ─── Pydantic models ───────────────────────────────────────────────────────────


class McpServerCreate(BaseModel):
    name: str
    transport: str  # 'stdio' | 'sse' | 'streamable_http'
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    trust: str = "ask"
    enabled: bool = True


class McpServerUpdate(BaseModel):
    name: str | None = None
    transport: str | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    trust: str | None = None
    enabled: bool | None = None


class McpTestResult(BaseModel):
    ok: bool
    tools: list[dict] = Field(default_factory=list)
    error: str | None = None


# ─── Helpers ───────────────────────────────────────────────────────────────────


def _mask_secret(value: Any) -> Any:
    """Mask sensitive values: length > 20 and not ${...} → ****<last4>."""
    if isinstance(value, str):
        if value.startswith("${") and value.endswith("}"):
            return value
        if len(value) > 20:
            return f"****{value[-4:]}"
        return value
    if isinstance(value, dict):
        return {k: _mask_secret(v) for k, v in value.items()}
    return value


def _server_to_dict(row: McpServer) -> dict:
    """Serialize an McpServer row to a JSON-serializable dict with masked secrets."""
    return {
        "id": row.id,
        "name": row.name,
        "transport": row.transport,
        "command": row.command,
        "args": list(row.args) if row.args else [],
        "env": _mask_secret(dict(row.env) if row.env else {}),
        "url": row.url,
        "headers": _mask_secret(dict(row.headers) if row.headers else {}),
        "trust": row.trust,
        "enabled": row.enabled,
        "createdAt": row.created_at,
    }


def _validate_create(body: McpServerCreate) -> str | None:
    """Return an error message if the body is invalid, None if valid."""
    if not _NAME_RE.match(body.name):
        return "name must match [a-z0-9_] (lowercase alphanumeric + underscore)"
    if body.transport not in ("stdio", "sse", "streamable_http"):
        return "transport must be 'stdio', 'sse', or 'streamable_http'"
    if body.transport == "stdio" and not body.command:
        return "stdio transport requires 'command'"
    if body.transport in ("sse", "streamable_http") and not body.url:
        return f"{body.transport} transport requires 'url'"
    if body.trust not in ("always", "ask"):
        return "trust must be 'always' or 'ask'"
    return None


# ─── Routes ────────────────────────────────────────────────────────────────────


@router.get("/mcp/servers")
async def list_mcp_servers(user: User = Depends(get_current_user)) -> JSONResponse:
    """List all MCP servers with sensitive fields masked."""
    async with get_db() as db:
        result = await db.execute(select(McpServer).order_by(McpServer.created_at))
        rows = result.scalars().all()
    return JSONResponse({"servers": [_server_to_dict(r) for r in rows]})


@router.post("/mcp/servers")
async def create_mcp_server(body: McpServerCreate, user: User = Depends(get_current_user)) -> JSONResponse:
    """Create a new MCP server."""
    err = _validate_create(body)
    if err:
        return JSONResponse({"error": err}, status_code=400)

    server = McpServer(
        id=new_mcp_server_id(),
        name=body.name,
        transport=body.transport,
        command=body.command,
        args=body.args,
        env=body.env,
        url=body.url,
        headers=body.headers,
        trust=body.trust,
        enabled=body.enabled,
        created_at=now_ms(),
    )
    try:
        async with get_db() as db:
            db.add(server)
            await db.commit()
    except IntegrityError:
        return JSONResponse(
            {"error": f"MCP server with name '{body.name}' already exists"},
            status_code=409,
        )
    return JSONResponse({"server": _server_to_dict(server)})


@router.patch("/mcp/servers/{server_id}")
async def update_mcp_server(server_id: str, body: McpServerUpdate, user: User = Depends(get_current_user)) -> JSONResponse:
    """Update an existing MCP server."""
    async with get_db() as db:
        result = await db.execute(select(McpServer).where(McpServer.id == server_id))
        server = result.scalar_one_or_none()
        if server is None:
            return JSONResponse({"error": "MCP server not found"}, status_code=404)

        update_data = body.model_dump(exclude_none=True)
        if "name" in update_data and not _NAME_RE.match(update_data["name"]):
            return JSONResponse(
                {"error": "name must match [a-z0-9_]"},
                status_code=400,
            )
        if "transport" in update_data and update_data["transport"] not in (
            "stdio",
            "sse",
            "streamable_http",
        ):
            return JSONResponse(
                {"error": "transport must be 'stdio', 'sse', or 'streamable_http'"},
                status_code=400,
            )
        if "trust" in update_data and update_data["trust"] not in ("always", "ask"):
            return JSONResponse(
                {"error": "trust must be 'always' or 'ask'"},
                status_code=400,
            )

        for key, value in update_data.items():
            setattr(server, key, value)

        try:
            await db.commit()
        except IntegrityError:
            return JSONResponse(
                {"error": "MCP server with that name already exists"},
                status_code=409,
            )
        return JSONResponse({"server": _server_to_dict(server)})


@router.delete("/mcp/servers/{server_id}")
async def delete_mcp_server(server_id: str, user: User = Depends(get_current_user)) -> JSONResponse:
    """Delete an MCP server and remove it from all agents' mcp_server_ids."""
    async with get_db() as db:
        result = await db.execute(select(McpServer).where(McpServer.id == server_id))
        server = result.scalar_one_or_none()
        if server is None:
            return JSONResponse({"error": "MCP server not found"}, status_code=404)

        await db.delete(server)

        # Remove this server_id from all agents' mcp_server_ids arrays.
        # Can't use JSONB containment in WHERE — the column type (sqlalchemy
        # generic JSON) generates LIKE which is incompatible with jsonb.
        all_agents = (await db.execute(select(Agent))).scalars().all()
        for agent in all_agents:
            ids = agent.mcp_server_ids_list
            if server_id in ids:
                ids.remove(server_id)
                agent.mcp_server_ids_list = ids
                await invalidate_agent_cache(agent.id)

        await db.commit()
    return JSONResponse({"ok": True})


@router.post("/mcp/servers/{server_id}/test")
async def test_mcp_server(server_id: str, user: User = Depends(get_current_user)) -> JSONResponse:
    """Test connection: establish temporary connection, listTools, close."""
    async with get_db() as db:
        result = await db.execute(select(McpServer).where(McpServer.id == server_id))
        server = result.scalar_one_or_none()
        if server is None:
            return JSONResponse({"error": "MCP server not found"}, status_code=404)

        config = McpServerConfig(
            id=server.id,
            name=server.name,
            transport=server.transport,
            command=server.command,
            args=list(server.args) if server.args else [],
            env=dict(server.env) if server.env else None,
            url=server.url,
            headers=dict(server.headers) if server.headers else None,
            trust=server.trust,
        )

    manager = McpClientManager()
    try:
        await manager.connect_all([config])
        tools = await manager.list_tools_as_api()
        tool_preview = [
            {"name": t["function"]["name"], "description": t["function"]["description"]}
            for t in tools
        ]
        return JSONResponse({"ok": True, "tools": tool_preview})
    except Exception as err:  # noqa: BLE001 - surface connection error to UI
        return JSONResponse({"ok": False, "tools": [], "error": str(err)})
    finally:
        await manager.close_all()
