"""Enable plan_approval_enabled on remote PostgreSQL."""
import asyncio
import asyncpg
import json

DB_URL = "postgresql://agenthub:agenthub@64.83.35.253:5432/agenthub"

async def main():
    conn = await asyncpg.connect(DB_URL)

    # Check current state
    row = await conn.fetchrow("SELECT id, settings FROM app_settings WHERE id = 'singleton'")
    if row:
        settings = row['settings'] or {}
        print(f"Current plan_approval_enabled = {settings.get('plan_approval_enabled', False)}")

        # Enable
        await conn.execute(
            """UPDATE app_settings SET settings = COALESCE(settings, '{}'::jsonb) || '{"plan_approval_enabled": true}'::jsonb WHERE id = 'singleton'"""
        )
        print(">>> Updated!")
    else:
        print("No singleton row, creating with defaults...")
        await conn.execute(
            """INSERT INTO app_settings (id, companion_mode, deployment_publish_enabled, settings, updated_at)
               VALUES ('singleton', 'off', false, '{"plan_approval_enabled": true}'::jsonb, 0)"""
        )
        print(">>> Created!")

    # Verify
    row = await conn.fetchrow("SELECT settings FROM app_settings WHERE id = 'singleton'")
    settings = row['settings'] or {}
    print(f"Verified: plan_approval_enabled = {settings.get('plan_approval_enabled', False)}")

    await conn.close()

asyncio.run(main())
