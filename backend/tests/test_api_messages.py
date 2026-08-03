"""Tests for the messages API routes (phase 6).

Covers POST /api/messages/{id}/edit | withdraw | pin | bookmark, each with a
happy path and at least one error path. Uses the lifespan-independent
`api_client` fixture (shares the `db` + `agents` fixtures' isolated DB).
"""

from app.services import conversation_service as cs


async def _new_message(agents) -> tuple[str, str]:
    """Create a single conversation, send one user message, return (conv_id, msg_id)."""
    conv = await cs.create_conversation(mode="single", agent_ids=[agents["alice"]])
    sent = await cs.send_message(conversation_id=conv.id, content="hello")
    return conv.id, sent.message_id


# ─── edit ────────────────────────────────────────────────────────────────────
async def test_edit_happy_path(api_client, agents):
    conv_id, msg_id = await _new_message(agents)
    resp = await api_client.post(
        f"/api/messages/{msg_id}/edit",
        json={"conversationId": conv_id, "content": "fixed"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert msg_id in body["deletedMessageIds"]
    assert "deletedArtifactIds" in body
    assert body["runIds"] is not None
    assert body["newMessage"]["parts"] == [{"type": "text", "content": "fixed"}]
    # camelCase wire shape on the nested message
    assert body["newMessage"]["conversationId"] == conv_id


async def test_edit_invalid_body_returns_400(api_client, agents):
    conv_id, msg_id = await _new_message(agents)
    # missing content
    resp = await api_client.post(
        f"/api/messages/{msg_id}/edit", json={"conversationId": conv_id}
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "Invalid body"
    assert "issues" in resp.json()


async def test_edit_unknown_message_returns_404(api_client, agents):
    conv = await cs.create_conversation(mode="single", agent_ids=[agents["alice"]])
    resp = await api_client.post(
        "/api/messages/msg_missing/edit",
        json={"conversationId": conv.id, "content": "x"},
    )
    assert resp.status_code == 404
    assert "not found" in resp.json()["error"].lower()


# ─── withdraw ────────────────────────────────────────────────────────────────
async def test_withdraw_happy_path(api_client, agents):
    conv_id, msg_id = await _new_message(agents)
    resp = await api_client.post(
        f"/api/messages/{msg_id}/withdraw", json={"conversationId": conv_id}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert msg_id in body["deletedMessageIds"]
    assert "deletedArtifactIds" in body


async def test_withdraw_non_latest_returns_400(api_client, agents):
    conv = await cs.create_conversation(mode="single", agent_ids=[agents["alice"]])
    first = await cs.send_message(conversation_id=conv.id, content="first")
    await cs.send_message(conversation_id=conv.id, content="second")
    resp = await api_client.post(
        f"/api/messages/{first.message_id}/withdraw",
        json={"conversationId": conv.id},
    )
    # "latest user message" error -> not "not found" -> 400
    assert resp.status_code == 400
    assert "error" in resp.json()


async def test_withdraw_invalid_body_returns_400(api_client, agents):
    resp = await api_client.post("/api/messages/m1/withdraw", json={})
    assert resp.status_code == 400
    assert resp.json()["error"] == "Invalid body"


# ─── pin ─────────────────────────────────────────────────────────────────────
async def test_pin_toggle_happy_path(api_client, agents):
    conv_id, msg_id = await _new_message(agents)
    resp = await api_client.post(
        f"/api/messages/{msg_id}/pin", json={"conversationId": conv_id}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["pinned"] is True
    assert msg_id in body["pinnedMessageIds"]

    # toggling again unpins
    resp2 = await api_client.post(
        f"/api/messages/{msg_id}/pin", json={"conversationId": conv_id}
    )
    assert resp2.status_code == 200
    assert resp2.json()["pinned"] is False


async def test_pin_unknown_message_returns_400(api_client, agents):
    conv = await cs.create_conversation(mode="single", agent_ids=[agents["alice"]])
    resp = await api_client.post(
        "/api/messages/msg_missing/pin", json={"conversationId": conv.id}
    )
    # pin route maps all service errors to 400
    assert resp.status_code == 400
    assert "error" in resp.json()


# ─── bookmark ────────────────────────────────────────────────────────────────
async def test_bookmark_toggle_happy_path(api_client, agents):
    conv_id, msg_id = await _new_message(agents)
    resp = await api_client.post(
        f"/api/messages/{msg_id}/bookmark", json={"conversationId": conv_id}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["bookmarked"] is True
    assert msg_id in body["bookmarkedMessageIds"]


async def test_bookmark_invalid_body_returns_400(api_client, agents):
    resp = await api_client.post("/api/messages/m1/bookmark", json={})
    assert resp.status_code == 400
    assert resp.json()["error"] == "Invalid body"


# ─── group chat without Orchestrator ─────────────────────────────────
async def test_group_chat_no_orchestrator_system_message(db, agents):
    """Sending a message in a group chat without an Orchestrator (and no @mention)
    should produce a system message informing the user."""
    # Create a group chat with two non-orchestrator agents.
    # Seed a second non-orchestrator agent for this test.
    from app.db.engine import get_db
    from app.db.models import Agent
    from app.utils.clock import now_ms

    async with get_db() as session:
        bob = Agent(
            id="ag_bob",
            name="Bob",
            avatar="B",
            description="another helper",
            system_prompt="bob prompt",
            adapter_name="mock",
            is_builtin=False,
            is_orchestrator=False,
            created_at=now_ms(),
        )
        bob.capabilities_list = []
        bob.tool_names_list = []
        session.add(bob)

    conv = await cs.create_conversation(
        mode="group", agent_ids=[agents["alice"], "ag_bob"]
    )
    result = await cs.send_message(conversation_id=conv.id, content="hello everyone")

    # No runs should be started (no orchestrator, no mention)
    assert result.run_ids == []
    # A system message should be in the result
    assert result.messages is not None
    assert len(result.messages) == 1
    sys_msg = result.messages[0]
    assert sys_msg.role == "agent"
    assert sys_msg.agent_id is None
    assert any(
        p.get("content", "").startswith("此群聊没有协调者")
        for p in sys_msg.parts
    )


async def test_group_chat_with_orchestrator_no_system_message(db, agents):
    """When a group chat has an Orchestrator, no system message should appear."""
    conv = await cs.create_conversation(
        mode="group", agent_ids=[agents["alice"], agents["orch"]]
    )
    result = await cs.send_message(conversation_id=conv.id, content="hello")
    # Orchestrator should respond, so no system message
    assert result.messages is None or len(result.messages) == 0
    assert len(result.run_ids) > 0


async def test_group_chat_no_orchestrator_with_mention(db, agents):
    """When @mentioning an agent in a group without Orchestrator, that agent responds normally."""
    from app.db.engine import get_db
    from app.db.models import Agent
    from app.utils.clock import now_ms

    async with get_db() as session:
        bob = Agent(
            id="ag_bob2",
            name="Bob2",
            avatar="B",
            description="another helper",
            system_prompt="bob prompt",
            adapter_name="mock",
            is_builtin=False,
            is_orchestrator=False,
            created_at=now_ms(),
        )
        bob.capabilities_list = []
        bob.tool_names_list = []
        session.add(bob)

    conv = await cs.create_conversation(
        mode="group", agent_ids=[agents["alice"], "ag_bob2"]
    )
    result = await cs.send_message(
        conversation_id=conv.id,
        content="@Alice help me",
        mentioned_agent_ids=[agents["alice"]],
    )
    # Alice should respond, no system message
    assert result.messages is None or len(result.messages) == 0
    assert len(result.run_ids) > 0
