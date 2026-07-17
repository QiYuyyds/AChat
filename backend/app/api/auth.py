"""Auth API router — register, login, me, refresh, logout, change-password, logout-all."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import select

from app.auth.dependencies import (
    clear_auth_cookie,
    get_current_user,
    set_auth_cookie,
)
from app.auth.service import (
    AuthResult,
    authenticate_default_user,
    authenticate_user,
    change_password,
    logout_all_devices,
    refresh_access_token,
    register_user,
)
from app.config import get_settings
from app.db.engine import get_db
from app.db.models import User
from app.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    VipLoginRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _auth_response(result: AuthResult) -> JSONResponse:
    """Build a JSON response with the auth cookie set."""
    response = JSONResponse(
        content={
            "user": result["user"],
            "tokens": result["tokens"],
            "config": _config_response(),
        }
    )
    set_auth_cookie(response, result["tokens"]["access_token"])
    return response


def _config_response() -> dict:
    """Public auth config for the frontend (e.g. allow_registration)."""
    settings = get_settings()
    return {
        "allowRegistration": settings.allow_registration,
        "vipLoginEnabled": settings.vip_login_enabled,
    }


# ─── POST /api/auth/register ───────────────────────────
@router.post("/auth/register")
async def register(request: Request) -> JSONResponse:
    """Register a new user account."""
    try:
        body = await request.json()
        req = RegisterRequest(**body)
    except (ValidationError, Exception) as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid request body: {e}",
        ) from e

    async with get_db() as db:
        try:
            result = await register_user(db, req.email, req.name, req.password)
        except ValueError as e:
            msg = str(e)
            if "disabled" in msg:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, detail=msg
                ) from e
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=msg
            ) from e

    return _auth_response(result)


# ─── POST /api/auth/login ──────────────────────────────
@router.post("/auth/login")
async def login(request: Request) -> JSONResponse:
    """Log in with email and password."""
    try:
        body = await request.json()
        req = LoginRequest(**body)
    except (ValidationError, Exception) as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid request body: {e}",
        ) from e

    async with get_db() as db:
        try:
            result = await authenticate_user(db, req.email, req.password)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
            ) from e

    return _auth_response(result)


# ─── POST /api/auth/vip-login ──────────────────────────
@router.post("/auth/vip-login")
async def vip_login(request: Request) -> JSONResponse:
    """Log in to the configured default account using only its password."""
    settings = get_settings()
    if not settings.vip_login_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    try:
        body = await request.json()
        req = VipLoginRequest(**body)
    except (ValidationError, Exception) as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid request body: {e}",
        ) from e

    async with get_db() as db:
        try:
            result = await authenticate_default_user(db, req.password)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
            ) from e

    return _auth_response(result)


# ─── GET /api/auth/me ──────────────────────────────────
@router.get("/auth/me")
async def me(user: User = Depends(get_current_user)) -> JSONResponse:
    """Return the current authenticated user's profile and auth config."""
    from app.auth.service import _user_profile

    return JSONResponse(
        content={"user": _user_profile(user), "config": _config_response()}
    )


# ─── GET /api/auth/config ──────────────────────────────
@router.get("/auth/config")
async def auth_config() -> JSONResponse:
    """Public auth config (no authentication required)."""
    return JSONResponse(content=_config_response())


# ─── POST /api/auth/refresh ────────────────────────────
@router.post("/auth/refresh")
async def refresh(request: Request) -> JSONResponse:
    """Issue a new access token from a valid refresh token."""
    try:
        body = await request.json()
        req = RefreshRequest(**body)
    except (ValidationError, Exception) as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid request body: {e}",
        ) from e

    async with get_db() as db:
        try:
            tokens = await refresh_access_token(db, req.refresh_token)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(e),
            ) from e

        # Fetch the user profile for the refreshed token
        from app.auth.jwt_handler import verify_token

        payload = verify_token(tokens["access_token"], expected_type="access")
        result = await db.execute(select(User).where(User.id == payload["sub"]))
        db_user = result.scalar_one()
        from app.auth.service import _user_profile

        response = JSONResponse(
            content={
                "user": _user_profile(db_user),
                "tokens": tokens,
                "config": _config_response(),
            }
        )
        set_auth_cookie(response, tokens["access_token"])
        return response


# ─── POST /api/auth/logout ─────────────────────────────
@router.post("/auth/logout")
async def logout() -> JSONResponse:
    """Clear the auth cookie (client-side logout)."""
    response = JSONResponse(content={"ok": True})
    clear_auth_cookie(response)
    return response


# ─── POST /api/auth/change-password ────────────────────
@router.post("/auth/change-password")
async def change_password_endpoint(
    request: Request,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Change the current user's password and invalidate existing tokens."""
    try:
        body = await request.json()
        req = ChangePasswordRequest(**body)
    except (ValidationError, Exception) as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid request body: {e}",
        ) from e

    async with get_db() as db:
        # Re-attach the user to this session
        from sqlalchemy import select

        result = await db.execute(select(User).where(User.id == user.id))
        db_user = result.scalar_one()
        try:
            tokens = await change_password(db, db_user, req.current_password, req.new_password)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            ) from e

    response = JSONResponse(content={"tokens": tokens})
    set_auth_cookie(response, tokens["access_token"])
    return response


# ─── POST /api/auth/logout-all ─────────────────────────
@router.post("/auth/logout-all")
async def logout_all(
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Increment token_version to invalidate all existing tokens."""
    async with get_db() as db:
        from sqlalchemy import select

        result = await db.execute(select(User).where(User.id == user.id))
        db_user = result.scalar_one()
        await logout_all_devices(db, db_user)

    response = JSONResponse(content={"ok": True})
    clear_auth_cookie(response)
    return response
