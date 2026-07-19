"""Mirror cloud conversations / agents into the desktop engine local DB.

Desktop online: conversations are created on the official API; the local engine
receives send-message / run traffic and needs matching rows for ownership checks
and AgentRunner. This module pulls cloud state over HTTPS and UPSERTs locally.
Workspace paths are always local (data-dir workspaces), never cloud paths.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from sqlalchemy import select

from app.db.engine import get_db
from app.db.models import Agent, Conversation, Message, Workspace
from app.desktop.cloud_client import get_cloud_client, get_cloud_session
from app.utils.clock import now_ms
from app.utils.ids import new_workspace_id

logger = logging.getLogger(__name__)


async def ensure_conversation_context(conversation_id: str, user_id: str) -> Conversation:
    """Ensure conversation + agents (+ optional recent messages) exist locally."""
    await ensure_agents_mirrored(user_id)
    conv = await _get_local_conversation(conversation_id)
    if conv is None:
        cloud_conv = await _fetch_cloud_conversation(conversation_id)
        if cloud_conv is None:
            raise ValueError(f"Conversation not found on cloud: {conversation_id}")
        conv = await _upsert_conversation(cloud_conv, user_id)
    await _ensure_workspace(conversation_id, user_id)
    # Best-effort history for SDK adapters; failures should not block send.
    try:
        await mirror_recent_messages(conversation_id)
    except Exception as e:
        logger.warning("mirror messages failed conversation=%s: %s", conversation_id, e)
    return conv


async def ensure_agents_mirrored(user_id: str) -> int:
    client = get_cloud_client()
    if not get_cloud_session().is_authenticated:
        return 0
    try:
        data = await client.get_json("/api/agents")
    except Exception as e:
        logger.warning("fetch agents failed: %s", e)
        return 0
    agents = data.get("agents") if isinstance(data, dict) else None
    if not isinstance(agents, list):
        return 0
    count = 0
    for raw in agents:
        if not isinstance(raw, dict):
            continue
        await _upsert_agent(raw, user_id)
        count += 1
    return count


async def mirror_recent_messages(conversation_id: str) -> int:
    client = get_cloud_client()
    try:
        data = await client.get_json(f"/api/conversations/{conversation_id}/messages")
    except Exception as e:
        logger.warning("fetch messages failed: %s", e)
        return 0
    messages = data.get("messages") if isinstance(data, dict) else None
    if not isinstance(messages, list):
        return 0
    n = 0
    for raw in messages:
        if isinstance(raw, dict):
            await _upsert_local_message(raw, conversation_id)
            n += 1
    return n


async def _get_local_conversation(conversation_id: str) -> Conversation | None:
    async with get_db() as db:
        result = await db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        row = result.scalar_one_or_none()
        if row is not None:
            db.expunge(row)
        return row


async def _fetch_cloud_conversation(conversation_id: str) -> dict[str, Any] | None:
    """Cloud has list but not always single-get; scan list then probe messages."""
    client = get_cloud_client()
    try:
        data = await client.get_json("/api/conversations")
    except Exception as e:
        logger.warning("fetch conversations failed: %s", e)
        data = None
    items = data.get("conversations") if isinstance(data, dict) else None
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and item.get("id") == conversation_id:
                return item
    # Fallback: if messages endpoint is readable, synthesize a minimal cloud row
    # so the local engine can own/run against this conversation id.
    try:
        await client.get_json(f"/api/conversations/{conversation_id}/messages")
        return {
            "id": conversation_id,
            "title": "Conversation",
            "mode": "single",
            "agentIds": [],
        }
    except Exception as e:
        logger.warning(
            "cloud conversation %s not found via list/messages: %s",
            conversation_id,
            e,
        )
        return None



async def _upsert_conversation(raw: dict[str, Any], user_id: str) -> Conversation:
    now = now_ms()
    cid = str(raw["id"])
    async with get_db() as db:
        result = await db.execute(select(Conversation).where(Conversation.id == cid))
        row = result.scalar_one_or_none()
        if row is None:
            row = Conversation(
                id=cid,
                user_id=user_id,
                title=str(raw.get("title") or "Conversation"),
                mode=str(raw.get("mode") or "single"),
                archived=bool(raw.get("archived") or False),
                pinned_at=raw.get("pinnedAt") or raw.get("pinned_at"),
                fs_write_approval_mode=str(
                    raw.get("fsWriteApprovalMode")
                    or raw.get("fs_write_approval_mode")
                    or "review"
                ),
                rag_enabled=bool(raw.get("ragEnabled") or raw.get("rag_enabled") or False),
                summary=raw.get("summary"),
                dispatch_mode=str(
                    raw.get("dispatchMode") or raw.get("dispatch_mode") or "solo"
                ),
                created_at=int(raw.get("createdAt") or raw.get("created_at") or now),
                updated_at=int(raw.get("updatedAt") or raw.get("updated_at") or now),
            )
            agent_ids = raw.get("agentIds") or raw.get("agent_ids") or []
            row.agent_ids_list = list(agent_ids) if isinstance(agent_ids, list) else []
            pinned = raw.get("pinnedMessageIds") or raw.get("pinned_message_ids") or []
            row.pinned_message_ids_list = list(pinned) if isinstance(pinned, list) else []
            bookmarked = (
                raw.get("bookmarkedMessageIds") or raw.get("bookmarked_message_ids") or []
            )
            row.bookmarked_message_ids_list = (
                list(bookmarked) if isinstance(bookmarked, list) else []
            )
            db.add(row)
        else:
            row.title = str(raw.get("title") or row.title)
            row.mode = str(raw.get("mode") or row.mode)
            row.user_id = user_id
            agent_ids = raw.get("agentIds") or raw.get("agent_ids")
            if isinstance(agent_ids, list):
                row.agent_ids_list = list(agent_ids)
            row.updated_at = int(raw.get("updatedAt") or raw.get("updated_at") or now)
        await db.flush()
        db.expunge(row)
        return row


async def _upsert_agent(raw: dict[str, Any], user_id: str) -> None:
    aid = str(raw.get("id") or "")
    if not aid:
        return
    now = now_ms()
    is_builtin = bool(raw.get("isBuiltin") if "isBuiltin" in raw else raw.get("is_builtin"))
    async with get_db() as db:
        result = await db.execute(select(Agent).where(Agent.id == aid))
        row = result.scalar_one_or_none()
        caps = raw.get("capabilities") or []
        tools = raw.get("toolNames") or raw.get("tool_names") or []
        skills = raw.get("skillNames") or raw.get("skill_names") or []
        mcp_ids = raw.get("mcpServerIds") or raw.get("mcp_server_ids") or []
        custom_args = raw.get("customArgs") or raw.get("custom_args") or []
        fields = {
            "name": str(raw.get("name") or "Agent"),
            "avatar": str(raw.get("avatar") or "🤖"),
            "description": str(raw.get("description") or ""),
            "system_prompt": str(
                raw.get("systemPrompt") or raw.get("system_prompt") or ""
            ),
            "adapter_name": str(
                raw.get("adapterName") or raw.get("adapter_name") or "custom"
            ),
            "model_provider": raw.get("modelProvider") or raw.get("model_provider"),
            "model_id": raw.get("modelId") or raw.get("model_id"),
            "api_key": raw.get("apiKey") or raw.get("api_key"),
            "api_base_url": raw.get("apiBaseUrl") or raw.get("api_base_url"),
            "executable_path": raw.get("executablePath") or raw.get("executable_path"),
            "protocol_family": raw.get("protocolFamily") or raw.get("protocol_family"),
            "is_builtin": is_builtin,
            "is_orchestrator": bool(
                raw.get("isOrchestrator")
                if "isOrchestrator" in raw
                else raw.get("is_orchestrator")
            ),
            "supports_vision": bool(
                raw.get("supportsVision")
                if "supportsVision" in raw
                else raw.get("supports_vision")
            ),
            "memory_enabled": bool(
                raw.get("memoryEnabled")
                if "memoryEnabled" in raw
                else raw.get("memory_enabled")
            ),
            "user_id": None if is_builtin else user_id,
        }
        if row is None:
            row = Agent(
                id=aid,
                created_at=int(raw.get("createdAt") or raw.get("created_at") or now),
                **fields,
            )
            row.capabilities_list = list(caps) if isinstance(caps, list) else []
            row.tool_names_list = list(tools) if isinstance(tools, list) else []
            row.skill_names_list = list(skills) if isinstance(skills, list) else []
            row.mcp_server_ids_list = list(mcp_ids) if isinstance(mcp_ids, list) else []
            row.custom_args_list = list(custom_args) if isinstance(custom_args, list) else []
            db.add(row)
        else:
            for k, v in fields.items():
                setattr(row, k, v)
            if isinstance(caps, list):
                row.capabilities_list = list(caps)
            if isinstance(tools, list):
                row.tool_names_list = list(tools)
            if isinstance(skills, list):
                row.skill_names_list = list(skills)
            if isinstance(mcp_ids, list):
                row.mcp_server_ids_list = list(mcp_ids)
            if isinstance(custom_args, list):
                row.custom_args_list = list(custom_args)
        await db.flush()


async def _ensure_workspace(conversation_id: str, user_id: str) -> None:
    from app.services.conversation_service import _workspaces_root

    root = os.path.join(_workspaces_root(user_id), conversation_id)
    os.makedirs(root, exist_ok=True)
    async with get_db() as db:
        result = await db.execute(
            select(Workspace).where(Workspace.conversation_id == conversation_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            db.add(
                Workspace(
                    id=new_workspace_id(),
                    conversation_id=conversation_id,
                    root_path=root,
                    mode="sandbox",
                    bound_path=None,
                    created_at=now_ms(),
                )
            )
        else:
            # Keep local root_path under desktop data dir.
            row.root_path = root


async def _upsert_local_message(raw: dict[str, Any], conversation_id: str) -> None:
    mid = str(raw.get("id") or "")
    if not mid:
        return
    async with get_db() as db:
        result = await db.execute(select(Message).where(Message.id == mid))
        row = result.scalar_one_or_none()
        parts = raw.get("parts") or []
        mentioned = raw.get("mentionedAgentIds") or raw.get("mentioned_agent_ids") or []
        if row is None:
            msg = Message(
                id=mid,
                conversation_id=str(raw.get("conversationId") or conversation_id),
                role=str(raw.get("role") or "user"),
                agent_id=raw.get("agentId") or raw.get("agent_id"),
                status=str(raw.get("status") or "complete"),
                parent_message_id=raw.get("parentMessageId") or raw.get("parent_message_id"),
                run_id=raw.get("runId") or raw.get("run_id"),
                usage=raw.get("usage"),
                hidden=bool(raw.get("hidden") or False),
                created_at=int(raw.get("createdAt") or raw.get("created_at") or now_ms()),
            )
            msg.parts_list = list(parts) if isinstance(parts, list) else []
            msg.mentioned_agent_ids_list = (
                list(mentioned) if isinstance(mentioned, list) else []
            )
            db.add(msg)
        else:
            if isinstance(parts, list):
                row.parts_list = list(parts)
            row.status = str(raw.get("status") or row.status)
            if "usage" in raw:
                row.usage = raw.get("usage")
