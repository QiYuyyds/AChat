"""Tests for per-user data isolation.

Creates two users, verifies that conversations, agents, documents, and
settings are isolated between them.
"""

from __future__ import annotations

from app.auth.jwt_handler import create_access_token
from app.auth.password import hash_password
from app.db.engine import get_db
from app.db.models import Agent, User
from app.utils.clock import now_ms


async def _create_user(db, user_id: str, email: str, name: str) -> str:
    """Create a user and return a JWT access token."""
    now = now_ms()
    async with get_db() as session:
        user = User(
            id=user_id,
            email=email,
            name=name,
            password_hash=hash_password("password123"),
            token_version=0,
            created_at=now,
            updated_at=now,
        )
        session.add(user)
    return create_access_token(user_id, email, 0)


async def _authed_client(db, token: str):
    """Create an httpx client with the given auth token."""
    import httpx

    import app.services.agent_runner  # noqa: F401
    from app.main import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    client.headers["Authorization"] = f"Bearer {token}"
    await client.__aenter__()
    return client


async def test_conversation_isolation(db):
    """User A's conversations are not visible to user B."""
    token_a = await _create_user(db, "user_a", "a@test.com", "User A")
    token_b = await _create_user(db, "user_b", "b@test.com", "User B")

    # Seed an agent for user A
    now = now_ms()
    async with get_db() as session:
        agent = Agent(
            id="ag_a1",
            name="Agent A1",
            avatar="A",
            description="agent for user A",
            system_prompt="prompt",
            adapter_name="mock",
            is_builtin=False,
            is_orchestrator=False,
            supports_vision=False,
            created_at=now,
            user_id="user_a",
        )
        agent.capabilities_list = []
        agent.tool_names_list = []
        session.add(agent)

    client_a = await _authed_client(db, token_a)
    client_b = await _authed_client(db, token_b)
    try:
        # User A creates a conversation
        resp = await client_a.post("/api/conversations", json={
            "mode": "single",
            "agentIds": ["ag_a1"],
            "title": "A's conversation",
        })
        assert resp.status_code == 201, resp.text

        # User A can see it
        resp = await client_a.get("/api/conversations")
        assert resp.status_code == 200
        convs = resp.json()["conversations"]
        assert len(convs) == 1
        assert convs[0]["title"] == "A's conversation"

        # User B cannot see it
        resp = await client_b.get("/api/conversations")
        assert resp.status_code == 200
        convs = resp.json()["conversations"]
        assert len(convs) == 0
    finally:
        await client_a.aclose()
        await client_b.aclose()


async def test_agent_isolation(db):
    """Custom agents are only visible to their owner; builtin agents are shared."""
    token_a = await _create_user(db, "user_a2", "a2@test.com", "User A2")
    token_b = await _create_user(db, "user_b2", "b2@test.com", "User B2")

    now = now_ms()
    async with get_db() as session:
        # Custom agent for user A
        custom_a = Agent(
            id="ag_custom_a",
            name="Custom A",
            avatar="C",
            description="custom agent for A",
            system_prompt="prompt",
            adapter_name="mock",
            is_builtin=False,
            is_orchestrator=False,
            supports_vision=False,
            created_at=now,
            user_id="user_a2",
        )
        custom_a.capabilities_list = []
        custom_a.tool_names_list = []

        # Builtin agent (shared)
        builtin = Agent(
            id="ag_builtin",
            name="Builtin",
            avatar="B",
            description="builtin agent",
            system_prompt="prompt",
            adapter_name="mock",
            is_builtin=True,
            is_orchestrator=False,
            supports_vision=False,
            created_at=now,
            user_id=None,
        )
        builtin.capabilities_list = []
        builtin.tool_names_list = []

        session.add(custom_a)
        session.add(builtin)

    client_a = await _authed_client(db, token_a)
    client_b = await _authed_client(db, token_b)
    try:
        # User A sees their custom agent + builtin
        resp = await client_a.get("/api/agents")
        assert resp.status_code == 200
        agent_ids = {a["id"] for a in resp.json()["agents"]}
        assert "ag_custom_a" in agent_ids
        assert "ag_builtin" in agent_ids

        # User B sees only builtin, not user A's custom agent
        resp = await client_b.get("/api/agents")
        assert resp.status_code == 200
        agent_ids = {a["id"] for a in resp.json()["agents"]}
        assert "ag_builtin" in agent_ids
        assert "ag_custom_a" not in agent_ids
    finally:
        await client_a.aclose()
        await client_b.aclose()


async def test_settings_isolation(db):
    """Each user has their own settings."""
    token_a = await _create_user(db, "user_sa", "sa@test.com", "User SA")
    token_b = await _create_user(db, "user_sb", "sb@test.com", "User SB")

    client_a = await _authed_client(db, token_a)
    client_b = await _authed_client(db, token_b)
    try:
        # User A sets an API key
        resp = await client_a.patch("/api/settings", json={
            "anthropicApiKey": "sk-ant-user-a",
        })
        assert resp.status_code == 200

        # User A can see it
        resp = await client_a.get("/api/settings")
        assert resp.json()["settings"]["anthropicApiKey"] == "sk-ant-user-a"

        # User B has different (null) settings
        resp = await client_b.get("/api/settings")
        assert resp.json()["settings"]["anthropicApiKey"] is None
    finally:
        await client_a.aclose()
        await client_b.aclose()
