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
    "toolNames",
    "skillNames",
    "mcpServerIds",
    "protocolFamily",
    "executablePath",
    "customArgs",
    "isBuiltin",
    "isOrchestrator",
    "isGuide",
    "memoryEnabled",
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
            "toolNames": ["bash", "fs_read"],
        },
    )
    assert resp.status_code == 201
    agent = resp.json()["agent"]
    assert set(agent.keys()) == _AGENT_ROW_KEYS
    assert agent["id"].startswith("ag_")
    assert agent["name"] == "Coder"
    assert agent["adapterName"] == "custom"
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
        },
    )
    assert resp.status_code == 201
    agent = resp.json()["agent"]
    assert agent["adapterName"] == "custom"  # defaulted
    assert agent["avatar"] == "🤖"  # defaulted


async def test_create_cli_adapter_clears_tools(api_client, db):
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
    assert agent["toolNames"] == []


async def test_create_custom_no_longer_requires_model(api_client, db):
    resp = await api_client.post(
        "/api/agents",
        json={
            "name": "NoModel",
            "description": "desc",
            "systemPrompt": "p",
            "adapterName": "custom",
        },
    )
    assert resp.status_code == 201
    agent = resp.json()["agent"]
    assert agent["adapterName"] == "custom"


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


async def test_patch_rejects_unknown_model_fields(api_client, db):
    # Model fields are no longer part of the Agent entity;
    # PATCH with them should be rejected as unrecognized keys.
    created = await api_client.post(
        "/api/agents",
        json={
            "name": "Test",
            "description": "d",
            "systemPrompt": "p",
            "adapterName": "custom",
        },
    )
    aid = created.json()["agent"]["id"]

    resp = await api_client.patch(f"/api/agents/{aid}", json={"modelProvider": "deepseek"})
    assert resp.status_code == 400
    assert resp.json()["error"] == "Invalid body"


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
    assert "write_artifact" in draft["toolNames"]
    assert len(draft["toolPermissionSummaries"]) == len(draft["toolNames"])
    assert draft["systemPrompt"].startswith("你是")


async def test_draft_local_code_preset(api_client, db):
    resp = await api_client.post(
        "/api/agents/draft",
        json={"intent": "需要一个能写代码并运行命令的本地工程师"},
    )
    assert resp.status_code == 200
    draft = resp.json()["draft"]
    # coder preset returns deploy_workspace + read_artifact (baseline tools like
    # bash are always-on and not listed in toolNames)
    assert "deploy_workspace" in draft["toolNames"]
    assert "write_artifact" not in draft["toolNames"]


async def test_draft_invalid_too_short(api_client, db):
    resp = await api_client.post("/api/agents/draft", json={"intent": "hi"})
    assert resp.status_code == 400
    assert resp.json()["error"] == "Invalid body"


# ─── Orchestrator designation ───────────────────────────────────────
async def test_create_orchestrator_agent(api_client, db):
    resp = await api_client.post(
        "/api/agents",
        json={
            "name": "Planner",
            "description": "orchestrator agent",
            "systemPrompt": "you plan tasks",
            "adapterName": "custom",
            "toolNames": ["bash", "fs_read"],
            "isOrchestrator": True,
        },
    )
    assert resp.status_code == 201
    agent = resp.json()["agent"]
    assert agent["isOrchestrator"] is True
    # ask_user should be auto-added for orchestrator agents
    assert "ask_user" in agent["toolNames"]
    # Original tools preserved
    assert "bash" in agent["toolNames"]
    assert "fs_read" in agent["toolNames"]


async def test_create_non_orchestrator_default(api_client, db):
    resp = await api_client.post(
        "/api/agents",
        json={
            "name": "Normal",
            "description": "normal agent",
            "systemPrompt": "p",
            "adapterName": "custom",
        },
    )
    assert resp.status_code == 201
    agent = resp.json()["agent"]
    assert agent["isOrchestrator"] is False
    assert "ask_user" not in agent["toolNames"]


async def test_patch_update_is_orchestrator(api_client, agents):
    # Alice starts as non-orchestrator
    resp = await api_client.patch(
        "/api/agents/ag_alice",
        json={"isOrchestrator": True},
    )
    assert resp.status_code == 200
    agent = resp.json()["agent"]
    assert agent["isOrchestrator"] is True

    # Toggle back
    resp2 = await api_client.patch(
        "/api/agents/ag_alice",
        json={"isOrchestrator": False},
    )
    assert resp2.status_code == 200
    assert resp2.json()["agent"]["isOrchestrator"] is False
