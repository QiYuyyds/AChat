"""ModelProfile CRUD + connectivity test API routes."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.adapters.custom_provider_client import (
    validate_openai_compatible_api_key,
    validate_openai_compatible_base_url,
)
from app.auth.dependencies import get_current_user
from app.db.engine import get_local_db
from app.db.models import ModelProfile, User
from app.schemas.model_profile import (
    CreateModelProfileRequest,
    UpdateModelProfileRequest,
    _mask_key,
)
from app.utils.clock import now_ms
from app.utils.ids import new_model_profile_id

router = APIRouter()

# Per-profile test cooldown: min 3s between tests.
_test_cooldowns: dict[str, float] = {}
_TEST_COOLDOWN_S = 3.0


def _serialize(row: ModelProfile) -> dict[str, Any]:
    """Serialize a ModelProfile row to wire shape (api_key masked)."""
    return {
        "id": row.id,
        "name": row.name,
        "provider": row.provider,
        "modelId": row.model_id,
        "apiKeyMasked": _mask_key(row.api_key),
        "apiBaseUrl": row.api_base_url,
        "isDefault": row.is_default,
        "supportsVision": row.supports_vision,
        "lastTestStatus": row.last_test_status,
        "lastTestedAt": row.last_tested_at,
        "createdAt": row.created_at,
        "updatedAt": row.updated_at,
    }


# ─── GET /api/model-profiles ──────────────────────────────────────
@router.get("/model-profiles")
async def list_model_profiles(user: User = Depends(get_current_user)) -> JSONResponse:
    """List all ModelProfiles for the current user."""
    async with get_local_db() as db:
        rows = (
            await db.execute(
                select(ModelProfile)
                .where(ModelProfile.user_id == user.id)
                .order_by(ModelProfile.created_at.asc())
            )
        ).scalars().all()
        return JSONResponse({"profiles": [_serialize(r) for r in rows]})


# ─── POST /api/model-profiles ─────────────────────────────────────
@router.post("/model-profiles")
async def create_model_profile(
    request: Request, user: User = Depends(get_current_user)
) -> JSONResponse:
    """Create a new ModelProfile."""
    try:
        raw = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid body"}, status_code=400)

    try:
        body = CreateModelProfileRequest.model_validate(raw)
    except ValidationError as exc:
        return JSONResponse(
            {"error": "Invalid body", "issues": exc.errors()}, status_code=400
        )

    # Validate openai-compatible fields
    base_url_error = validate_openai_compatible_base_url(body.provider, body.api_base_url)
    if base_url_error:
        return JSONResponse({"error": base_url_error}, status_code=400)
    api_key_error = validate_openai_compatible_api_key(body.provider, body.api_key)
    if api_key_error:
        return JSONResponse({"error": api_key_error}, status_code=400)

    now = now_ms()
    profile = ModelProfile(
        id=new_model_profile_id(),
        user_id=user.id,
        name=body.name.strip(),
        provider=body.provider,
        model_id=body.model_id.strip(),
        api_key=(body.api_key.strip() if body.api_key else None),
        api_base_url=(body.api_base_url.strip() if body.api_base_url else None) or None,
        is_default=False,
        supports_vision=body.supports_vision or False,
        last_test_status="untested",
        last_tested_at=None,
        created_at=now,
        updated_at=now,
    )

    async with get_local_db() as db:
        # Check if user has zero profiles → this becomes default
        existing_count = (
            await db.execute(
                select(ModelProfile).where(ModelProfile.user_id == user.id)
            )
        ).scalars().all()
        if len(existing_count) == 0:
            profile.is_default = True
        elif body.is_default:
            # Unset existing default
            for existing in existing_count:
                if existing.is_default:
                    existing.is_default = False
                    db.add(existing)
            profile.is_default = True

        db.add(profile)
        try:
            await db.flush()
        except IntegrityError:
            return JSONResponse(
                {"error": "A profile with this name already exists"}, status_code=400
            )
        result = _serialize(profile)

    return JSONResponse({"profile": result}, status_code=201)


# ─── PATCH /api/model-profiles/{id} ───────────────────────────────
@router.patch("/model-profiles/{profile_id}")
async def update_model_profile(
    profile_id: str, request: Request, user: User = Depends(get_current_user)
) -> JSONResponse:
    """Update a ModelProfile."""
    try:
        raw = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid body"}, status_code=400)

    try:
        body = UpdateModelProfileRequest.model_validate(raw)
    except ValidationError as exc:
        return JSONResponse(
            {"error": "Invalid body", "issues": exc.errors()}, status_code=400
        )

    async with get_local_db() as db:
        profile = await db.get(ModelProfile, profile_id)
        if profile is None or profile.user_id != user.id:
            return JSONResponse({"error": "Profile not found"}, status_code=404)

        provided = body.model_fields_set

        if "name" in provided and body.name is not None:
            profile.name = body.name.strip()
        if "provider" in provided and body.provider is not None:
            profile.provider = body.provider
        if "model_id" in provided and body.model_id is not None:
            profile.model_id = body.model_id.strip()
        if "api_key" in provided and body.api_key is not None:
            profile.api_key = body.api_key.strip() or None
        if "api_base_url" in provided and body.api_base_url is not None:
            profile.api_base_url = body.api_base_url.strip() or None
        if "supports_vision" in provided and body.supports_vision is not None:
            profile.supports_vision = body.supports_vision

        # Validate openai-compatible fields after merge
        base_url_error = validate_openai_compatible_base_url(
            profile.provider, profile.api_base_url
        )
        if base_url_error:
            return JSONResponse({"error": base_url_error}, status_code=400)
        api_key_error = validate_openai_compatible_api_key(
            profile.provider, profile.api_key
        )
        if api_key_error:
            return JSONResponse({"error": api_key_error}, status_code=400)

        # Handle default toggle
        if "is_default" in provided and body.is_default is not None:
            if body.is_default and not profile.is_default:
                # Unset existing default
                existing = (
                    await db.execute(
                        select(ModelProfile)
                        .where(
                            ModelProfile.user_id == user.id,
                            ModelProfile.is_default == True,  # noqa: E712
                            ModelProfile.id != profile_id,
                        )
                    )
                ).scalars().all()
                for other in existing:
                    other.is_default = False
                    db.add(other)
                profile.is_default = True
            elif not body.is_default and profile.is_default:
                # Don't allow unsetting default directly — will be handled
                # by the next profile becoming default on delete.
                # But if user explicitly unsets, allow it.
                profile.is_default = False

        profile.updated_at = now_ms()
        db.add(profile)
        await db.flush()
        result = _serialize(profile)

    return JSONResponse({"profile": result})


# ─── DELETE /api/model-profiles/{id} ──────────────────────────────
@router.delete("/model-profiles/{profile_id}")
async def delete_model_profile(
    profile_id: str, user: User = Depends(get_current_user)
) -> JSONResponse:
    """Delete a ModelProfile. If deleting the default, auto-assign new default."""
    async with get_local_db() as db:
        profile = await db.get(ModelProfile, profile_id)
        if profile is None or profile.user_id != user.id:
            return JSONResponse({"error": "Profile not found"}, status_code=404)

        was_default = profile.is_default
        await db.delete(profile)
        await db.flush()

        # Auto-assign new default if we deleted the default
        if was_default:
            remaining = (
                await db.execute(
                    select(ModelProfile)
                    .where(ModelProfile.user_id == user.id)
                    .order_by(ModelProfile.created_at.asc())
                )
            ).scalars().first()
            if remaining:
                remaining.is_default = True
                db.add(remaining)
                await db.flush()

    return JSONResponse({"ok": True})


# ─── POST /api/model-profiles/{id}/test ───────────────────────────
@router.post("/model-profiles/{profile_id}/test")
async def test_model_profile(
    profile_id: str, user: User = Depends(get_current_user)
) -> JSONResponse:
    """Test connectivity: send a minimal chat completion ping."""
    # Rate limit: 3s cooldown per profile
    now_ts = time.monotonic()
    last_test = _test_cooldowns.get(profile_id)
    if last_test and (now_ts - last_test) < _TEST_COOLDOWN_S:
        return JSONResponse(
            {
                "error": f"Please wait {_TEST_COOLDOWN_S}s between tests for the same profile"
            },
            status_code=429,
        )

    async with get_local_db() as db:
        profile = await db.get(ModelProfile, profile_id)
        if profile is None or profile.user_id != user.id:
            return JSONResponse({"error": "Profile not found"}, status_code=404)

        # Resolve client config
        from openai import AsyncOpenAI

        from app.adapters.custom_provider_client import (
            resolve_custom_provider_client_config,
        )

        try:
            client_config = resolve_custom_provider_client_config(
                profile.provider, profile.api_key, profile.api_base_url
            )
        except ValueError as exc:
            _update_test_status(db, profile, "fail")
            return JSONResponse(
                {"testResult": {"status": "fail", "latencyMs": 0, "error": str(exc)}}
            )

        client = AsyncOpenAI(
            api_key=client_config.api_key,
            base_url=client_config.base_url,
            max_retries=0,
        )

        start = time.monotonic()
        try:
            await client.chat.completions.create(
                model=profile.model_id,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
            )
            latency_ms = int((time.monotonic() - start) * 1000)
            _update_test_status(db, profile, "ok", now_ms())
            _test_cooldowns[profile_id] = now_ts
            return JSONResponse(
                {
                    "testResult": {
                        "status": "ok",
                        "latencyMs": latency_ms,
                        "error": None,
                    }
                }
            )
        except Exception as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            _update_test_status(db, profile, "fail", now_ms())
            _test_cooldowns[profile_id] = now_ts
            return JSONResponse(
                {
                    "testResult": {
                        "status": "fail",
                        "latencyMs": latency_ms,
                        "error": str(exc)[:500],
                    }
                }
            )


def _update_test_status(
    db, profile: ModelProfile, status: str, tested_at: int | None = None
) -> None:
    """Update the profile's test status fields."""
    profile.last_test_status = status
    profile.last_tested_at = tested_at
    profile.updated_at = now_ms()
    db.add(profile)
