"""API tests for the agents router (app/api/agents.py).

Covers GET /api/agents, POST /api/agents, PATCH /api/agents/{id},
DELETE /api/agents/{id}, and POST /api/agents/draft — happy path plus at
least one error path each. Uses the api_client fixture (shares the isolated
test DB) and the seeded `agents` fixture from conftest.
"""

from __future__ import annotations

_AGENT_ROW_KEYS = {
    "id",
    "name",
    "avatar",
    "description",
    "capabilities",
    "systemPrompt",
    "adapterName",
    "modelProvider",
    "modelId",
    "apiKey",
    "apiBaseUrl",
    "toolNames",
    "isBuiltin",
    "isOrchestrator",
    "supportsVision",
    "createdAt",
}


# ─── GET /api/agents ────────────────────────────────────────────────
async def test_list_agents_builtin_first(api_client, agents):
    resp = await api_client.get("/api/agents")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"agents"}
    rows = body["agents"]
    assert len(rows) == 2
    # Full AgentRow shape, including apiKey (byte-for-byte with the frontend).
    assert set(rows[0].keys()) == _AGENT_ROW_KEYS
    # builtin (orchestrator) comes first.
    assert rows[0]["id"] == "ag_orch"
    assert rows[0]["isBuiltin"] is True
    assert rows[1]["id"] == "ag_alice"


async def test_list_agents_empty(api_client, db):
    resp = await api_client.get("/api/agents")
    assert resp.status_code == 200
    assert resp.json() == {"agents": []}


# ─── POST /api/agents ───────────────────────────────────────────────
async def test_create_custom_agent_happy(api_client, db):
    resp = await api_client.post(
        "/api/agents",
        json={
            "name": "Coder",
            "avatar": "🛠",
            "description": "writes code",
            "capabilities": ["code"],
            "systemPrompt": "you write code",
            "adapterName": "custom",
            "modelProvider": "deepseek",
            "modelId": "deepseek-v4-flash",
            "toolNames": ["bash", "fs_read"],
            "supportsVision": True,
        },
    )
    assert resp.status_code == 201
    agent = resp.json()["agent"]
    assert set(agent.keys()) == _AGENT_ROW_KEYS
    assert agent["id"].startswith("ag_")
    assert agent["name"] == "Coder"
    assert agent["adapterName"] == "custom"
    assert agent["modelProvider"] == "deepseek"
    assert agent["toolNames"] == ["bash", "fs_read"]
    assert agent["isBuiltin"] is False
    assert agent["isOrchestrator"] is False


async def test_create_agent_defaults_adapter_and_avatar(api_client, db):
    resp = await api_client.post(
        "/api/agents",
        json={
            "name": "NoAvatar",
            "description": "desc",
            "systemPrompt": "p",
            "modelProvider": "openai",
            "modelId": "gpt-4o",
        },
    )
    assert resp.status_code == 201
    agent = resp.json()["agent"]
    assert agent["adapterName"] == "custom"  # defaulted
    assert agent["avatar"] == "🤖"  # defaulted


async def test_create_sdk_adapter_clears_tools_and_provider(api_client, db):
    resp = await api_client.post(
        "/api/agents",
        json={
            "name": "Claude",
            "description": "sdk agent",
            "systemPrompt": "p",
            "adapterName": "claude-code",
            "toolNames": ["bash"],
        },
    )
    assert resp.status_code == 201
    agent = resp.json()["agent"]
    assert agent["adapterName"] == "claude-code"
    assert agent["modelProvider"] is None
    assert agent["toolNames"] == []


