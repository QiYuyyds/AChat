"""Settings API routes.

Port of src/app/api/settings/route.ts and src/app/api/settings/mobile-token/route.ts.

Wire contract (byte-for-byte with the unchanged React frontend, which types the
response as ``AppSettingsRow``):
- ``GET  /api/settings``               → 200 ``{ "settings": <full row> }``
- ``PATCH /api/settings``              → 200 ``{ "settings": <full row> }``;
                                          400 ``{ "error": "Invalid body", "issues": [...] }``
- ``POST /api/settings/mobile-token``  → 200 ``{ "settings": <full row> }``

The serialized row mirrors the Drizzle ``AppSettingsRow`` exactly (all columns,
including ``id`` and ``updatedAt``). Key fields are returned VERBATIM — the TS
source does not redact (see settings/route.ts: "key 字段会原样返回").
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.db.models import AppSettings
from app.schemas import UpdateSettingsRequest
from app.services import settings_service
from app.services.settings_service import AppSettingsPatch

router = APIRouter()


def _serialize(row: AppSettings) -> dict[str, Any]:
    """Full AppSettingsRow wire shape (camelCase), matching the Drizzle select row."""
    return {
        "id": row.id,
        "anthropicApiKey": row.anthropic_api_key,
        "anthropicBaseUrl": row.anthropic_base_url,
        "openaiApiKey": row.openai_api_key,
        "deepseekApiKey": row.deepseek_api_key,
        "arkApiKey": row.ark_api_key,
        "companionMode": row.companion_mode,
        "mobileDeviceToken": row.mobile_device_token,
        "deploymentPublishEnabled": row.deployment_publish_enabled,
        "deploymentPublishDir": row.deployment_publish_dir,
        "deploymentPublicBaseUrl": row.deployment_public_base_url,
        "updatedAt": row.updated_at,
    }


@router.get("/settings")
async def get_settings() -> JSONResponse:
    """Return global app settings (keys returned verbatim, as the TS source does)."""
    row = await settings_service.get_app_settings()
    return JSONResponse({"settings": _serialize(row)})


# Fields a PATCH may carry; only keys actually present in the body are applied
# (matching the TS Object.entries(patch) semantics: undefined = leave untouched,
# explicit null = clear).
_PATCH_FIELDS: tuple[str, ...] = (
    "anthropic_api_key",
    "anthropic_base_url",
    "openai_api_key",
    "deepseek_api_key",
    "ark_api_key",
    "companion_mode",
    "mobile_device_token",
    "deployment_publish_enabled",
    "deployment_publish_dir",
    "deployment_public_base_url",
)


@router.patch("/settings")
async def update_settings(request: Request) -> JSONResponse:
    """UPSERT a partial patch onto the singleton settings row."""
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

    # Only forward keys the client actually sent, so an absent field stays
    # untouched while an explicit null clears it. model_fields_set holds the
    # canonical (snake_case) field names regardless of whether the input used
    # the camelCase alias.
    sent = parsed.model_dump(by_alias=False)
    provided_fields = parsed.model_fields_set

    patch: AppSettingsPatch = {
        field: sent[field]  # type: ignore[literal-required]
        for field in _PATCH_FIELDS
        if field in provided_fields
    }

    row = await settings_service.update_app_settings(patch)
    return JSONResponse({"settings": _serialize(row)})


@router.post("/settings/mobile-token")
async def regenerate_mobile_token() -> JSONResponse:
    """Issue a fresh mobile pairing token, preserving the current companion mode."""
    row = await settings_service.regenerate_mobile_device_token()
    return JSONResponse({"settings": _serialize(row)})
