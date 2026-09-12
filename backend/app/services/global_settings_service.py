"""Global settings service — server-level config shared across all users.

Stores deployment publish config (enabled, dir, base URL) in a single-row
``global_settings`` table. All users share the same deployment configuration.
"""

from __future__ import annotations

from typing import TypedDict

from sqlalchemy import select

from app.db.engine import get_remote_db
from app.db.models import GlobalSettings
from app.utils.clock import now_ms

SINGLETON_ID = "singleton"

_global_cache: GlobalSettings | None = None


class GlobalSettingsPatch(TypedDict, total=False):
    deployment_publish_enabled: bool
    deployment_publish_dir: str | None
    deployment_public_base_url: str | None
    # Infra connection overrides (rag-infra-config): None = 未配置，回落 env
    milvus_host: str | None
    milvus_port: int | None
    neo4j_uri: str | None
    neo4j_user: str | None
    neo4j_password: str | None
    enable_graph: bool | None


def _empty_global_settings() -> GlobalSettings:
    return GlobalSettings(
        id=SINGLETON_ID,
        deployment_publish_enabled=False,
        deployment_publish_dir=None,
        deployment_public_base_url=None,
        updated_at=0,
    )


def _normalize_str(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    return None if trimmed == "" else trimmed


def get_global_settings_sync() -> GlobalSettings | None:
    """Return cached global settings (or None if not loaded yet)."""
    return _global_cache


async def get_global_settings() -> GlobalSettings:
    """Return the singleton global settings row, or an all-default transient instance.

    Uses Redis cache when available; falls back to in-memory cache then DB.
    """
    global _global_cache
    from app.infra.cache_helpers import get_global_settings_cached

    cached = await get_global_settings_cached()
    if cached is not None:
        _global_cache = cached
        return cached
    if _global_cache is not None:
        return _global_cache
    async with get_remote_db() as db:
        result = await db.execute(
            select(GlobalSettings).where(GlobalSettings.id == SINGLETON_ID)
        )
        row = result.scalar_one_or_none()
    if row is None:
        row = _empty_global_settings()
    _global_cache = row
    return row


async def update_global_settings(patch: GlobalSettingsPatch) -> GlobalSettings:
    """UPSERT global settings: keys in patch are written (None clears), absent leaves untouched."""
    global _global_cache
    async with get_remote_db() as db:
        result = await db.execute(
            select(GlobalSettings).where(GlobalSettings.id == SINGLETON_ID)
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = _empty_global_settings()
            db.add(row)

        if "deployment_publish_enabled" in patch:
            row.deployment_publish_enabled = bool(patch["deployment_publish_enabled"])
        if "deployment_publish_dir" in patch:
            row.deployment_publish_dir = _normalize_str(patch["deployment_publish_dir"])
        if "deployment_public_base_url" in patch:
            row.deployment_public_base_url = _normalize_str(
                patch["deployment_public_base_url"]
            )
        if "milvus_host" in patch:
            row.milvus_host = _normalize_str(patch["milvus_host"])
        if "milvus_port" in patch:
            row.milvus_port = patch["milvus_port"]
        if "neo4j_uri" in patch:
            row.neo4j_uri = _normalize_str(patch["neo4j_uri"])
        if "neo4j_user" in patch:
            row.neo4j_user = _normalize_str(patch["neo4j_user"])
        if "neo4j_password" in patch:
            row.neo4j_password = _normalize_str(patch["neo4j_password"])
        if "enable_graph" in patch:
            row.enable_graph = patch["enable_graph"]

        if row.deployment_publish_enabled is None:
            row.deployment_publish_enabled = False

        row.updated_at = now_ms()
        await db.flush()
        db.expunge(row)

    _global_cache = row
    from app.infra.cache_helpers import invalidate_global_settings_cache
    await invalidate_global_settings_cache()
    return row
