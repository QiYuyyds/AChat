"""manage_agents tool — list / create / update / delete user agents.

Reuses the existing CRUD functions in app.api.agents (which own the
serialization + validation logic). All operations are scoped by
ToolContext.user_id. Builtin agents are read-only (list allowed, modify/delete
rejected). Guide agents (is_guide=True) cannot be modified or deleted.
"""

from __future__ import annotations

from typing import Any

from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok
from app.tools.manage_base import emit_guide_side_effect

# ── Handler + sub-handlers ───────────────────────────────────────────────────


async def _manage_agents_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    action = args.get("action", "")
    user_id = ctx.user_id
    if user_id is None:
        return err("manage_agents requires a user context")

    if action == "list":
        return await _list_agents(args, user_id)
    elif action == "create":
        return await _create_agent(args, user_id, ctx)
    elif action == "update":
        return await _update_agent(args, user_id, ctx)
    elif action == "delete":
        return await _delete_agent(args, user_id, ctx)
    else:
        return err(f"Unknown action: {action}")


async def _list_agents(args: dict[str, Any], user_id: str) -> ToolResult:
    from sqlalchemy import or_, select

    from app.db.engine import get_db
    from app.db.models import Agent

    include_builtin = args.get("include_builtin", True)
    async with get_db() as db:
        query = select(Agent).where(
            or_(Agent.user_id.is_(None), Agent.user_id == user_id)
        )
        if not include_builtin:
            query = query.where(Agent.is_builtin.is_(False))
        query = query.order_by(Agent.is_builtin.desc(), Agent.created_at.desc())
        result = await db.execute(query)
        rows = result.scalars().all()

    from app.api.agents import _serialize

    return ok({"agents": [_serialize(r) for r in rows]})


async def _create_agent(args: dict[str, Any], user_id: str, ctx: ToolContext) -> ToolResult:
    from app.api.agents import _create_custom_agent
    from app.schemas import CreateAgentRequest

    adapter_name = args.get("adapter_name", "custom")
    if adapter_name != "custom":
        return err(
            "小A 只支持创建 Custom Agent（SDK 路线），不支持 Claude Code / Codex 等 CLI 类型。"
            "请使用 adapter_name=\"custom\"，并通过 model_provider / model_id / api_key 配置模型。"
        )

    body_data: dict[str, Any] = {
        "name": args.get("name", ""),
        "description": args.get("description", ""),
        "systemPrompt": args.get("system_prompt", ""),
        "adapterName": "custom",
        "modelProvider": args.get("model_provider"),
        "modelId": args.get("model_id"),
        "apiKey": args.get("api_key"),
        "apiBaseUrl": args.get("api_base_url"),
        "toolNames": args.get("tool_names", []),
        "skillNames": args.get("skill_names", []),
        "mcpServerIds": args.get("mcp_server_ids", []),
        "supportsVision": args.get("supports_vision", False),
        "isOrchestrator": args.get("is_orchestrator", False),
        "memoryEnabled": args.get("memory_enabled", False),
        "capabilities": [],
    }
    try:
        body = CreateAgentRequest.model_validate(body_data)
        row = await _create_custom_agent(body, user_id)
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"Failed to create agent: {e}")

    emit_guide_side_effect(ctx=ctx, target="agents", action="create")
    return ok({"agent": row, "message": f"已创建 Agent「{row['name']}」"})


