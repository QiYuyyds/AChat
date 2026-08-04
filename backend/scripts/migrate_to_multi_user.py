"""Migrate single-user database to multi-user schema.

Creates a default user, back-fills ``user_id`` on all existing rows, copies
deployment config to ``global_settings``, and migrates ``app_settings`` →
``user_settings``.

Usage::

    cd backend
    python -m scripts.migrate_to_multi_user

Environment variables:
    DEFAULT_USER_EMAIL    — email for the default user (default: admin@local)
    DEFAULT_USER_PASSWORD — password for the default user (auto-generated if empty)
    DATABASE_URL          — PostgreSQL connection string
"""

from __future__ import annotations

import asyncio
import os
import secrets
import sys

# Ensure backend/ is on sys.path when run as a script
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import select, text

from app.auth.password import hash_password
from app.config import get_settings
from app.db.engine import get_db, init_db
from app.db.models import (
    AppSettings,
    Document,
    GlobalSettings,
    User,
    UserSettings,
)
from app.utils.clock import now_ms


async def migrate() -> None:
    settings = get_settings()
    await init_db()

    email = settings.default_user_email or "admin@local"
    password = settings.default_user_password or secrets.token_urlsafe(16)

    async with get_db() as db:
        # 1. Create default user (idempotent)
        existing = await db.execute(select(User).where(User.email == email))
        user = existing.scalar_one_or_none()
        if user is None:
            import nanoid

            user = User(
                id=nanoid.generate(),
                email=email,
                name="Default User",
                password_hash=hash_password(password),
                token_version=0,
                created_at=now_ms(),
                updated_at=now_ms(),
            )
            db.add(user)
            await db.flush()
            print(f"[migration] Created default user: {email}")
            print(f"[migration] Password: {password}")
        else:
            print(f"[migration] Default user already exists: {email}")

        default_user_id = user.id

        # 2. Back-fill user_id on remote ownership tables only
        # (local tables no longer have user_id columns)
        for _model_cls, label in [
            (Document, "documents"),
        ]:
            result = await db.execute(
                text(
                    f"UPDATE {label} SET user_id = :uid "
                    f"WHERE user_id IS NULL"
                ),
                {"uid": default_user_id},
            )
            if result.rowcount:
                print(f"[migration] Back-filled {result.rowcount} row(s) in {label}")

        # 3. Back-fill memory + RAG tables
        for table_name in ("long_term_memory", "memory_nodes", "chat_history", "rag_chunks"):
            result = await db.execute(
                text(
                    f"UPDATE {table_name} SET user_id = :uid "
                    f"WHERE user_id IS NULL"
                ),
                {"uid": default_user_id},
            )
            if result.rowcount:
                print(f"[migration] Back-filled {result.rowcount} row(s) in {table_name}")

        # 4. Back-fill user_preferences (existing table with string user_id)
        result = await db.execute(
            text(
                "UPDATE user_preferences SET user_id = :uid "
                "WHERE user_id = 'default_user' OR user_id NOT IN (SELECT id FROM users)"
            ),
            {"uid": default_user_id},
        )
        if result.rowcount:
            print(f"[migration] Back-filled {result.rowcount} row(s) in user_preferences")

        # 5. Migrate app_settings → user_settings
        old_settings = await db.execute(
            select(AppSettings).where(AppSettings.id == "singleton")
        )
        old_row = old_settings.scalar_one_or_none()

        if old_row is not None:
            # 5a. Copy deployment config to global_settings
            gs_result = await db.execute(
                select(GlobalSettings).where(GlobalSettings.id == "singleton")
            )
            gs = gs_result.scalar_one_or_none()
            if gs is None:
                gs = GlobalSettings(
                    id="singleton",
                    deployment_publish_enabled=old_row.deployment_publish_enabled or False,
                    deployment_publish_dir=old_row.deployment_publish_dir,
                    deployment_public_base_url=old_row.deployment_public_base_url,
                    updated_at=now_ms(),
                )
                db.add(gs)
                print("[migration] Created global_settings from app_settings")

            # 5b. Create user_settings row for default user
            us_result = await db.execute(
                select(UserSettings).where(UserSettings.user_id == default_user_id)
            )
            us = us_result.scalar_one_or_none()
            if us is None:
                us = UserSettings(
                    user_id=default_user_id,
                    anthropic_api_key=old_row.anthropic_api_key,
                    anthropic_base_url=old_row.anthropic_base_url,
                    openai_api_key=old_row.openai_api_key,
                    deepseek_api_key=old_row.deepseek_api_key,
                    ark_api_key=old_row.ark_api_key,
                    companion_mode=old_row.companion_mode or "off",
                    mobile_device_token=old_row.mobile_device_token,
                    settings=old_row.settings,
                    updated_at=now_ms(),
                )
                db.add(us)
                print("[migration] Created user_settings from app_settings")

        # 6. Set NOT NULL constraints on remote ownership columns (PostgreSQL)
        for table_name in ("documents",):
            try:
                await db.execute(
                    text(f"ALTER TABLE {table_name} ALTER COLUMN user_id SET NOT NULL")
                )
                print(f"[migration] Set user_id NOT NULL on {table_name}")
            except Exception as e:
                print(f"[migration] Skipped NOT NULL on {table_name}: {e}")

        print("\n[migration] Done!")
        print(f"  Login email:    {email}")
        if not settings.default_user_password:
            print(f"  Login password: {password}")
            print("  (save this password — it will not be shown again)")


if __name__ == "__main__":
    asyncio.run(migrate())
