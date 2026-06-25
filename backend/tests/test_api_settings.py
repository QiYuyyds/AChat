"""API tests for the settings router (app/api/settings.py).

Covers GET /api/settings, PATCH /api/settings, and POST /api/settings/mobile-token,
including happy paths and an error path each. Uses the api_client fixture (shares
the isolated test DB) from conftest.
"""

from __future__ import annotations

_FULL_KEYS = {
    "id",
    "anthropicApiKey",
    "anthropicBaseUrl",
    "openaiApiKey",
    "deepseekApiKey",
    "arkApiKey",
    "companionMode",
    "mobileDeviceToken",
    "deploymentPublishEnabled",
    "deploymentPublishDir",
    "deploymentPublicBaseUrl",
    "updatedAt",
}


async def test_get_settings_defaults(api_client, db):
    resp = await api_client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"settings"}
    s = body["settings"]
    # Full AppSettingsRow shape (byte-for-byte with the frontend type).
    assert set(s.keys()) == _FULL_KEYS
    assert s["id"] == "singleton"
    assert s["companionMode"] == "off"
    assert s["anthropicApiKey"] is None
    assert s["deploymentPublishEnabled"] is False


async def test_patch_upserts_and_returns_row(api_client, db):
    resp = await api_client.patch(
        "/api/settings",
        json={"anthropicApiKey": "sk-ant-123", "openaiApiKey": "sk-oai-456"},
    )
    assert resp.status_code == 200
    s = resp.json()["settings"]
    # Keys returned verbatim, no redaction (mirrors TS source).
    assert s["anthropicApiKey"] == "sk-ant-123"
    assert s["openaiApiKey"] == "sk-oai-456"
    assert s["id"] == "singleton"
    assert s["updatedAt"] > 0

    # Persisted: a follow-up GET reflects the patch.
    got = (await api_client.get("/api/settings")).json()["settings"]
    assert got["anthropicApiKey"] == "sk-ant-123"
    assert got["openaiApiKey"] == "sk-oai-456"


async def test_patch_absent_field_untouched_null_clears(api_client, db):
    await api_client.patch("/api/settings", json={"anthropicApiKey": "keep-me"})

    # openaiApiKey absent → anthropic key stays; openai still None.
    resp = await api_client.patch("/api/settings", json={"openaiApiKey": "added"})
    s = resp.json()["settings"]
    assert s["anthropicApiKey"] == "keep-me"
    assert s["openaiApiKey"] == "added"

    # Explicit null clears the field.
    resp = await api_client.patch("/api/settings", json={"anthropicApiKey": None})
    s = resp.json()["settings"]
    assert s["anthropicApiKey"] is None
    assert s["openaiApiKey"] == "added"


async def test_patch_companion_mode_issues_token(api_client, db):
    resp = await api_client.patch("/api/settings", json={"companionMode": "lan"})
    assert resp.status_code == 200
    s = resp.json()["settings"]
    assert s["companionMode"] == "lan"
    # Switching off-->non-off with no token auto-issues one.
    assert isinstance(s["mobileDeviceToken"], str) and s["mobileDeviceToken"]


async def test_patch_invalid_companion_mode_returns_400(api_client, db):
    resp = await api_client.patch("/api/settings", json={"companionMode": "bogus"})
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "Invalid body"
    assert "issues" in body


async def test_patch_non_object_body_returns_400(api_client, db):
    resp = await api_client.patch(
        "/api/settings",
        content=b"[]",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "Invalid body"


async def test_mobile_token_regenerates(api_client, db):
    first = (await api_client.post("/api/settings/mobile-token")).json()["settings"]
    token1 = first["mobileDeviceToken"]
    assert isinstance(token1, str) and token1

    second = (await api_client.post("/api/settings/mobile-token")).json()["settings"]
    token2 = second["mobileDeviceToken"]
    assert isinstance(token2, str) and token2
    assert token1 != token2


async def test_mobile_token_response_shape(api_client, db):
    resp = await api_client.post("/api/settings/mobile-token")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"settings"}
    assert set(body["settings"].keys()) == _FULL_KEYS
