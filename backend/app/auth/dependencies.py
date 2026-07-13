"""FastAPI authentication dependencies."""

from __future__ import annotations

import logging

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt_handler import verify_token
from app.config import get_settings
from app.db.engine import get_db_session
from app.db.models import User

logger = logging.getLogger(__name__)

COOKIE_NAME = "agenthub_token"


def _extract_token(request: Request) -> str | None:
    """Read JWT from HttpOnly cookie (production) or Authorization header (dev).

    The Authorization header fallback exists so that cross-origin dev setups
    (frontend :3000 → backend :8000) and API testing tools can authenticate
    without cookies. SSE connections use the ``?token=`` query param which is
    handled separately in the stream endpoint.
    """
    # 1. Cookie (same-origin / production)
    token = request.cookies.get(COOKIE_NAME)
    if token:
        return token

    # 2. Authorization: Bearer <token>
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]

    return None


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> User:
    """Resolve the authenticated User from the request, or raise 401."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": 'Bearer realm="agenthub"'},
    )

    token = _extract_token(request)
    if not token:
        raise credentials_exception

    try:
        payload = verify_token(token, expected_type="access")
    except Exception:
        raise credentials_exception from None

    user_id = payload.get("sub")
    token_ver = payload.get("ver", 0)

    if not user_id:
        raise credentials_exception

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise credentials_exception

    # token_version mismatch → password changed or logout-all
    if user.token_version != token_ver:
        raise credentials_exception

    return user


async def get_current_user_optional(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> User | None:
    """Like get_current_user but returns None instead of raising 401.

    Used by endpoints that need to optionally identify the user (e.g. SSE
    in dev mode where the token may come from a query param).
    """
    token = _extract_token(request)
    if not token:
        return None
    try:
        payload = verify_token(token, expected_type="access")
    except Exception:
        return None

    user_id = payload.get("sub")
    token_ver = payload.get("ver", 0)
    if not user_id:
        return None

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or user.token_version != token_ver:
        return None
    return user


def set_auth_cookie(response, token: str) -> None:
    """Set the JWT as an HttpOnly cookie on the response."""
    settings = get_settings()
    # Determine if we're in a cross-origin dev setup
    origins = settings.cors_origins_list
    is_cross_origin = len(origins) > 0 and any(
        ":3000" in o or ":8000" not in o for o in origins
    )

    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=not settings.debug,  # HTTPS-only in production
        samesite="none" if (is_cross_origin and not settings.debug) else "lax",
        path="/",
        max_age=settings.jwt_access_token_expiry,
    )


def clear_auth_cookie(response) -> None:
    """Remove the JWT cookie."""
    response.delete_cookie(key=COOKIE_NAME, path="/")
