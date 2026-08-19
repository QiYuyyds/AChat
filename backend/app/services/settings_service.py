"""Per-user settings (API keys, companion config) + legacy singleton bridge.

Multi-user refactor: per-user API keys and companion config live in
``user_settings`` (PK = user_id). Server-level deployment config lives in
``global_settings`` (singleton). The legacy ``app_settings`` singleton is kept
as a backward-compat bridge: ``get_app_settings()`` reads from the first
available ``user_settings`` row, falling back to the old table.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
from typing import Literal, TypedDict

from sqlalchemy import select

from app.config import get_settings
from app.db.engine import get_remote_db
from app.db.models import AppSettings, UserSettings
from app.utils.clock import now_ms

SINGLETON_ID = "singleton"

CompanionMode = Literal["off", "lan", "tailnet"]

DEFAULT_COMPANION_PORT = 60646


def _empty_user_settings(user_id: str) -> UserSettings:
    return UserSettings(
        user_id=user_id,
        anthropic_api_key=None,
        anthropic_base_url=None,
        openai_api_key=None,
        deepseek_api_key=None,
        ark_api_key=None,
        companion_mode="off",
        mobile_device_token=None,
        obsidian_vault_path=None,
        rag_chunk_preset=None,
        rag_chunk_size=None,
        rag_chunk_overlap=None,
        ocr_engine=None,
        updated_at=0,
    )


def _empty_settings() -> AppSettings:
    """Legacy empty AppSettings for backward-compat callers."""
    return AppSettings(
        id=SINGLETON_ID,
        anthropic_api_key=None,
        anthropic_base_url=None,
        openai_api_key=None,
        deepseek_api_key=None,
        ark_api_key=None,
        companion_mode="off",
        mobile_device_token=None,
        deployment_publish_enabled=False,
        deployment_publish_dir=None,
        deployment_public_base_url=None,
        updated_at=0,
    )


# ─── Per-user settings ───────────────────────────────────────────────────────


async def get_user_settings(user_id: str) -> UserSettings:
    """Return the per-user settings row, or an all-default transient instance."""
    from app.infra.cache_helpers import get_user_settings_cached

    cached = await get_user_settings_cached(user_id)
    if cached is not None:
        return cached
    return _empty_user_settings(user_id)


class UserSettingsPatch(TypedDict, total=False):
    """Partial patch for per-user settings."""

    anthropic_api_key: str | None
    anthropic_base_url: str | None
    openai_api_key: str | None
    deepseek_api_key: str | None
    ark_api_key: str | None
    companion_mode: CompanionMode
    mobile_device_token: str | None
    obsidian_vault_path: str | None
    rag_chunk_preset: str | None
    rag_chunk_size: int | None
    rag_chunk_overlap: int | None
    ocr_engine: str | None


_USER_STRING_FIELDS = (
    "anthropic_api_key",
    "anthropic_base_url",
    "openai_api_key",
    "deepseek_api_key",
    "ark_api_key",
    "companion_mode",
    "mobile_device_token",
    "obsidian_vault_path",
    "rag_chunk_preset",
    "ocr_engine",
)

_USER_INT_FIELDS = (
    "rag_chunk_size",
    "rag_chunk_overlap",
)


async def update_user_settings(user_id: str, patch: UserSettingsPatch) -> UserSettings:
    """UPSERT per-user settings: keys in patch are written (None clears), absent leaves untouched."""
    async with get_remote_db() as db:
        result = await db.execute(
            select(UserSettings).where(UserSettings.user_id == user_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = _empty_user_settings(user_id)
            db.add(row)

        for field in _USER_STRING_FIELDS:
            if field in patch:
                setattr(row, field, _normalize(patch[field]))  # type: ignore[literal-required]

        for field in _USER_INT_FIELDS:
            if field in patch:
                setattr(row, field, patch[field])  # type: ignore[literal-required]

        if row.companion_mode is None:
            row.companion_mode = "off"

        if row.companion_mode != "off" and not row.mobile_device_token:
            row.mobile_device_token = new_mobile_device_token()

        row.updated_at = now_ms()
        await db.flush()
        db.expunge(row)

    from app.infra.cache_helpers import invalidate_user_settings_cache
    await invalidate_user_settings_cache(user_id)
    return row


async def regenerate_user_mobile_device_token(user_id: str) -> UserSettings:
    """Issue a fresh mobile pairing token for the user."""
    current = await get_user_settings(user_id)
    return await update_user_settings(
        user_id,
        {
            "mobile_device_token": new_mobile_device_token(),
            "companion_mode": current.companion_mode,  # type: ignore[typeddict-item]
        },
    )


# ─── Effective key resolution (per-user) ─────────────────────────────────────


async def get_effective_api_key(provider: str, user_id: str | None = None) -> str | None:
    """Effective key for a provider: user_settings → env var → None."""
    if user_id is not None:
        settings = await get_user_settings(user_id)
    else:
        settings = await get_app_settings()
    if provider == "anthropic":
        return settings.anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY")
    if provider == "openai":
        return settings.openai_api_key or os.environ.get("OPENAI_API_KEY")
    if provider == "deepseek":
        return settings.deepseek_api_key or os.environ.get("DEEPSEEK_API_KEY")
    if provider == "ark":
        return settings.ark_api_key or os.environ.get("ARK_API_KEY")
    return None


async def get_effective_anthropic_base_url(user_id: str | None = None) -> str | None:
    if user_id is not None:
        settings = await get_user_settings(user_id)
    else:
        settings = await get_app_settings()
    return settings.anthropic_base_url or os.environ.get("ANTHROPIC_BASE_URL")


# ─── Legacy singleton bridge (backward compat) ───────────────────────────────


async def get_app_settings() -> AppSettings:
    """Legacy bridge: read from the first user_settings row, fall back to app_settings.

    Used by code paths that haven't been updated to pass user_id yet.
    """
    async with get_remote_db() as db:
        # Try the first user_settings row
        result = await db.execute(select(UserSettings).limit(1))
        user_row = result.scalar_one_or_none()
        if user_row is not None:
            return _user_settings_to_app_settings(user_row)
        # Fall back to legacy app_settings table
        result = await db.execute(
            select(AppSettings).where(AppSettings.id == SINGLETON_ID)
        )
        row = result.scalar_one_or_none()
    return row if row is not None else _empty_settings()


def _user_settings_to_app_settings(us: UserSettings) -> AppSettings:
    """Project a UserSettings row onto an AppSettings shape for backward compat."""
    from app.services.global_settings_service import get_global_settings_sync

    gs = get_global_settings_sync()
    return AppSettings(
        id=SINGLETON_ID,
        anthropic_api_key=us.anthropic_api_key,
        anthropic_base_url=us.anthropic_base_url,
        openai_api_key=us.openai_api_key,
        deepseek_api_key=us.deepseek_api_key,
        ark_api_key=us.ark_api_key,
        companion_mode=us.companion_mode,
        mobile_device_token=us.mobile_device_token,
        deployment_publish_enabled=gs.deployment_publish_enabled if gs else False,
        deployment_publish_dir=gs.deployment_publish_dir if gs else None,
        deployment_public_base_url=gs.deployment_public_base_url if gs else None,
        updated_at=us.updated_at,
    )


# ─── Companion config ────────────────────────────────────────────────────────


def new_mobile_device_token() -> str:
    """24 random bytes, base64url (no padding)."""
    return base64.urlsafe_b64encode(secrets.token_bytes(24)).rstrip(b"=").decode("ascii")


def write_companion_config(
    companion_mode: CompanionMode,
    mobile_device_token: str | None,
    companion_port: int = DEFAULT_COMPANION_PORT,
) -> None:
    """Write ``<data_dir>/companion.json`` for the companion runtime."""
    data_dir = os.environ.get("AGENTHUB_DATA_DIR")
    base = data_dir if data_dir else str(get_settings().data_path)
    os.makedirs(base, exist_ok=True)
    config = {
        "companionMode": companion_mode,
        "mobileDeviceToken": mobile_device_token,
        "companionPort": companion_port,
    }
    with open(os.path.join(base, "companion.json"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(config, ensure_ascii=False, indent=2))


def sync_companion_runtime(settings: UserSettings | AppSettings) -> None:
    """Write companion.json and set/clear AGENTHUB_MOBILE_TOKEN env var."""
    write_companion_config(
        companion_mode=settings.companion_mode,  # type: ignore[arg-type]
        mobile_device_token=settings.mobile_device_token,
        companion_port=DEFAULT_COMPANION_PORT,
    )
    if settings.companion_mode != "off" and settings.mobile_device_token:
        os.environ["AGENTHUB_MOBILE_TOKEN"] = settings.mobile_device_token
    else:
        os.environ.pop("AGENTHUB_MOBILE_TOKEN", None)


# ─── Legacy AppSettings UPSERT (backward compat) ────────────────────────────


class AppSettingsPatch(TypedDict, total=False):
    """Legacy patch: covers both per-user and global fields."""

    anthropic_api_key: str | None
    anthropic_base_url: str | None
    openai_api_key: str | None
    deepseek_api_key: str | None
    ark_api_key: str | None
    companion_mode: CompanionMode
    mobile_device_token: str | None
    deployment_publish_enabled: bool
    deployment_publish_dir: str | None
    deployment_public_base_url: str | None


def _normalize(value: str | bool | None) -> str | bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    trimmed = value.strip()
    return None if trimmed == "" else trimmed


_STRING_FIELDS = (
    "anthropic_api_key",
    "anthropic_base_url",
    "openai_api_key",
    "deepseek_api_key",
    "ark_api_key",
    "companion_mode",
    "mobile_device_token",
    "deployment_publish_dir",
    "deployment_public_base_url",
)
_BOOL_FIELDS = ("deployment_publish_enabled",)


async def update_app_settings(patch: AppSettingsPatch) -> AppSettings:
    """Legacy UPSERT: writes per-user fields to the first user_settings row,
    and global fields to global_settings. Kept for callers not yet updated.
    """
    from app.services.global_settings_service import update_global_settings

    async with get_remote_db() as db:
        result = await db.execute(select(UserSettings).limit(1))
        row = result.scalar_one_or_none()
        if row is None:
            row = _empty_user_settings("legacy")
            db.add(row)

        # Per-user fields
        for field in _USER_STRING_FIELDS:
            if field in patch:
                setattr(row, field, _normalize(patch[field]))  # type: ignore[literal-required]
        for field in _USER_INT_FIELDS:
            if field in patch:
                setattr(row, field, patch[field])  # type: ignore[literal-required]
        if row.companion_mode is None:
            row.companion_mode = "off"
        if row.companion_mode != "off" and not row.mobile_device_token:
            row.mobile_device_token = new_mobile_device_token()

        row.updated_at = now_ms()
        await db.flush()
        db.expunge(row)

    from app.infra.cache_helpers import invalidate_user_settings_cache
    await invalidate_user_settings_cache(row.user_id)

    # Global fields
    global_patch: dict = {}
    for field in (*_BOOL_FIELDS, "deployment_publish_dir", "deployment_public_base_url"):
        if field in patch:
            global_patch[field] = patch[field]  # type: ignore[literal-required]
    if global_patch:
        await update_global_settings(global_patch)  # type: ignore[arg-type]

    sync_companion_runtime(row)
    return _user_settings_to_app_settings(row)


async def regenerate_mobile_device_token() -> AppSettings:
    """Legacy: issue a fresh mobile pairing token."""
    async with get_remote_db() as db:
        result = await db.execute(select(UserSettings).limit(1))
        row = result.scalar_one_or_none()
    user_id = row.user_id if row is not None else "legacy"
    await regenerate_user_mobile_device_token(user_id)
    return await get_app_settings()


async def get_mobile_device_token() -> str | None:
    """Legacy: current mobile pairing token."""
    settings = await get_app_settings()
    return settings.mobile_device_token
