"""HTTP contract tests for the conversations router.

Uses the `api_client` fixture (real routes over ASGITransport, shared test DB)
and the `agents` fixture (two seeded mock-adapter agents). asyncio_mode=auto.
"""


async def _create_single(api_client, agent_id: str, title: str | None = None) -> dict:
    body = {"mode": "single", "agentIds": [agent_id]}
    if title is not None:
        body["title"] = title
    resp = await api_client.post("/api/conversations", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["conversation"]


# ─── create / list ───────────────────────────────────────────────────────────
async def test_create_conversation_happy(api_client, agents):
    resp = await api_client.post(
        "/api/conversations",
        json={"mode": "single", "agentIds": [agents["alice"]], "title": "Hi"},
    )
    assert resp.status_code == 201
    conv = resp.json()["conversation"]
    assert conv["title"] == "Hi"
    assert conv["mode"] == "single"
    assert conv["agentIds"] == [agents["alice"]]
    assert conv["workspaceMode"] == "sandbox"


async def test_create_conversation_invalid_body(api_client, agents):
    # Missing required `mode`.
    resp = await api_client.post(
        "/api/conversations", json={"agentIds": [agents["alice"]]}
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "Invalid body"


async def test_create_conversation_unknown_agent(api_client, agents):
    resp = await api_client.post(
        "/api/conversations", json={"mode": "single", "agentIds": ["nope"]}
    )
    assert resp.status_code == 400
    assert "not found" in resp.json()["error"].lower()


async def test_list_conversations(api_client, agents):
    await _create_single(api_client, agents["alice"])
    resp = await api_client.get("/api/conversations")
    assert resp.status_code == 200
    convs = resp.json()["conversations"]
    assert isinstance(convs, list)
    assert len(convs) >= 1


# ─── patch ───────────────────────────────────────────────────────────────────
async def test_patch_rename(api_client, agents):
    conv = await _create_single(api_client, agents["alice"])
    resp = await api_client.patch(
        f"/api/conversations/{conv['id']}", json={"title": "Renamed"}
    )
    assert resp.status_code == 200
    assert resp.json()["conversation"]["title"] == "Renamed"


async def test_patch_toggle_pin(api_client, agents):
    conv = await _create_single(api_client, agents["alice"])
    resp = await api_client.patch(
        f"/api/conversations/{conv['id']}", json={"togglePin": True}
    )
    assert resp.status_code == 200
    assert resp.json()["conversation"]["pinnedAt"] is not None


async def test_patch_approval_mode(api_client, agents):
    conv = await _create_single(api_client, agents["alice"])
    resp = await api_client.patch(
        f"/api/conversations/{conv['id']}", json={"fsWriteApprovalMode": "auto"}
    )
    assert resp.status_code == 200
    assert resp.json()["conversation"]["fsWriteApprovalMode"] == "auto"


async def test_patch_add_agents(api_client, agents):
    conv = await _create_single(api_client, agents["alice"])
    resp = await api_client.patch(
        f"/api/conversations/{conv['id']}", json={"addAgentIds": [agents["orch"]]}
    )
    assert resp.status_code == 200
    out = resp.json()["conversation"]
    assert agents["orch"] in out["agentIds"]
    assert out["mode"] == "group"


async def test_patch_empty_body_invalid(api_client, agents):
    conv = await _create_single(api_client, agents["alice"])
    resp = await api_client.patch(f"/api/conversations/{conv['id']}", json={})
    assert resp.status_code == 400
    assert resp.json()["error"] == "Invalid body"


# ─── delete ──────────────────────────────────────────────────────────────────
async def test_delete_conversation(api_client, agents):
    conv = await _create_single(api_client, agents["alice"])
    resp = await api_client.delete(f"/api/conversations/{conv['id']}")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


async def test_delete_missing_conversation(api_client, agents):
    resp = await api_client.delete("/api/conversations/conv_missing")
    assert resp.status_code == 404
    assert "error" in resp.json()


# ─── messages ────────────────────────────────────────────────────────────────
async def test_list_messages_empty(api_client, agents):
    conv = await _create_single(api_client, agents["alice"])
    resp = await api_client.get(f"/api/conversations/{conv['id']}/messages")
    assert resp.status_code == 200
    assert resp.json()["messages"] == []


async def test_send_message_happy(api_client, agents):
    conv = await _create_single(api_client, agents["alice"])
    resp = await api_client.post(
        f"/api/conversations/{conv['id']}/messages", json={"content": "hello"}
    )
    assert resp.status_code == 202
    out = resp.json()
    assert "messageId" in out
    assert isinstance(out["runIds"], list)

    # The user message is persisted and listable.
    listed = await api_client.get(f"/api/conversations/{conv['id']}/messages")
    msgs = listed.json()["messages"]
    assert any(m["id"] == out["messageId"] for m in msgs)


async def test_send_message_empty_invalid(api_client, agents):
    conv = await _create_single(api_client, agents["alice"])
    resp = await api_client.post(
        f"/api/conversations/{conv['id']}/messages", json={"content": "   "}
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "Invalid body"


async def test_send_message_missing_conversation(api_client, agents):
    resp = await api_client.post(
        "/api/conversations/conv_missing/messages", json={"content": "hi"}
    )
    assert resp.status_code == 400
    assert "error" in resp.json()


async def test_clear_history(api_client, agents):
    conv = await _create_single(api_client, agents["alice"])
    resp = await api_client.delete(f"/api/conversations/{conv['id']}/messages")
    assert resp.status_code == 200
    out = resp.json()
    assert out["conversation"]["id"] == conv["id"]
    assert "deletedMessageCount" in out
    assert "deletedRunCount" in out
    assert "deletedSummaryCount" in out


async def test_clear_history_missing_conversation(api_client, agents):
    resp = await api_client.delete("/api/conversations/conv_missing/messages")
    assert resp.status_code == 404
    assert "error" in resp.json()


# ─── regenerate ──────────────────────────────────────────────────────────────
async def test_regenerate_no_user_message(api_client, agents):
    # No user message yet -> service raises -> 400.
    conv = await _create_single(api_client, agents["alice"])
    resp = await api_client.post(f"/api/conversations/{conv['id']}/regenerate")
    assert resp.status_code == 400
    assert "error" in resp.json()


# ─── compact (deferred) ──────────────────────────────────────────────────────
async def test_compact_deferred(api_client, agents):
    # Nothing to compact is a benign skip, not an error: 200 + a friendly
    # ephemeral system message, no 400.
    conv = await _create_single(api_client, agents["alice"])
    resp = await api_client.post(f"/api/conversations/{conv['id']}/compact")
    assert resp.status_code == 200
    body = resp.json()
    assert body["skipped"] is True
    assert body.get("reason")
    assert body["message"]["role"] == "system"
    assert "error" not in body


# ─── deploy ──────────────────────────────────────────────────────────────────
async def test_deploy_candidates_empty(api_client, agents):
    conv = await _create_single(api_client, agents["alice"])
    resp = await api_client.get(f"/api/conversations/{conv['id']}/deploy")
    assert resp.status_code == 200
    assert resp.json()["candidates"] == []


async def test_deploy_no_candidates(api_client, agents):
    conv = await _create_single(api_client, agents["alice"])
    resp = await api_client.post(f"/api/conversations/{conv['id']}/deploy", json={})
    assert resp.status_code == 200
    out = resp.json()
    assert out["kind"] == "no_candidates"
    assert out["candidates"] == []
    assert out["message"]["conversationId"] == conv["id"]
