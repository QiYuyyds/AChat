"""Desktop auth path without remote business API (local JWT preference)."""

from __future__ import annotations

import os

import pytest

from app.desktop.runtime import DesktopRuntime, cloud_api_client_enabled, set_desktop_runtime


@pytest.fixture()
def desktop_direct(tmp_path, monkeypatch):
    monkeypatch.setenv("ACHAT_RUNTIME", "desktop")
    monkeypatch.setenv("ACHAT_FEATURE_CLOUD_API_CLIENT", "0")
    monkeypatch.setenv("ACHAT_FEATURE_DIRECT_INFRA", "1")
    rt = DesktopRuntime(
        data_dir=tmp_path,
        engine_token="t",
        feature_direct_infra=True,
        feature_cloud_api_client=False,
    )
    set_desktop_runtime(rt)
    yield rt
    import app.desktop.runtime as m

    m._RUNTIME = None
    for k in (
        "ACHAT_RUNTIME",
        "ACHAT_FEATURE_CLOUD_API_CLIENT",
        "ACHAT_FEATURE_DIRECT_INFRA",
        "ACHAT_ENGINE_TOKEN",
        "ACHAT_DATA_DIR",
    ):
        os.environ.pop(k, None)


def test_cloud_api_client_disabled_by_default(desktop_direct):
    assert cloud_api_client_enabled() is False


@pytest.mark.asyncio
async def test_resolve_desktop_user_noop_without_cloud_flag(desktop_direct):
    from app.desktop.auth import resolve_desktop_user

    user = await resolve_desktop_user("any-token")
    assert user is None
