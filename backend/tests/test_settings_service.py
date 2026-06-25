"""Tests for the UPSERT + mobile-token additions to settings_service.

Read-side behaviour (get_effective_api_key / base url) is exercised indirectly
elsewhere; here we focus on update_app_settings, regenerate_mobile_device_token,
the companion-runtime sync, and token generation.
"""

import json
import os

import pytest

from app.services import settings_service as svc


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path, monkeypatch):
    """Redirect companion.json writes and AGENTHUB_MOBILE_TOKEN into the tmp dir."""
    monkeypatch.setenv("AGENTHUB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("AGENTHUB_MOBILE_TOKEN", raising=False)
    yield


def test_new_mobile_device_token_is_urlsafe_and_unique():
    a = svc.new_mobile_device_token()
    b = svc.new_mobile_device_token()
    assert a != b
    # base64url(24 bytes) with padding stripped -> 32 chars, no '+' '/' '='
    assert len(a) == 32
    assert "=" not in a and "+" not in a and "/" not in a


async def test_get_app_settings_empty_defaults(db):
    settings = await svc.get_app_settings()
    assert settings.id == "singleton"
    assert settings.anthropic_api_key is None
    assert settings.companion_mode == "off"
    assert settings.deployment_publish_enabled is False
    assert settings.mobile_device_token is None


async def test_update_creates_row_and_trims(db):
    out = await svc.update_app_settings(
        {
            "anthropic_api_key": "  sk-abc  ",
            "openai_api_key": "",  # empty -> None
            "deployment_publish_enabled": True,
        }
    )
    assert out.anthropic_api_key == "sk-abc"  # trimmed
    assert out.openai_api_key is None  # empty collapses to None
    assert out.deployment_publish_enabled is True
    assert out.updated_at > 0

    # Persisted: a fresh read returns the same values.
    again = await svc.get_app_settings()
    assert again.anthropic_api_key == "sk-abc"
    assert again.deployment_publish_enabled is True


async def test_update_is_partial_absent_key_untouched(db):
    await svc.update_app_settings({"anthropic_api_key": "key1", "openai_api_key": "key2"})
    # Patch only one field; the other must survive.
    await svc.update_app_settings({"openai_api_key": "key2-new"})
    settings = await svc.get_app_settings()
    assert settings.anthropic_api_key == "key1"
    assert settings.openai_api_key == "key2-new"


async def test_update_null_clears_field(db):
    await svc.update_app_settings({"anthropic_api_key": "key1"})
    await svc.update_app_settings({"anthropic_api_key": None})
    settings = await svc.get_app_settings()
    assert settings.anthropic_api_key is None


async def test_keys_returned_verbatim_no_redaction(db):
    """TS GET returns key fields verbatim; the service must not redact."""
    await svc.update_app_settings({"anthropic_api_key": "sk-secret-1234567890"})
    settings = await svc.get_app_settings()
    assert settings.anthropic_api_key == "sk-secret-1234567890"


async def test_enabling_companion_auto_issues_token(db):
    out = await svc.update_app_settings({"companion_mode": "lan"})
    assert out.companion_mode == "lan"
    assert out.mobile_device_token  # auto-generated
    assert len(out.mobile_device_token) == 32


async def test_companion_off_does_not_issue_token(db):
    out = await svc.update_app_settings({"anthropic_api_key": "k"})
    assert out.companion_mode == "off"
    assert out.mobile_device_token is None


async def test_sync_companion_runtime_writes_file_and_env(db, tmp_path):
    await svc.update_app_settings({"companion_mode": "tailnet"})
    config_path = tmp_path / "data" / "companion.json"
    assert config_path.exists()
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data["companionMode"] == "tailnet"
    assert data["companionPort"] == svc.DEFAULT_COMPANION_PORT
    assert data["mobileDeviceToken"]
    # Env var set while companion is on.
    assert os.environ.get("AGENTHUB_MOBILE_TOKEN") == data["mobileDeviceToken"]


async def test_turning_companion_off_clears_env(db):
    await svc.update_app_settings({"companion_mode": "lan"})
    assert os.environ.get("AGENTHUB_MOBILE_TOKEN")
    await svc.update_app_settings({"companion_mode": "off"})
    assert os.environ.get("AGENTHUB_MOBILE_TOKEN") is None


async def test_regenerate_mobile_device_token_preserves_mode(db):
    first = await svc.update_app_settings({"companion_mode": "lan"})
    token1 = first.mobile_device_token
    regenerated = await svc.regenerate_mobile_device_token()
    assert regenerated.mobile_device_token != token1
    assert regenerated.companion_mode == "lan"  # mode preserved


async def test_regenerate_when_off_keeps_off_but_issues_token(db):
    out = await svc.regenerate_mobile_device_token()
    # mode stays off, but the explicitly-passed token is honored
    assert out.companion_mode == "off"
    assert out.mobile_device_token
    assert len(out.mobile_device_token) == 32


async def test_get_mobile_device_token(db):
    assert await svc.get_mobile_device_token() is None
    await svc.update_app_settings({"companion_mode": "lan"})
    token = await svc.get_mobile_device_token()
    assert token and len(token) == 32
