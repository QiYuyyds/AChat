"""Desktop settings key resolution via cloud client."""

from __future__ import annotations

import asyncio

from app.desktop.cloud_client import clear_cloud_session, set_cloud_access_token
from app.desktop.runtime import DesktopRuntime, set_desktop_runtime
from app.services.settings_service import get_user_settings


class _FakeClient:
    async def fetch_user_settings(self):
        return {
            "settings": {
                "openaiApiKey": "sk-test-from-cloud",
                "anthropicApiKey": "sk-ant-cloud",
            }
        }


def test_desktop_get_user_settings_from_cloud(tmp_path, monkeypatch):
    import app.desktop.runtime as runtime_mod
    import app.services.settings_service as ss

    rt = DesktopRuntime(
        data_dir=tmp_path,
        engine_token="t",
        official_api_url="https://api.example",
        allowed_origins=["https://app.example"],
    )
    set_desktop_runtime(rt)
    set_cloud_access_token("user-jwt", user_id="u1")

    monkeypatch.setattr(ss, "get_cloud_client", lambda: _FakeClient(), raising=False)
    # Patch the import site used inside _get_user_settings_from_cloud
    import app.desktop.cloud_client as cloud

    monkeypatch.setattr(cloud, "get_cloud_client", lambda: _FakeClient())

    row = asyncio.run(get_user_settings("u1"))
    assert row.openai_api_key == "sk-test-from-cloud"
    assert row.anthropic_api_key == "sk-ant-cloud"

    clear_cloud_session()
    runtime_mod._RUNTIME = None
