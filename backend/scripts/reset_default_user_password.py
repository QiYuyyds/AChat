"""Reset the configured default user's password from server environment values.

Run from the backend directory:

    python -m scripts.reset_default_user_password
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.auth.password import hash_password
from app.config import get_settings
from app.db.engine import close_db, get_db, init_db
from app.db.models import User
from app.utils.clock import now_ms


async def reset_default_user_password() -> None:
    """Replace the default user's password hash and invalidate existing tokens."""
    settings = get_settings()
    if not settings.default_user_password:
        raise ValueError("DEFAULT_USER_PASSWORD must not be empty")

    async with get_db() as db:
        result = await db.execute(
            select(User).where(User.email == settings.default_user_email)
        )
        user = result.scalar_one_or_none()
        if user is None:
            raise ValueError("Default user not found")

        user.password_hash = hash_password(settings.default_user_password)
        user.token_version += 1
        user.updated_at = now_ms()


async def main() -> None:
    """Initialize database services and run the password reset."""
    settings = get_settings()
    await init_db()
    try:
        await reset_default_user_password()
        print(f"Default user password reset: {settings.default_user_email}")
    finally:
        await close_db()


if __name__ == "__main__":
    asyncio.run(main())
