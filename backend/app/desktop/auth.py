"""Desktop engine user resolution.

v1 default: local JWT (same as web, engine signs against configured JWT_SECRET /
primary PG users). Optional legacy: cloud HTTPS /api/auth/me when cloud_api_client
feature flag is on.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from app.db.engine import get_db
from app.db.models import User
from app.utils.clock import now_ms

logger = logging.getLogger(__name__)

_DESKTOP_STUB_HASH = "!desktop-local-mirror!"


async def resolve_desktop_user(
    access_token: str | None = None,
    *,
    user_id_hint: str | None = None,
) -> User | None:
    """Prefer local JWT; fall back to legacy cloud profile when feature-enabled."""
    from app.desktop.runtime import cloud_api_client_enabled

    # Local JWT is the v1 primary path — handled by get_current_user after this
    # returns None. Only attempt cloud resolve when the legacy flag is on.
    if not cloud_api_client_enabled():
        return None

    from app.desktop.cloud_client import get_cloud_session, set_cloud_access_token

    session = get_cloud_session()
    token = access_token or session.access_token
    if not token:
        return None

    if access_token and access_token != session.access_token:
        set_cloud_access_token(access_token, user_id=user_id_hint or session.user_id)

    profile = await _fetch_cloud_profile(token)
    if profile is None:
        return None

    uid = str(profile.get("id") or "")
    if not uid:
        return None

    set_cloud_access_token(token, user_id=uid)
    return await ensure_local_user(
        user_id=uid,
        email=str(profile.get("email") or f"{uid}@desktop.local"),
        name=str(profile.get("name") or "Desktop User"),
        avatar_url=profile.get("avatarUrl") or profile.get("avatar_url"),
    )


async def _fetch_cloud_profile(token: str) -> dict[str, Any] | None:
    from app.desktop.cloud_client import CloudApiClient, get_cloud_session, set_cloud_access_token

    prev = get_cloud_session().access_token
    prev_uid = get_cloud_session().user_id
    set_cloud_access_token(token, user_id=prev_uid)
    try:
        data = await CloudApiClient().get_json("/api/auth/me")
    except Exception as e:
        logger.warning("desktop cloud /api/auth/me failed: %s", e)
        set_cloud_access_token(prev, user_id=prev_uid)
        return None
    user = data.get("user") if isinstance(data, dict) else None
    return user if isinstance(user, dict) else None


async def ensure_local_user(
    *,
    user_id: str,
    email: str,
    name: str,
    avatar_url: str | None = None,
) -> User:
    """UPSERT a shadow User row for legacy cloud identity mapping."""
    async with get_db() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        row = result.scalar_one_or_none()
        now = now_ms()
        if row is None:
            row = User(
                id=user_id,
                email=email,
                name=name,
                password_hash=_DESKTOP_STUB_HASH,
                avatar_url=avatar_url,
                token_version=0,
                created_at=now,
                updated_at=now,
            )
            db.add(row)
        else:
            row.email = email
            row.name = name
            if avatar_url is not None:
                row.avatar_url = avatar_url
            row.updated_at = now
        await db.flush()
        db.expunge(row)
        return row
