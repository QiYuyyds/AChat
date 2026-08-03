"""Tests for the multi-user migration script (backend/scripts/migrate_to_multi_user.py).

Verifies that the migration creates a default user and back-fills user_id on
existing rows.
"""

from __future__ import annotations


async def test_migration_creates_default_user(db, monkeypatch):
    """Migration creates a default user with the configured email."""
    monkeypatch.setenv("DEFAULT_USER_EMAIL", "admin@migration.test")
    monkeypatch.setenv("DEFAULT_USER_PASSWORD", "adminpass123")

    from app.config import get_settings
    get_settings.cache_clear()

    from app.db.engine import get_db
    from app.db.models import Agent, User
    from app.utils.clock import now_ms

    # Seed an agent without user_id (simulating pre-migration data)
    now = now_ms()
    async with get_db() as session:
        agent = Agent(
            id="ag_legacy",
            name="Legacy Agent",
            avatar="L",
            description="pre-migration agent",
            system_prompt="prompt",
            adapter_name="mock",
            is_builtin=False,
            is_orchestrator=False,
            created_at=now,
            user_id=None,
        )
        agent.capabilities_list = []
        agent.tool_names_list = []
        session.add(agent)

    # Run the migration
    from scripts.migrate_to_multi_user import migrate
    await migrate()

    # Verify default user was created
    async with get_db() as session:
        from sqlalchemy import select

        result = await session.execute(
            select(User).where(User.email == "admin@migration.test")
        )
        user = result.scalar_one_or_none()
        assert user is not None
        assert user.email == "admin@migration.test"

        # Verify the legacy agent was back-filled
        result = await session.execute(
            select(Agent).where(Agent.id == "ag_legacy")
        )
        agent = result.scalar_one()
        assert agent.user_id == user.id


async def test_migration_idempotent(db, monkeypatch):
    """Running migration twice doesn't create duplicate users."""
    monkeypatch.setenv("DEFAULT_USER_EMAIL", "admin2@migration.test")
    monkeypatch.setenv("DEFAULT_USER_PASSWORD", "adminpass123")

    from app.config import get_settings
    get_settings.cache_clear()

    from scripts.migrate_to_multi_user import migrate

    await migrate()
    await migrate()

    from sqlalchemy import select

    from app.db.engine import get_db
    from app.db.models import User

    async with get_db() as session:
        result = await session.execute(
            select(User).where(User.email == "admin2@migration.test")
        )
        users = result.scalars().all()
        assert len(users) == 1
