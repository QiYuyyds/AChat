"""Cached entity lookup helpers — read-through Redis cache for low-churn entities.

Each function checks Redis first; on miss, queries PostgreSQL and backfills
Redis with TTL. On Redis unavailable, falls through to direct PG query.
Invalidation is handled by the caller (DEL on write).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from app.db.engine import get_db
from app.db.models import Agent, GlobalSettings, UserSettings, Workspace
from app.infra.cache import get_cache

logger = logging.getLogger(__name__)

# TTL constants (seconds)
AGENT_TTL = 300
USER_SETTINGS_TTL = 300
WORKSPACE_TTL = 300
GLOBAL_SETTINGS_TTL = 300

# Column names for serialization
_AGENT_COLUMNS = [
    "id", "user_id", "name", "avatar", "description",
    "system_prompt", "adapter_name", "model_provider", "model_id",
    "api_key", "api_base_url", "executable_path", "protocol_family",
    "is_builtin", "is_orchestrator", "supports_vision", "memory_enabled",
    "created_at",
]


def _serialize_agent(agent: Agent) -> dict[str, Any]:
    return {col: getattr(agent, col) for col in _AGENT_COLUMNS} | {
        "capabilities": agent.capabilities_list,
        "tool_names": agent.tool_names_list,
        "skill_names": agent.skill_names_list,
        "hook_names": agent.hook_names_list,
        "mcp_server_ids": agent.mcp_server_ids_list,
        "custom_args": list(agent.custom_args) if agent.custom_args else [],
    }


def _deserialize_agent(data: dict[str, Any]) -> Agent:
    agent = Agent(**{col: data.get(col) for col in _AGENT_COLUMNS})
    agent.capabilities = data.get("capabilities", [])
    agent.tool_names = data.get("tool_names", [])
    agent.skill_names = data.get("skill_names", [])
    agent.hook_names = data.get("hook_names", [])
    agent.mcp_server_ids = data.get("mcp_server_ids", [])
    agent.custom_args = data.get("custom_args", [])
    return agent


async def get_agent_cached(agent_id: str) -> Agent | None:
    """Return Agent by ID, using Redis cache when available."""
    cache = get_cache()
    key = f"agent:{agent_id}"

    async def _load() -> dict[str, Any] | None:
        async with get_db() as db:
            result = await db.execute(
                select(Agent).where(Agent.id == agent_id)
            )
            agent = result.scalar_one_or_none()
            if agent is None:
                return None
            return _serialize_agent(agent)

    data = await cache.get_or_load(key, AGENT_TTL, _load)
    if data is None:
        return None
    return _deserialize_agent(data)


async def invalidate_agent_cache(agent_id: str) -> None:
    cache = get_cache()
    await cache.delete(f"agent:{agent_id}")


_USER_SETTINGS_COLUMNS = [
    "user_id", "anthropic_api_key", "anthropic_base_url",
    "openai_api_key", "deepseek_api_key", "ark_api_key",
    "companion_mode", "mobile_device_token", "obsidian_vault_path", "updated_at",
]


def _serialize_user_settings(us: UserSettings) -> dict[str, Any]:
    return {col: getattr(us, col) for col in _USER_SETTINGS_COLUMNS}


def _deserialize_user_settings(data: dict[str, Any]) -> UserSettings:
    return UserSettings(**{col: data.get(col) for col in _USER_SETTINGS_COLUMNS})


async def get_user_settings_cached(user_id: str) -> UserSettings | None:
    """Return UserSettings by user_id, using Redis cache when available."""
    cache = get_cache()
    key = f"user_settings:{user_id}"

    async def _load() -> dict[str, Any] | None:
        async with get_db() as db:
            result = await db.execute(
                select(UserSettings).where(UserSettings.user_id == user_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return _serialize_user_settings(row)

    data = await cache.get_or_load(key, USER_SETTINGS_TTL, _load)
    if data is None:
        return None
    return _deserialize_user_settings(data)


async def invalidate_user_settings_cache(user_id: str) -> None:
    cache = get_cache()
    await cache.delete(f"user_settings:{user_id}")


_WORKSPACE_COLUMNS = ["id", "conversation_id", "root_path", "mode", "bound_path", "env_preference", "created_at"]


def _serialize_workspace(ws: Workspace) -> dict[str, Any]:
    return {col: getattr(ws, col) for col in _WORKSPACE_COLUMNS}


def _deserialize_workspace(data: dict[str, Any]) -> Workspace:
    return Workspace(**{col: data.get(col) for col in _WORKSPACE_COLUMNS})


async def get_workspace_cached(conversation_id: str) -> Workspace | None:
    """Return Workspace by conversation_id, using Redis cache when available."""
    cache = get_cache()
    key = f"workspace:{conversation_id}"

    async def _load() -> dict[str, Any] | None:
        async with get_db() as db:
            result = await db.execute(
                select(Workspace).where(Workspace.conversation_id == conversation_id)
            )
            ws = result.scalar_one_or_none()
            if ws is None:
                return None
            return _serialize_workspace(ws)

    data = await cache.get_or_load(key, WORKSPACE_TTL, _load)
    if data is None:
        return None
    return _deserialize_workspace(data)


async def invalidate_workspace_cache(conversation_id: str) -> None:
    cache = get_cache()
    await cache.delete(f"workspace:{conversation_id}")


_GLOBAL_SETTINGS_COLUMNS = [
    "id", "deployment_publish_enabled", "deployment_publish_dir",
    "deployment_public_base_url", "updated_at",
]


def _serialize_global_settings(gs: GlobalSettings) -> dict[str, Any]:
    return {col: getattr(gs, col) for col in _GLOBAL_SETTINGS_COLUMNS}


def _deserialize_global_settings(data: dict[str, Any]) -> GlobalSettings:
    return GlobalSettings(**{col: data.get(col) for col in _GLOBAL_SETTINGS_COLUMNS})


async def get_global_settings_cached() -> GlobalSettings | None:
    """Return singleton GlobalSettings, using Redis cache when available."""
    cache = get_cache()
    key = "global_settings"

    async def _load() -> dict[str, Any] | None:
        async with get_db() as db:
            result = await db.execute(
                select(GlobalSettings).where(GlobalSettings.id == "singleton")
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            return _serialize_global_settings(row)

    data = await cache.get_or_load(key, GLOBAL_SETTINGS_TTL, _load)
    if data is None:
        return None
    return _deserialize_global_settings(data)


async def invalidate_global_settings_cache() -> None:
    cache = get_cache()
    await cache.delete("global_settings")