async def _update_agent(args: dict[str, Any], user_id: str, ctx: ToolContext) -> ToolResult:

    from app.db.engine import get_db
    from app.db.models import Agent

    agent_id = args.get("agent_id")
    if not agent_id:
        return err("agent_id is required for update action")

    # Check the agent exists and is not builtin/guide
    async with get_db() as db:
        agent = await db.get(Agent, agent_id)
        if agent is None:
            return err(f"Agent not found: {agent_id}")
        if agent.user_id is not None and agent.user_id != user_id:
            return err(f"Agent not found: {agent_id}")
        if agent.is_builtin or getattr(agent, "is_guide", False):
            return err("不能修改 builtin 或 guide Agent")

    from app.api.agents import _update_custom_agent
    from app.schemas import UpdateAgentRequest

    updates_raw: dict[str, Any] = {}
    updates = args.get("updates", {})
    field_map = {
        "name": "name",
        "description": "description",
        "system_prompt": "systemPrompt",
        "model_provider": "modelProvider",
        "model_id": "modelId",
        "api_key": "apiKey",
        "api_base_url": "apiBaseUrl",
        "tool_names": "toolNames",
        "skill_names": "skillNames",
        "mcp_server_ids": "mcpServerIds",
        "supports_vision": "supportsVision",
        "is_orchestrator": "isOrchestrator",
        "memory_enabled": "memoryEnabled",
    }
    for py_key, wire_key in field_map.items():
        if py_key in updates:
            updates_raw[wire_key] = updates[py_key]

    if "adapter_name" in updates:
        updates_raw["adapterName"] = updates["adapter_name"]

    try:
        body = UpdateAgentRequest.model_validate(updates_raw)
        row = await _update_custom_agent(
            agent_id, body,
            has_adapter_name="adapterName" in updates_raw,
            adapter_name_patch=updates_raw.get("adapterName"),
            user_id=user_id,
        )
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(f"Failed to update agent: {e}")

    emit_guide_side_effect(ctx=ctx, target="agents", action="update")
    return ok({"agent": row, "message": f"已更新 Agent「{row['name']}」"})


async def _delete_agent(args: dict[str, Any], user_id: str, ctx: ToolContext) -> ToolResult:
    if not args.get("confirm", False):
        return err("删除操作需要先通过 ask_user 向用户确认，并传 confirm=true")

    agent_id = args.get("agent_id")
    if not agent_id:
        return err("agent_id is required for delete action")

    from app.db.engine import get_db
    from app.db.models import Agent

    async with get_db() as db:
        agent = await db.get(Agent, agent_id)
        if agent is None:
            return err(f"Agent not found: {agent_id}")
        if agent.user_id is not None and agent.user_id != user_id:
            return err(f"Agent not found: {agent_id}")
        if agent.is_builtin:
            return err("不能删除 builtin Agent")
        if getattr(agent, "is_guide", False):
            return err("不能删除 guide Agent")
        agent_name = agent.name

    from app.api.agents import _delete_custom_agent

    try:
        await _delete_custom_agent(agent_id, user_id)
    except ValueError as e:
        return err(str(e))

    emit_guide_side_effect(ctx=ctx, target="agents", action="delete")
    return ok({"message": f"已删除 Agent「{agent_name}」"})


# ── Tool definition ──────────────────────────────────────────────────────────

manage_agents_tool = ToolDef(
    name="manage_agents",
    description=(
        "管理用户 Agent：列表 / 创建 / 修改 / 删除。"
        "action: list | create | update | delete。"
        "delete 操作需要先通过 ask_user 向用户确认，并传 confirm=true。"
        "不能修改或删除 builtin Agent（is_builtin=true）和 guide Agent（is_guide=true）。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "create", "update", "delete"],
            },
            "include_builtin": {"type": "boolean", "default": True},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "system_prompt": {"type": "string"},
            "adapter_name": {
                "type": "string",
                "enum": ["custom", "claude-code", "codex"],
            },
            "model_provider": {"type": "string"},
            "model_id": {"type": "string"},
            "api_key": {"type": "string"},
            "api_base_url": {"type": "string"},
            "tool_names": {"type": "array", "items": {"type": "string"}},
            "skill_names": {"type": "array", "items": {"type": "string"}},
            "mcp_server_ids": {"type": "array", "items": {"type": "string"}},
            "supports_vision": {"type": "boolean"},
            "is_orchestrator": {"type": "boolean"},
            "memory_enabled": {"type": "boolean"},
            "agent_id": {"type": "string"},
            "updates": {"type": "object"},
            "confirm": {"type": "boolean", "default": False},
        },
        "required": ["action"],
    },
    handler=_manage_agents_handler,  # type: ignore[assignment]
)
