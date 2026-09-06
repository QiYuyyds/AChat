"""Tests for the multi-user migration script (backend/scripts/migrate_to_multi_user.py).

Verifies that the migration creates a default user and back-fills user_id on
remote ownership tables.

C 类 integration：migrate() 会无条件对远端表（long_term_memory / memory_nodes 等，
只存在于真实远程 PostgreSQL，app/db/models.py 未定义对应模型，单库 SQLite 测试环境
按设计不建这些表，见 test_dual_db 对 remote tables 缺席的断言）执行回填 SQL，
因此这两个用例需要真实双库基础设施，默认环境自动跳过（见 tests/conftest.py 的
integration 自动跳过规则）。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


async def test_migration_creates_default_user(db, monkeypatch):
    """Migration creates a default user with the configured email."""
    monkeypatch.setenv("DEFAULT_USER_EMAIL", "admin@migration.test")
    monkeypatch.setenv("DEFAULT_USER_PASSWORD", "adminpass123")

    from app.config import get_settings
    get_settings.cache_clear()

    # Run the migration
    from scripts.migrate_to_multi_user import migrate
    await migrate()

    # Verify default user was created
    from sqlalchemy import select

    from app.db.engine import get_db
    from app.db.models import User

    async with get_db() as session:
        result = await session.execute(
            select(User).where(User.email == "admin@migration.test")
        )
        user = result.scalar_one_or_none()
        assert user is not None
        assert user.email == "admin@migration.test"


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
