"""Auth business logic: register, authenticate, refresh, change password, logout-all."""

from __future__ import annotations

from typing import TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt_handler import (
    create_access_token,
    create_refresh_token,
    verify_token,
)
from app.auth.password import hash_password, verify_password
from app.config import get_settings
from app.db.models import User
from app.utils.clock import now_ms


class AuthTokens(TypedDict):
    """Token pair returned by login/register/refresh."""

    access_token: str
    refresh_token: str
    token_type: str


class UserProfile(TypedDict):
    """Public user profile returned by auth endpoints."""

    id: str
    email: str
    name: str
    avatarUrl: str | None


def _user_profile(user: User) -> UserProfile:
    return UserProfile(
        id=user.id,
        email=user.email,
        name=user.name,
        avatarUrl=user.avatar_url,
    )


def _tokens_for_user(user: User) -> AuthTokens:
    return AuthTokens(
        access_token=create_access_token(user.id, user.email, user.token_version),
        refresh_token=create_refresh_token(user.id, user.email, user.token_version),
        token_type="bearer",
    )


class AuthResult(TypedDict):
    user: UserProfile
    tokens: AuthTokens


async def register_user(
    db: AsyncSession, email: str, name: str, password: str
) -> AuthResult:
    """Create a new user account. Raises ValueError on duplicate email or disabled registration."""
    settings = get_settings()
    if not settings.allow_registration:
        raise ValueError("Registration is disabled")

    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none() is not None:
        raise ValueError("Email already registered")

    import nanoid

    user = User(
        id=nanoid.generate(),
        email=email,
        name=name,
        password_hash=hash_password(password),
        token_version=0,
        created_at=now_ms(),
        updated_at=now_ms(),
    )
    db.add(user)
    await db.flush()
    db.expunge(user)
    return AuthResult(user=_user_profile(user), tokens=_tokens_for_user(user))


async def authenticate_user(
    db: AsyncSession, email: str, password: str
) -> AuthResult:
    """Verify credentials and return tokens. Raises ValueError on failure."""
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        raise ValueError("Invalid credentials")

    return AuthResult(user=_user_profile(user), tokens=_tokens_for_user(user))


async def authenticate_default_user(
    db: AsyncSession, password: str
) -> AuthResult:
    """Authenticate the configured default account without exposing its email."""
    email = get_settings().default_user_email
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(password, user.password_hash):
        raise ValueError("Invalid credentials")

    return AuthResult(user=_user_profile(user), tokens=_tokens_for_user(user))


_STUB_PASSWORD_PREFIXES = ("!",)  # e.g. "!desktop-local-mirror!" — not a bcrypt hash


def _is_unusable_password_hash(password_hash: str | None) -> bool:
    """True when the stored hash can never verify a real password (stub / empty)."""
    if not password_hash:
        return True
    if password_hash.startswith(_STUB_PASSWORD_PREFIXES):
        return True
    # bcrypt hashes start with $2b$ / $2a$ / $2y$
    return not password_hash.startswith("$2")


async def ensure_default_local_user(db: AsyncSession) -> User | None:
    """Create or repair the configured default local account.

    Used at process startup so desktop SQLite / fresh DBs can log in with
    ``DEFAULT_USER_EMAIL`` + ``DEFAULT_USER_PASSWORD`` without a manual migration.

    - Missing user → create with bcrypt hash of ``DEFAULT_USER_PASSWORD``
    - Existing user with unusable/stub hash → repair hash (does not touch real bcrypt)
    - No-op when ``DEFAULT_USER_PASSWORD`` is empty
    """
    settings = get_settings()
    email = (settings.default_user_email or "").strip()
    password = settings.default_user_password or ""
    if not email or not password:
        return None

    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    now = now_ms()

    if user is None:
        import nanoid

        user = User(
            id=nanoid.generate(),
            email=email,
            name="Default User",
            password_hash=hash_password(password),
            token_version=0,
            created_at=now,
            updated_at=now,
        )
        db.add(user)
        await db.flush()
        db.expunge(user)
        return user

    if _is_unusable_password_hash(user.password_hash):
        user.password_hash = hash_password(password)
        user.token_version = int(user.token_version or 0) + 1
        user.updated_at = now
        await db.flush()
        db.expunge(user)
        return user

    return user


async def refresh_access_token(
    db: AsyncSession, refresh_token: str
) -> AuthTokens:
    """Issue a new access token from a valid refresh token. Raises ValueError on failure."""
    try:
        payload = verify_token(refresh_token, expected_type="refresh")
    except Exception as e:
        raise ValueError("Invalid refresh token") from e

    user_id = payload.get("sub")
    token_ver = payload.get("ver", 0)
    if not user_id:
        raise ValueError("Invalid refresh token")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or user.token_version != token_ver:
        raise ValueError("Invalid refresh token")

    # Issue both new access and refresh tokens (refresh rotation)
    return _tokens_for_user(user)


async def change_password(
    db: AsyncSession, user: User, current_password: str, new_password: str
) -> AuthTokens:
    """Change user password and invalidate all existing tokens. Raises ValueError on failure."""
    if not verify_password(current_password, user.password_hash):
        raise ValueError("Current password is incorrect")

    user.password_hash = hash_password(new_password)
    user.token_version += 1
    user.updated_at = now_ms()
    await db.flush()
    db.expunge(user)
    return _tokens_for_user(user)


async def logout_all_devices(db: AsyncSession, user: User) -> None:
    """Increment token_version to invalidate all existing tokens."""
    user.token_version += 1
    user.updated_at = now_ms()
    await db.flush()
