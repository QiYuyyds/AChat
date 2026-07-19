"""Tests for startup default-user create / stub-hash repair."""

from __future__ import annotations

from sqlalchemy import select

from app.auth.password import hash_password, verify_password
from app.auth.service import ensure_default_local_user
from app.db.engine import get_db
from app.db.models import User
from app.utils.clock import now_ms


async def test_creates_default_user_when_missing(db, monkeypatch):
    monkeypatch.setenv("DEFAULT_USER_EMAIL", "seed@local")
    monkeypatch.setenv("DEFAULT_USER_PASSWORD", "123456")
    from app.config import get_settings

    get_settings.cache_clear()

    async with get_db() as session:
        user = await ensure_default_local_user(session)
        assert user is not None
        assert user.email == "seed@local"
        assert verify_password("123456", user.password_hash)

    async with get_db() as session:
        result = await session.execute(select(User).where(User.email == "seed@local"))
        row = result.scalar_one()
        assert verify_password("123456", row.password_hash)


async def test_repairs_stub_password_hash(db, monkeypatch):
    monkeypatch.setenv("DEFAULT_USER_EMAIL", "stub@local")
    monkeypatch.setenv("DEFAULT_USER_PASSWORD", "123456")
    from app.config import get_settings

    get_settings.cache_clear()

    async with get_db() as session:
        import nanoid

        session.add(
            User(
                id=nanoid.generate(),
                email="stub@local",
                name="Stub",
                password_hash="!desktop-local-mirror!",
                token_version=0,
                created_at=now_ms(),
                updated_at=now_ms(),
            )
        )
        await session.flush()

    async with get_db() as session:
        user = await ensure_default_local_user(session)
        assert user is not None
        assert verify_password("123456", user.password_hash)
        assert user.token_version == 1


async def test_does_not_overwrite_real_bcrypt_hash(db, monkeypatch):
    monkeypatch.setenv("DEFAULT_USER_EMAIL", "real@local")
    monkeypatch.setenv("DEFAULT_USER_PASSWORD", "123456")
    from app.config import get_settings

    get_settings.cache_clear()

    original = hash_password("other-pass-999")
    async with get_db() as session:
        import nanoid

        session.add(
            User(
                id=nanoid.generate(),
                email="real@local",
                name="Real",
                password_hash=original,
                token_version=0,
                created_at=now_ms(),
                updated_at=now_ms(),
            )
        )
        await session.flush()

    async with get_db() as session:
        await ensure_default_local_user(session)

    async with get_db() as session:
        result = await session.execute(select(User).where(User.email == "real@local"))
        row = result.scalar_one()
        assert row.password_hash == original
        assert verify_password("other-pass-999", row.password_hash)
        assert row.token_version == 0


async def test_noop_when_password_empty(db, monkeypatch):
    monkeypatch.setenv("DEFAULT_USER_EMAIL", "nopass@local")
    monkeypatch.setenv("DEFAULT_USER_PASSWORD", "")
    from app.config import get_settings

    get_settings.cache_clear()

    async with get_db() as session:
        assert await ensure_default_local_user(session) is None
