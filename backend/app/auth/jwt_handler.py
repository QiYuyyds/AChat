"""JWT token creation and verification using PyJWT."""

from __future__ import annotations

import time
from typing import Any, Literal

import jwt

from app.config import get_settings

_TOKEN_VERSION = 1  # bump if payload structure changes

TokenPayload = dict[str, Any]


def _secret() -> str:
    return get_settings().jwt_secret


def create_access_token(user_id: str, email: str, token_version: int) -> str:
    """Create a short-lived access JWT for the given user."""
    settings = get_settings()
    now = int(time.time())
    payload: TokenPayload = {
        "sub": user_id,
        "email": email,
        "type": "access",
        "iat": now,
        "exp": now + settings.jwt_access_token_expiry,
        "ver": token_version,
    }
    return jwt.encode(payload, _secret(), algorithm="HS256")


def create_refresh_token(user_id: str, email: str, token_version: int) -> str:
    """Create a long-lived refresh JWT for the given user."""
    settings = get_settings()
    now = int(time.time())
    payload: TokenPayload = {
        "sub": user_id,
        "email": email,
        "type": "refresh",
        "iat": now,
        "exp": now + settings.jwt_refresh_token_expiry,
        "ver": token_version,
    }
    return jwt.encode(payload, _secret(), algorithm="HS256")


def verify_token(
    token: str, expected_type: Literal["access", "refresh"] | None = None
) -> TokenPayload:
    """Decode and verify a JWT. Raises jwt.PyJWTError on failure.

    If ``expected_type`` is given, the token's ``type`` claim must match.
    """
    payload = jwt.decode(token, _secret(), algorithms=["HS256"])
    if expected_type is not None and payload.get("type") != expected_type:
        raise jwt.InvalidTokenError(
            f"Expected token type '{expected_type}', got '{payload.get('type')}'"
        )
    return payload
