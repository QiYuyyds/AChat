"""Settings API routes — per-user settings + global deployment config.

Multi-user refactor: per-user API keys and companion config are stored in
``user_settings`` (PK = user_id). Deployment publish config is server-level
and stored in ``global_settings`` (shared across all users).

Wire contract (camelCase, compatible with the React frontend):
- ``GET  /api/settings``               → 200 ``{ "settings": <merged row> }``
- ``PATCH /api/settings``              → 200 ``{ "settings": <merged row> }``
- ``POST /api/settings/mobile-token``  → 200 ``{ "settings": <merged row> }``
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.auth.dependencies import get_current_user
from app.db.models import GlobalSettings, User, UserSettings
from app.schemas import UpdateSettingsRequest
from app.services import settings_service
from app.services.global_settings_service import (
    GlobalSettingsPatch,
    get_global_settings,
    update_global_settings,
)
from app.services.settings_service import (
    UserSettingsPatch,
    get_user_settings,
    update_user_settings,
)

router = APIRouter()


def _serialize_user_settings(us: UserSettings) -> dict[str, Any]:
    return {
        "anthropicApiKey": us.anthropic_api_key,
        "anthropicBaseUrl": us.anthropic_base_url,
        "openaiApiKey": us.openai_api_key,
        "deepseekApiKey": us.deepseek_api_key,
        "arkApiKey": us.ark_api_key,
        "companionMode": us.companion_mode,
        "mobileDeviceToken": us.mobile_device_token,
        "obsidianVaultPath": us.obsidian_vault_path,
        "ragChunkPreset": us.rag_chunk_preset,
        "ragChunkSize": us.rag_chunk_size,
        "ragChunkOverlap": us.rag_chunk_overlap,
        "ocrEngine": us.ocr_engine,
        "updatedAt": us.updated_at,
    }


def _serialize_global_settings(gs: GlobalSettings) -> dict[str, Any]:
    return {
        "deploymentPublishEnabled": gs.deployment_publish_enabled,
        "deploymentPublishDir": gs.deployment_publish_dir,
        "deploymentPublicBaseUrl": gs.deployment_public_base_url,
    }


def _serialize_merged(us: UserSettings, gs: GlobalSettings) -> dict[str, Any]:
    """Merge per-user and global settings into a single wire shape (backward compat)."""
    merged = _serialize_user_settings(us)
    merged.update(_serialize_global_settings(gs))
    return merged


# Fields that belong to per-user settings
_USER_FIELDS = frozenset({
    "anthropic_api_key",
    "anthropic_base_url",
    "openai_api_key",
    "deepseek_api_key",
    "ark_api_key",
    "companion_mode",
    "mobile_device_token",
    "obsidian_vault_path",
    "rag_chunk_preset",
    "rag_chunk_size",
    "rag_chunk_overlap",
    "ocr_engine",
})

# Fields that belong to global settings
_GLOBAL_FIELDS = frozenset({
    "deployment_publish_enabled",
    "deployment_publish_dir",
    "deployment_public_base_url",
})


@router.get("/settings")
async def get_settings_endpoint(user: User = Depends(get_current_user)) -> JSONResponse:
    """Return per-user settings merged with global deployment config."""
    us = await get_user_settings(user.id)
    gs = await get_global_settings()
    return JSONResponse({"settings": _serialize_merged(us, gs)})


@router.patch("/settings")
async def update_settings_endpoint(
    request: Request,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """UPSERT a partial patch: per-user fields → user_settings, global fields → global_settings."""
    try:
        raw = await request.json()
    except Exception:
        raw = None

    if not isinstance(raw, dict):
        return JSONResponse(
            {"error": "Invalid body", "issues": []},
            status_code=400,
        )

    try:
        parsed = UpdateSettingsRequest.model_validate(raw)
    except ValidationError as exc:
        return JSONResponse(
            {"error": "Invalid body", "issues": exc.errors()},
            status_code=400,
        )

    sent = parsed.model_dump(by_alias=False)
    provided_fields = parsed.model_fields_set

    # Split patch into per-user and global
    user_patch: UserSettingsPatch = {}
    global_patch: GlobalSettingsPatch = {}

    for field in provided_fields:
        if field in _USER_FIELDS:
            user_patch[field] = sent[field]  # type: ignore[literal-required]
        elif field in _GLOBAL_FIELDS:
            global_patch[field] = sent[field]  # type: ignore[literal-required]

    if user_patch:
        us = await update_user_settings(user.id, user_patch)
    else:
        us = await get_user_settings(user.id)

    if global_patch:
        gs = await update_global_settings(global_patch)
    else:
        gs = await get_global_settings()

    return JSONResponse({"settings": _serialize_merged(us, gs)})


@router.post("/settings/mobile-token")
async def regenerate_mobile_token(
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Issue a fresh mobile pairing token for the current user."""
    us = await settings_service.regenerate_user_mobile_device_token(user.id)
    gs = await get_global_settings()
    return JSONResponse({"settings": _serialize_merged(us, gs)})


@router.get("/cache-metrics")
async def get_cache_metrics(
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Return aggregate prompt cache hit rate metrics for monitoring."""
    from app.infra.cache_metrics import cache_metrics

    return JSONResponse({
        "hit_rate": cache_metrics.recent_hit_rate(),
        "recent_requests": cache_metrics.recent_count,
        "alert": cache_metrics.should_alert(),
    })
