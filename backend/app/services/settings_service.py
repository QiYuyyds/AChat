"""Global API key / endpoint / deployment settings (single-row table).

Full port of src/server/settings-service.ts. The read side is used by the
deploy tools (publish config) and the adapters (effective API keys); the
UPSERT + companion-runtime sync side backs 阶段 6's ``/api/settings`` and
``/api/settings/mobile-token`` routes.

Read semantics for adapters: ``get_effective_api_key(provider)`` returns the
app_settings field, else the ``<PROVIDER>_API_KEY`` env var, else None.
``agents.api_key`` (per-agent override, highest priority) is handled by the
adapter layer.

Redaction note: the TS ``GET /api/settings`` returns key fields VERBATIM
(see src/app/api/settings/route.ts — "key 字段会原样返回；用户已自行选择填明文").
There is no server-side redaction in the source, so this port does not redact
either; the router stage must serialize the row as-is to stay byte-for-byte.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
from typing import Literal, TypedDict

from sqlalchemy import select

from app.config import get_settings
from app.db.engine import get_db
from app.db.models import AppSettings
from app.utils.clock import now_ms

SINGLETON_ID = "singleton"

CompanionMode = Literal["off", "lan", "tailnet"]

# Mirrors companion-config.ts DEFAULT_COMPANION_PORT.
DEFAULT_COMPANION_PORT = 60646


def _empty_settings() -> AppSettings:
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


async def get_app_settings() -> AppSettings:
    """Return the singleton settings row, or an all-default transient instance."""
    async with get_db() as db:
        result = await db.execute(
            select(AppSettings).where(AppSettings.id == SINGLETON_ID)
        )
        row = result.scalar_one_or_none()
    return row if row is not None else _empty_settings()


async def get_effective_api_key(provider: str) -> str | None:
    """Effective key for a provider: app_settings → env var → None."""
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


async def get_effective_anthropic_base_url() -> str | None:
    settings = await get_app_settings()
    return settings.anthropic_base_url or os.environ.get("ANTHROPIC_BASE_URL")


# --- Companion config (port of src/server/companion-config.ts) ---------------


def new_mobile_device_token() -> str:
    """24 random bytes, base64url (no padding) — matches randomBytes(24).toString('base64url')."""
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


def sync_companion_runtime(settings: AppSettings) -> None:
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


# --- UPSERT (port of updateAppSettings / regenerateMobileDeviceToken) --------


class AppSettingsPatch(TypedDict, total=False):
    """Partial patch: a key set to None clears the field, an absent key is left untouched."""

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
    """Empty/whitespace-only strings collapse to None; strings are trimmed; bools pass through."""
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
    """UPSERT the singleton row: keys present in ``patch`` are written (None clears,
    absent leaves untouched), then companion runtime is synced. Returns the new row.
    """
    async with get_db() as db:
        result = await db.execute(
            select(AppSettings).where(AppSettings.id == SINGLETON_ID)
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = AppSettings(
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
            db.add(row)

        for field in (*_STRING_FIELDS, *_BOOL_FIELDS):
            if field in patch:
                setattr(row, field, _normalize(patch[field]))  # type: ignore[literal-required]

        # companion_mode is non-nullable; a cleared/empty value falls back to 'off'.
        if row.companion_mode is None:
            row.companion_mode = "off"
        # deployment_publish_enabled is non-nullable; coerce a cleared value to False.
        if row.deployment_publish_enabled is None:
            row.deployment_publish_enabled = False

        if row.companion_mode != "off" and not row.mobile_device_token:
            row.mobile_device_token = new_mobile_device_token()

        row.updated_at = now_ms()
        await db.flush()
        db.expunge(row)

    sync_companion_runtime(row)
    return row


async def regenerate_mobile_device_token() -> AppSettings:
    """Issue a fresh mobile pairing token, preserving the current companion mode."""
    current = await get_app_settings()
    return await update_app_settings(
        {
            "mobile_device_token": new_mobile_device_token(),
            "companion_mode": current.companion_mode,  # type: ignore[typeddict-item]
        }
    )


async def get_mobile_device_token() -> str | None:
    """Current mobile pairing token, or None if unset."""
    settings = await get_app_settings()
    return settings.mobile_device_token
