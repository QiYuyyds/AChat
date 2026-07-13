"""Profile API — personal info fields + avatar upload/serving."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import select

from app.auth.dependencies import get_current_user
from app.config import get_settings
from app.db.engine import get_db
from app.db.models import User, UserPreference
from app.memory.preference import Preference

logger = logging.getLogger(__name__)

router = APIRouter()

_PROFILE_KEYS = {
    "name": "姓名",
    "location": "所在地",
    "hometown": "家乡",
    "preferences": "喜好",
    "bio": "简介",
}

_ALLOWED_IMAGE_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}

_AVATAR_MAX_SIZE = 2 * 1024 * 1024  # 2MB


@router.get("/profile")
async def get_profile(user: User = Depends(get_current_user)) -> JSONResponse:
    """Read the current user's personal profile fields and avatar URL."""
    prefs = await _read_profile_prefs(user.id)
    return JSONResponse({
        "name": prefs.get("姓名"),
        "location": prefs.get("所在地"),
        "hometown": prefs.get("家乡"),
        "preferences": prefs.get("喜好"),
        "bio": prefs.get("简介"),
        "avatarUrl": user.avatar_url,
    })


@router.put("/profile")
async def update_profile(
    body: dict,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Update personal profile fields. null clears a field."""
    pref = Preference(user_id=user.id)
    updates: dict[str, str | None] = {}
    for field, canonical_key in _PROFILE_KEYS.items():
        if field not in body:
            continue
        value = body[field]
        if value is None:
            await pref.delete(canonical_key)
        else:
            await pref.set(canonical_key, str(value), source="manual")
        updates[field] = value

    prefs = await _read_profile_prefs(user.id)
    return JSONResponse({
        "name": prefs.get("姓名"),
        "location": prefs.get("所在地"),
        "hometown": prefs.get("家乡"),
        "preferences": prefs.get("喜好"),
        "bio": prefs.get("简介"),
        "avatarUrl": user.avatar_url,
    })


@router.post("/profile/avatar")
async def upload_avatar(
    file: UploadFile,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Upload an avatar image (multipart/form-data, field 'file')."""
    if file.content_type not in _ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid image type. Allowed: png, jpeg, webp, gif.",
        )

    data = await file.read()
    if len(data) > _AVATAR_MAX_SIZE:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Avatar image must be 2MB or smaller.",
        )

    settings = get_settings()
    ext = _ALLOWED_IMAGE_TYPES[file.content_type]
    avatar_dir = settings.data_path / "users" / user.id / "avatar"
    avatar_dir.mkdir(parents=True, exist_ok=True)

    # Delete previous avatar files
    _cleanup_avatar_dir(avatar_dir)

    filename = f"{int(time.time())}{ext}"
    filepath = avatar_dir / filename
    filepath.write_bytes(data)

    async with get_db() as session:
        result = await session.execute(select(User).where(User.id == user.id))
        db_user = result.scalar_one()
        db_user.avatar_url = "/api/profile/avatar"
        db_user.updated_at = int(time.time() * 1000)

    logger.info("Avatar uploaded for user %s", user.id)
    return JSONResponse({"avatarUrl": "/api/profile/avatar"})


@router.get("/profile/avatar")
async def serve_avatar(user: User = Depends(get_current_user)) -> FileResponse:
    """Serve the current user's avatar image."""
    if not user.avatar_url:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No avatar")

    settings = get_settings()
    avatar_dir = settings.data_path / "users" / user.id / "avatar"
    if not avatar_dir.is_dir():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No avatar")

    # Find the latest file in the avatar directory
    files = sorted(avatar_dir.iterdir(), key=lambda f: f.name, reverse=True)
    if not files:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No avatar")

    filepath = files[0]
    ext = filepath.suffix.lower()
    media_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(ext, "application/octet-stream")

    return FileResponse(filepath, media_type=media_type)


async def _read_profile_prefs(user_id: str) -> dict[str, str]:
    """Read the 5 canonical profile keys from UserPreference."""
    async with get_db() as session:
        stmt = select(UserPreference).where(UserPreference.user_id == user_id)
        result = await session.execute(stmt)
        rows = result.scalars().all()
    return {r.key: r.value for r in rows}


def _cleanup_avatar_dir(avatar_dir) -> None:
    """Delete all existing files in the avatar directory."""
    if not avatar_dir.is_dir():
        return
    for f in avatar_dir.iterdir():
        try:
            f.unlink()
        except OSError as e:
            logger.warning("Failed to delete old avatar %s: %s", f, e)
