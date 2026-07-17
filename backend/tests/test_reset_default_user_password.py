"""Tests for the server-only default-user password reset command."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.auth.password import verify_password
from app.db.engine import get_db
from app.db.models import User


async def test_reset_updates_hash_and_invalidates_tokens(db, test_user, monkeypatch):
    monkeypatch.setenv("DEFAULT_USER_EMAIL", test_user["email"])
    monkeypatch.setenv("DEFAULT_USER_PASSWORD", "654321")
    from app.config import get_settings

    get_settings.cache_clear()
    from scripts.reset_default_user_password import reset_default_user_password

    await reset_default_user_password()

    async with get_db() as session:
        result = await session.execute(select(User).where(User.id == test_user["id"]))
        user = result.scalar_one()
        assert verify_password("654321", user.password_hash)
        assert user.token_version == 1


async def test_reset_rejects_empty_password(db, test_user, monkeypatch):
    monkeypatch.setenv("DEFAULT_USER_EMAIL", test_user["email"])
    monkeypatch.setenv("DEFAULT_USER_PASSWORD", "")
    from app.config import get_settings

    get_settings.cache_clear()
    from scripts.reset_default_user_password import reset_default_user_password

    with pytest.raises(ValueError, match="DEFAULT_USER_PASSWORD must not be empty"):
        await reset_default_user_password()

    async with get_db() as session:
        result = await session.execute(select(User).where(User.id == test_user["id"]))
        user = result.scalar_one()
        assert verify_password("testpass123", user.password_hash)
        assert user.token_version == 0