async def test_create_custom_requires_model(api_client, db):
    resp = await api_client.post(
        "/api/agents",
        json={
            "name": "Bad",
            "description": "desc",
            "systemPrompt": "p",
            "adapterName": "custom",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "Custom adapter requires modelProvider and modelId"


async def test_create_invalid_body(api_client, db):
    resp = await api_client.post("/api/agents", json={"name": ""})
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "Invalid body"
    assert "issues" in body


# ─── PATCH /api/agents/{id} ─────────────────────────────────────────
async def test_patch_updates_fields(api_client, agents):
    resp = await api_client.patch(
        "/api/agents/ag_alice",
        json={"name": "Alice2", "description": "updated"},
    )
    assert resp.status_code == 200
    agent = resp.json()["agent"]
    assert agent["name"] == "Alice2"
    assert agent["description"] == "updated"


async def test_patch_clears_api_key_with_null(api_client, db):
    # Create a custom agent with an api key, then clear it via null.
    created = await api_client.post(
        "/api/agents",
        json={
            "name": "Keyed",
            "description": "d",
            "systemPrompt": "p",
            "adapterName": "custom",
            "modelProvider": "deepseek",
            "modelId": "deepseek-v4-flash",
            "apiKey": "sk-secret",
        },
    )
    aid = created.json()["agent"]["id"]
    assert created.json()["agent"]["apiKey"] == "sk-secret"

    resp = await api_client.patch(f"/api/agents/{aid}", json={"apiKey": None})
    assert resp.status_code == 200
    assert resp.json()["agent"]["apiKey"] is None


async def test_patch_unknown_key_rejected(api_client, agents):
    resp = await api_client.patch("/api/agents/ag_alice", json={"bogus": 1})
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "Invalid body"
    assert body["issues"][0]["code"] == "unrecognized_keys"


async def test_patch_missing_agent(api_client, db):
    resp = await api_client.patch("/api/agents/ag_nope", json={"name": "X"})
    assert resp.status_code == 400
    assert resp.json()["error"] == "Agent not found: ag_nope"


# ─── DELETE /api/agents/{id} ────────────────────────────────────────
async def test_delete_custom_agent(api_client, db):
    created = await api_client.post(
        "/api/agents",
        json={
            "name": "Tmp",
            "description": "d",
            "systemPrompt": "p",
            "adapterName": "custom",
            "modelProvider": "openai",
            "modelId": "gpt-4o",
        },
    )
    aid = created.json()["agent"]["id"]
    resp = await api_client.delete(f"/api/agents/{aid}")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    # Now gone.
    listing = await api_client.get("/api/agents")
    assert all(a["id"] != aid for a in listing.json()["agents"])


async def test_delete_builtin_rejected(api_client, agents):
    resp = await api_client.delete("/api/agents/ag_orch")
    assert resp.status_code == 400
    assert resp.json()["error"] == "Built-in agents cannot be deleted"


async def test_delete_missing_agent(api_client, db):
    resp = await api_client.delete("/api/agents/ag_nope")
    assert resp.status_code == 400
    assert resp.json()["error"] == "Agent not found: ag_nope"


# ─── POST /api/agents/draft ─────────────────────────────────────────
async def test_draft_artifact_intent(api_client, db):
    resp = await api_client.post(
        "/api/agents/draft",
        json={"intent": "帮我生成网页原型和文档"},
    )
    assert resp.status_code == 200
    draft = resp.json()["draft"]
    assert draft["adapterName"] == "custom"
    assert draft["modelProvider"] == "deepseek"
    assert draft["modelId"] == "deepseek-v4-flash"
    assert draft["supportsVision"] is True
    assert "write_artifact" in draft["toolNames"]
    assert len(draft["toolPermissionSummaries"]) == len(draft["toolNames"])
    assert draft["systemPrompt"].startswith(f"你是 {draft['name']}。")


async def test_draft_local_code_preset(api_client, db):
    resp = await api_client.post(
        "/api/agents/draft",
        json={"intent": "需要一个能写代码并运行命令的本地工程师"},
    )
    assert resp.status_code == 200
    draft = resp.json()["draft"]
    assert "bash" in draft["toolNames"]
    assert "write_artifact" not in draft["toolNames"]


async def test_draft_invalid_too_short(api_client, db):
    resp = await api_client.post("/api/agents/draft", json={"intent": "hi"})
    assert resp.status_code == 400
    assert resp.json()["error"] == "Invalid body"
