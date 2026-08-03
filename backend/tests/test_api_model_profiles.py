"""ModelProfile CRUD + connectivity test API tests.

Covers:
  12.1 — ModelProfile CRUD (create / set default uniqueness / delete default auto-transfer)
  12.2 — Connectivity test ok / fail / rate-limit
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _create_body(
    name: str = "DeepSeek Chat",
    provider: str = "deepseek",
    model_id: str = "deepseek-chat",
    api_key: str = "sk-test-1234567890",
    api_base_url: str | None = "https://api.deepseek.com/v1",
    is_default: bool | None = None,
    supports_vision: bool = False,
) -> dict:
    body: dict = {
        "name": name,
        "provider": provider,
        "modelId": model_id,
        "apiKey": api_key,
        "supportsVision": supports_vision,
    }
    if api_base_url is not None:
        body["apiBaseUrl"] = api_base_url
    if is_default is not None:
        body["isDefault"] = is_default
    return body


# ─── 12.1: CRUD ───────────────────────────────────────────────────────────────


async def test_list_empty(api_client, db):
    resp = await api_client.get("/api/model-profiles")
    assert resp.status_code == 200
    assert resp.json() == {"profiles": []}


async def test_create_profile_first_becomes_default(api_client, db):
    resp = await api_client.post("/api/model-profiles", json=_create_body())
    assert resp.status_code == 201
    profile = resp.json()["profile"]
    assert profile["id"].startswith("mp_")
    assert profile["name"] == "DeepSeek Chat"
    assert profile["provider"] == "deepseek"
    assert profile["modelId"] == "deepseek-chat"
    assert profile["isDefault"] is True
    assert profile["lastTestStatus"] == "untested"
    assert profile["apiKeyMasked"].startswith("****")
    assert "sk-test" not in profile["apiKeyMasked"]
    assert profile["apiKeyMasked"].endswith("7890")


async def test_create_second_profile_not_default(api_client, db):
    await api_client.post("/api/model-profiles", json=_create_body(name="First"))
    resp = await api_client.post(
        "/api/model-profiles",
        json=_create_body(name="Second", model_id="deepseek-coder"),
    )
    assert resp.status_code == 201
    assert resp.json()["profile"]["isDefault"] is False


async def test_set_default_unsets_others(api_client, db):
    r1 = await api_client.post("/api/model-profiles", json=_create_body(name="P1"))
    r2 = await api_client.post(
        "/api/model-profiles",
        json=_create_body(name="P2", model_id="deepseek-coder"),
    )
    p1_id = r1.json()["profile"]["id"]
    p2_id = r2.json()["profile"]["id"]

    resp = await api_client.patch(f"/api/model-profiles/{p2_id}", json={"isDefault": True})
    assert resp.status_code == 200
    assert resp.json()["profile"]["isDefault"] is True

    listing = await api_client.get("/api/model-profiles")
    profiles = {p["id"]: p for p in listing.json()["profiles"]}
    assert profiles[p2_id]["isDefault"] is True
    assert profiles[p1_id]["isDefault"] is False


async def test_delete_default_auto_transfers(api_client, db):
    r1 = await api_client.post("/api/model-profiles", json=_create_body(name="Default"))
    r2 = await api_client.post(
        "/api/model-profiles",
        json=_create_body(name="Other", model_id="deepseek-coder"),
    )
    default_id = r1.json()["profile"]["id"]
    other_id = r2.json()["profile"]["id"]

    resp = await api_client.delete(f"/api/model-profiles/{default_id}")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    listing = await api_client.get("/api/model-profiles")
    profiles = listing.json()["profiles"]
    assert len(profiles) == 1
    assert profiles[0]["id"] == other_id
    assert profiles[0]["isDefault"] is True


async def test_delete_non_default_no_transfer(api_client, db):
    await api_client.post("/api/model-profiles", json=_create_body(name="Default"))
    r2 = await api_client.post(
        "/api/model-profiles",
        json=_create_body(name="Other", model_id="deepseek-coder"),
    )
    other_id = r2.json()["profile"]["id"]

    await api_client.delete(f"/api/model-profiles/{other_id}")

    listing = await api_client.get("/api/model-profiles")
    profiles = listing.json()["profiles"]
    assert len(profiles) == 1
    assert profiles[0]["isDefault"] is True


async def test_delete_not_found(api_client, db):
    resp = await api_client.delete("/api/model-profiles/mp_nonexistent")
    assert resp.status_code == 404


async def test_patch_update_fields(api_client, db):
    created = await api_client.post("/api/model-profiles", json=_create_body())
    pid = created.json()["profile"]["id"]

    resp = await api_client.patch(
        f"/api/model-profiles/{pid}",
        json={"name": "Renamed", "modelId": "deepseek-v4-flash"},
    )
    assert resp.status_code == 200
    profile = resp.json()["profile"]
    assert profile["name"] == "Renamed"
    assert profile["modelId"] == "deepseek-v4-flash"


async def test_patch_api_key_blank_keeps_existing(api_client, db):
    created = await api_client.post(
        "/api/model-profiles",
        json=_create_body(api_key="sk-original-key-9999"),
    )
    pid = created.json()["profile"]["id"]
    assert created.json()["profile"]["apiKeyMasked"] == "****9999"

    resp = await api_client.patch(
        f"/api/model-profiles/{pid}",
        json={"name": "Updated Name"},
    )
    assert resp.status_code == 200
    assert resp.json()["profile"]["apiKeyMasked"] == "****9999"


async def test_invalid_create_body(api_client, db):
    resp = await api_client.post("/api/model-profiles", json={"name": ""})
    assert resp.status_code == 400
    assert resp.json()["error"] == "Invalid body"


# ─── 12.2: Connectivity test ──────────────────────────────────────────────────


async def test_connectivity_ok(api_client, db):
    created = await api_client.post("/api/model-profiles", json=_create_body())
    pid = created.json()["profile"]["id"]

    mock_response = AsyncMock()
    mock_response.model = "deepseek-chat"
    mock_resp = AsyncMock()
    mock_resp.choices = []
    with patch("openai.AsyncOpenAI") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        resp = await api_client.post(f"/api/model-profiles/{pid}/test")

    assert resp.status_code == 200
    result = resp.json()["testResult"]
    assert result["status"] == "ok"
    assert isinstance(result["latencyMs"], int)
    assert result["latencyMs"] >= 0

    listing = await api_client.get("/api/model-profiles")
    profile = next(p for p in listing.json()["profiles"] if p["id"] == pid)
    assert profile["lastTestStatus"] == "ok"
    assert profile["lastTestedAt"] is not None


async def test_connectivity_fail(api_client, db):
    created = await api_client.post("/api/model-profiles", json=_create_body())
    pid = created.json()["profile"]["id"]

    with patch("openai.AsyncOpenAI") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(
            side_effect=Exception("Invalid API key")
        )
        mock_client_cls.return_value = mock_client

        resp = await api_client.post(f"/api/model-profiles/{pid}/test")

    assert resp.status_code == 200
    result = resp.json()["testResult"]
    assert result["status"] == "fail"
    assert "Invalid API key" in result["error"]

    listing = await api_client.get("/api/model-profiles")
    profile = next(p for p in listing.json()["profiles"] if p["id"] == pid)
    assert profile["lastTestStatus"] == "fail"


async def test_connectivity_rate_limited(api_client, db):
    created = await api_client.post("/api/model-profiles", json=_create_body())
    pid = created.json()["profile"]["id"]

    from app.api.model_profiles import _test_cooldowns

    _test_cooldowns[pid] = time.monotonic()

    resp = await api_client.post(f"/api/model-profiles/{pid}/test")
    assert resp.status_code == 429
    assert "wait" in resp.json()["error"].lower()

    del _test_cooldowns[pid]


async def test_connectivity_not_found(api_client, db):
    resp = await api_client.post("/api/model-profiles/mp_nonexistent/test")
    assert resp.status_code == 404
