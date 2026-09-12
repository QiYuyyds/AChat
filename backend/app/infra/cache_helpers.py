"""Cached entity lookup helpers — process-internal dict TTL cache for remote cold data.

Agent and Workspace are read directly from local SQLite (0.1ms RTT, no cache needed).
UserSettings and GlobalSettings are read from remote PostgreSQL with a
process-internal dict TTL cache (5min) to avoid repeated 50ms round-trips.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import select

from app.db.engine import get_local_db, get_remote_db
from app.db.models import Agent, GlobalSettings, UserPreference, UserSettings, Workspace

logger = logging.getLogger(__name__)

# TTL constants (seconds)
_PROCESS_CACHE_TTL = 300  # 5 minutes

# ─── Process-internal dict cache for remote cold data ─────────────────
_process_cache: dict[str, tuple[float, Any]] = {}


def _cache_get(key: str) -> Any | None:
    """Return cached value if not expired, else None."""
    entry = _process_cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.monotonic() - ts > _PROCESS_CACHE_TTL:
        _process_cache.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: Any) -> None:
    """Store value in process cache with TTL."""
    if value is not None:
        _process_cache[key] = (time.monotonic(), value)


def _cache_del(key: str) -> None:
    """Remove a key from process cache (write-invalidation)."""
    _process_cache.pop(key, None)


# ─── Column names for serialization ─────────────────────────────────────
_AGENT_COLUMNS = [
    "id", "user_id", "name", "avatar", "description",
    "system_prompt", "adapter_name", "executable_path", "protocol_family",
    "is_builtin", "is_orchestrator", "is_guide", "memory_enabled",
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


# ─── Agent: direct local SQLite read (no cache needed, 0.1ms) ──────────


async def get_agent_cached(agent_id: str) -> Agent | None:
    """Return Agent by ID, reading directly from local SQLite."""
    async with get_local_db() as db:
        result = await db.execute(
            select(Agent).where(Agent.id == agent_id)
        )
        agent = result.scalar_one_or_none()
        if agent is None:
            return None
        return agent


async def invalidate_agent_cache(agent_id: str) -> None:
    """No-op: Agent is read directly from SQLite, no cache to invalidate."""
    pass


# ─── UserSettings: remote PG read + process-internal dict TTL cache ────

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
    """Return UserSettings by user_id, using process-internal dict TTL cache."""
    cache_key = f"user_settings:{user_id}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return _deserialize_user_settings(cached)

    async with get_remote_db() as db:
        result = await db.execute(
            select(UserSettings).where(UserSettings.user_id == user_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        data = _serialize_user_settings(row)
        _cache_set(cache_key, data)
        return row


async def invalidate_user_settings_cache(user_id: str) -> None:
    """Clear the process-internal cache entry for this user's settings."""
    _cache_del(f"user_settings:{user_id}")


# ─── Workspace: direct local SQLite read (no cache needed, 0.1ms) ──────

_WORKSPACE_COLUMNS = ["id", "conversation_id", "root_path", "mode", "bound_path", "env_preference", "created_at"]


def _serialize_workspace(ws: Workspace) -> dict[str, Any]:
    return {col: getattr(ws, col) for col in _WORKSPACE_COLUMNS}


def _deserialize_workspace(data: dict[str, Any]) -> Workspace:
    return Workspace(**{col: data.get(col) for col in _WORKSPACE_COLUMNS})


async def get_workspace_cached(conversation_id: str) -> Workspace | None:
    """Return Workspace by conversation_id, reading directly from local SQLite."""
    async with get_local_db() as db:
        result = await db.execute(
            select(Workspace).where(Workspace.conversation_id == conversation_id)
        )
        ws = result.scalar_one_or_none()
        if ws is None:
            return None
        return ws


async def invalidate_workspace_cache(conversation_id: str) -> None:
    """No-op: Workspace is read directly from SQLite, no cache to invalidate."""
    pass


# ─── GlobalSettings: remote PG read + process-internal dict TTL cache ──

_GLOBAL_SETTINGS_COLUMNS = [
    "id", "deployment_publish_enabled", "deployment_publish_dir",
    "deployment_public_base_url", "updated_at",
    # Infra connection overrides (rag-infra-config)
    "milvus_host", "milvus_port", "neo4j_uri", "neo4j_user",
    "neo4j_password", "enable_graph",
]


def _serialize_global_settings(gs: GlobalSettings) -> dict[str, Any]:
    return {col: getattr(gs, col) for col in _GLOBAL_SETTINGS_COLUMNS}


def _deserialize_global_settings(data: dict[str, Any]) -> GlobalSettings:
    return GlobalSettings(**{col: data.get(col) for col in _GLOBAL_SETTINGS_COLUMNS})


async def get_global_settings_cached() -> GlobalSettings | None:
    """Return singleton GlobalSettings, using process-internal dict TTL cache."""
    cache_key = "global_settings"
    cached = _cache_get(cache_key)
    if cached is not None:
        return _deserialize_global_settings(cached)

    async with get_remote_db() as db:
        result = await db.execute(
            select(GlobalSettings).where(GlobalSettings.id == "singleton")
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        data = _serialize_global_settings(row)
        _cache_set(cache_key, data)
        return row


async def invalidate_global_settings_cache() -> None:
    """Clear the process-internal cache entry for global settings."""
    _cache_del("global_settings")


# ─── UserPreference: remote PG read + process-internal dict TTL cache ──


async def get_user_preferences_cached(user_id: str) -> dict[str, str]:
    """Return all UserPreference key-value pairs for a user, using process-internal dict TTL cache.

    Returns a {key: value} dict. Cache is invalidated on write.
    """
    cache_key = f"user_preferences:{user_id}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return dict(cached)

    async with get_remote_db() as db:
        result = await db.execute(
            select(UserPreference).where(UserPreference.user_id == user_id)
        )
        rows = result.scalars().all()
        prefs = {r.key: r.value for r in rows}
        _cache_set(cache_key, dict(prefs))
        return prefs


async def invalidate_user_preferences_cache(user_id: str) -> None:
    """Clear the process-internal cache entry for this user's preferences."""
    _cache_del(f"user_preferences:{user_id}")
