"""FastAPI authentication dependencies."""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.desktop import get_or_seed_local_user, is_desktop_mode
from app.auth.jwt_handler import verify_token
from app.config import get_settings
from app.db.engine import get_db_session
from app.db.models import User

logger = logging.getLogger(__name__)

COOKIE_NAME = "agenthub_token"

# In-process user cache: user_id -> (User, expires_at)
_user_cache: dict[str, tuple[User, float]] = {}
_USER_CACHE_TTL = 60  # seconds

# Per-user locks to prevent thundering-herd PG queries on concurrent cache miss
_user_locks: dict[str, asyncio.Lock] = {}


def _get_user_lock(user_id: str) -> asyncio.Lock:
    """Get or create an asyncio.Lock for the given user_id."""
    lock = _user_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _user_locks[user_id] = lock
    return lock


def _invalidate_user_cache(user_id: str) -> None:
    """Manually evict a user from the in-process cache.

    Called by change-password / logout-all endpoints to immediately
    invalidate stale tokens rather than waiting for TTL expiry.
    """
    _user_cache.pop(user_id, None)


def _extract_token(request: Request) -> str | None:
    """Read JWT from HttpOnly cookie, Authorization header, or ?token= query param.

    Priority: cookie → Authorization header → query param. The query param
    fallback exists for cross-origin sandbox iframe previews and SSE
    EventSource connections that cannot set headers or send cookies.
    """
    # 1. Cookie (same-origin / production)
    token = request.cookies.get(COOKIE_NAME)
    if token:
        return token

    # 2. Authorization: Bearer <token>
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]

    # 3. Query param ?token= (cross-origin sandbox iframe / EventSource)
    token = request.query_params.get("token")
    if token:
        return token

    return None


async def _resolve_user(db: AsyncSession, user_id: str, token_ver: int) -> User:
    """Resolve a user from cache or PG, with per-user lock for herd safety.

    Raises HTTPException(401) if the user is not found or token_version mismatches.
    """
    credentials_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": 'Bearer realm="agenthub"'},
    )

    # Fast path: check cache without lock
    cached = _user_cache.get(user_id)
    if cached is not None:
        user, expires_at = cached
        if time.time() < expires_at:
            if user.token_version != token_ver:
                _user_cache.pop(user_id, None)
                raise credentials_exc
            logger.debug("user_cache hit user_id=%s", user_id)
            return user

    # Slow path: acquire per-user lock to prevent thundering herd
    lock = _get_user_lock(user_id)
    async with lock:
        # Re-check cache after acquiring lock (another request may have filled it)
        cached = _user_cache.get(user_id)
        if cached is not None:
            user, expires_at = cached
            if time.time() < expires_at:
                if user.token_version != token_ver:
                    _user_cache.pop(user_id, None)
                    raise credentials_exc
                logger.debug("user_cache hit user_id=%s", user_id)
                return user

        logger.debug("user_cache miss user_id=%s", user_id)
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

        if user is None:
            raise credentials_exc

        if user.token_version != token_ver:
            raise credentials_exc

        # Detach from session before caching so the subsequent
        # session.commit() doesn't expire attributes (DetachedInstanceError).
        db.expunge(user)
        _user_cache[user_id] = (user, time.time() + _USER_CACHE_TTL)
        return user


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> User:
    """Resolve the authenticated User from the request, or raise 401.

    桌面模式例外（AGENTHUB_DESKTOP=1）：无条件解析为固定本地用户，不做
    逐请求 JWT 验证（platform-security delta；本地服务器仅绑定 loopback）。
    """
    if is_desktop_mode():
        return await get_or_seed_local_user(db)

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

    return await _resolve_user(db, user_id, token_ver)


async def get_current_user_optional(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> User | None:
    """Like get_current_user but returns None instead of raising 401.

    Used by endpoints that need to optionally identify the user (e.g. SSE
    in dev mode where the token may come from a query param).
    """
    if is_desktop_mode():
        return await get_or_seed_local_user(db)

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

    try:
        return await _resolve_user(db, user_id, token_ver)
    except HTTPException:
        return None


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
