import asyncio
from app.db.engine import get_local_db
from app.db.models import AppSettings, ModelProfile
from sqlalchemy import select

async def check():
    async with get_local_db() as db:
        app = (await db.execute(
            select(AppSettings).where(AppSettings.id == "singleton")
        )).scalar_one_or_none()
        print("AppSettings:", app is not None)
        if app:
            print("  deepseek_key:", bool(app.deepseek_api_key))
            print("  openai_key:", bool(app.openai_api_key))

        mps = (await db.execute(
            select(ModelProfile).where(ModelProfile.is_default == True).limit(1)  # noqa: E712
        )).scalar_one_or_none()
        print("DefaultModelProfile:", mps is not None)
        if mps:
            print("  api_key:", bool(mps.api_key))
            print("  provider:", mps.provider)
            print("  model:", mps.model_id)
            print("  base_url:", mps.api_base_url)

asyncio.run(check())
