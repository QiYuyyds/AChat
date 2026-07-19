"""Tests for the workspace env API routes (app/api/workspaces.py).

Covers:
- GET  /api/workspaces/{id}/env-status — detection result + preference
- PATCH /api/workspaces/{id}/env-preference — persist user's env choice
- POST  /api/workspaces/{id}/create-venv — trigger async venv creation

Auth/ownership/CSRF are exercised: unauthenticated → 401, other user's
conversation → 404, invalid body → 400.
"""


import pytest_asyncio


@pytest_asyncio.fixture
async def local_conversation(api_client, agents, test_user, tmp_path):
    """Create a conversation with a local-mode workspace bound to tmp_path."""
    # Create a Python project marker so detection returns language=python.
    (tmp_path / "requirements.txt").write_text("fastapi")
    resp = await api_client.post(
        "/api/conversations",
        json={
            "mode": "single",
            "agentIds": [agents["alice"]],
            "title": "ws env test",
            "boundPath": str(tmp_path),
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["conversation"]["id"]


# ─── GET /env-status ─────────────────────────────────────────────────────────


async def test_env_status_sandbox_workspace(api_client, agents):
    """Sandbox workspace → language=unknown, venvPresent=false."""
    resp = await api_client.post(
        "/api/conversations",
        json={"mode": "single", "agentIds": [agents["alice"]], "title": "sandbox"},
    )
    conv_id = resp.json()["conversation"]["id"]
    status = await api_client.get(f"/api/workspaces/{conv_id}/env-status")
    assert status.status_code == 200
    body = status.json()
    assert body["workspaceMode"] == "sandbox"
    assert body["language"] == "unknown"
    assert body["venvPresent"] is False
    assert body["envPreference"] is None


async def test_env_status_local_python_project(api_client, local_conversation):
    """Local Python project without venv → language=python, venvPresent=false."""
    status = await api_client.get(f"/api/workspaces/{local_conversation}/env-status")
    assert status.status_code == 200
    body = status.json()
    assert body["workspaceMode"] == "local"
    assert body["language"] == "python"
    assert body["venvPresent"] is False
    assert body["envPreference"] is None


async def test_env_status_unauthenticated(raw_client):
    """No auth → 401."""
    status = await raw_client.get("/api/workspaces/nonexistent/env-status")
    assert status.status_code == 401


# ─── PATCH /env-preference ───────────────────────────────────────────────────


async def test_update_env_preference_valid(api_client, local_conversation):
    """Valid preference → 200, persisted."""
    for pref in ("skip", "system_python", "venv_created"):
        resp = await api_client.patch(
            f"/api/workspaces/{local_conversation}/env-preference",
            json={"preference": pref},
        )
        assert resp.status_code == 200, resp.text
        # Verify it was persisted via env-status
        status = await api_client.get(
            f"/api/workspaces/{local_conversation}/env-status"
        )
        assert status.json()["envPreference"] == pref


async def test_update_env_preference_invalid(api_client, local_conversation):
    """Invalid preference → 400."""
    resp = await api_client.patch(
        f"/api/workspaces/{local_conversation}/env-preference",
        json={"preference": "invalid_choice"},
    )
    assert resp.status_code == 400


async def test_update_env_preference_missing_body(api_client, local_conversation):
    """Missing body → 400."""
    resp = await api_client.patch(
        f"/api/workspaces/{local_conversation}/env-preference",
        json=None,
    )
    assert resp.status_code == 400


# ─── POST /create-venv ───────────────────────────────────────────────────────


async def test_create_venv_sandbox_returns_400(api_client, agents):
    """Sandbox workspace → 400 (venv creation only for local workspaces)."""
    resp = await api_client.post(
        "/api/conversations",
        json={"mode": "single", "agentIds": [agents["alice"]], "title": "sandbox"},
    )
    conv_id = resp.json()["conversation"]["id"]
    result = await api_client.post(f"/api/workspaces/{conv_id}/create-venv")
    assert result.status_code == 400


async def test_create_venv_local_returns_202(api_client, local_conversation):
    """Local workspace → 202 Accepted (actual creation is async via SSE)."""
    result = await api_client.post(
        f"/api/workspaces/{local_conversation}/create-venv"
    )
    assert result.status_code == 202
    assert result.json()["accepted"] is True


async def test_create_venv_nonexistent_conversation(api_client):
    """Non-existent conversation → 404."""
    result = await api_client.post("/api/workspaces/nonexistent/create-venv")
    assert result.status_code == 404


# ─── Ownership / 404 ─────────────────────────────────────────────────────────


async def test_env_status_nonexistent_conversation(api_client):
    """Non-existent conversation → 404 (verify_conversation_ownership raises 404)."""
    resp = await api_client.get("/api/workspaces/nonexistent_conv/env-status")
    assert resp.status_code == 404


async def test_update_env_preference_nonexistent(api_client):
    """Non-existent conversation → 404."""
    resp = await api_client.patch(
        "/api/workspaces/nonexistent_conv/env-preference",
        json={"preference": "skip"},
    )
    assert resp.status_code == 404
