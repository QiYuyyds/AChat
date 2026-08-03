"""Integration tests for ModelProfile migration and send_message with modelProfileId.

Covers:
  12.6 — send_message accepts modelProfileId and passes it to RunArgs
  12.9 — old Agent model config migrates to ModelProfiles
"""

from __future__ import annotations

import contextlib
from unittest.mock import patch

# ─── 12.6: send_message accepts modelProfileId ────────────────────────────────


async def test_send_message_passes_model_profile_id(api_client, db, test_user):
    """send_message should accept modelProfileId and pass it through."""
    from app.db.engine import get_db
    from app.db.models import Agent, Conversation, ModelProfile, Workspace
    from app.utils.clock import now_ms
    from app.utils.ids import new_conversation_id, new_workspace_id

    now = now_ms()

    # Create a model profile
    async with get_db() as session:
        profile = ModelProfile(
            id="mp_test_send",
            user_id=test_user["id"],
            name="Test Profile",
            provider="deepseek",
            model_id="deepseek-chat",
            api_key="sk-test",
            api_base_url="https://api.deepseek.com/v1",
            is_default=True,
            supports_vision=False,
            last_test_status="untested",
            last_tested_at=None,
            created_at=now,
            updated_at=now,
        )
        session.add(profile)

        # Create agent
        agent = Agent(
            id="ag_custom_test",
            user_id=test_user["id"],
            name="Custom",
            avatar="C",
            description="custom agent",
            system_prompt="prompt",
            adapter_name="mock",
            is_builtin=False,
            is_orchestrator=False,
            created_at=now,
        )
        agent.capabilities_list = []
        agent.tool_names_list = []
        agent.skill_names_list = []
        agent.mcp_server_ids_list = []
        agent.hook_names_list = []
        agent.custom_args_list = []
        session.add(agent)

        # Create conversation + workspace
        conv_id = new_conversation_id()
        ws_id = new_workspace_id()
        conv = Conversation(
            id=conv_id,
            user_id=test_user["id"],
            title="Test Conv",
            mode="single",
            agent_ids=[agent.id],
            created_at=now,
            updated_at=now,
        )
        conv.dispatch_mode = "solo"
        ws = Workspace(
            id=ws_id,
            conversation_id=conv_id,
            mode="sandbox",
            root_path="/tmp/test_ws",
            bound_path=None,
            created_at=now,
        )
        session.add(conv)
        session.add(ws)

    # Mock the conversation service to capture model_profile_id
    captured_args: dict = {}

    async def _mock_send_message(**kwargs):
        captured_args.update(kwargs)
        from types import SimpleNamespace
        return SimpleNamespace(
            message_id="msg_result",
            run_ids=["run_1"],
            messages=[],
            deploy=None,
        )

    with patch("app.api.conversations.conversation_service.send_message", _mock_send_message):
        resp = await api_client.post(
            f"/api/conversations/{conv_id}/messages",
            json={
                "content": "hello",
                "modelProfileId": "mp_test_send",
            },
        )

    assert resp.status_code == 202
    assert captured_args.get("model_profile_id") == "mp_test_send"


async def test_send_message_without_model_profile_id(api_client, db, test_user):
    """send_message should work without modelProfileId (defaults to None)."""
    from app.db.engine import get_db
    from app.db.models import Agent, Conversation, Workspace
    from app.utils.clock import now_ms
    from app.utils.ids import new_conversation_id, new_workspace_id

    now = now_ms()
    conv_id = new_conversation_id()
    ws_id = new_workspace_id()

    async with get_db() as session:
        agent = Agent(
            id="ag_mock_test2",
            user_id=test_user["id"],
            name="Mock",
            avatar="M",
            description="mock agent",
            system_prompt="prompt",
            adapter_name="mock",
            is_builtin=False,
            is_orchestrator=False,
            created_at=now,
        )
        agent.capabilities_list = []
        agent.tool_names_list = []
        agent.skill_names_list = []
        agent.mcp_server_ids_list = []
        agent.hook_names_list = []
        agent.custom_args_list = []
        session.add(agent)

        conv = Conversation(
            id=conv_id,
            user_id=test_user["id"],
            title="Test Conv 2",
            mode="single",
            agent_ids=[agent.id],
            created_at=now,
            updated_at=now,
        )
        conv.dispatch_mode = "solo"
        ws = Workspace(
            id=ws_id,
            conversation_id=conv_id,
            mode="sandbox",
            root_path="/tmp/test_ws2",
            bound_path=None,
            created_at=now,
        )
        session.add(conv)
        session.add(ws)

    captured_args: dict = {}

    async def _mock_send_message(**kwargs):
        captured_args.update(kwargs)
        from types import SimpleNamespace
        return SimpleNamespace(
            message_id="msg_result2",
            run_ids=["run_2"],
            messages=[],
            deploy=None,
        )

    with patch("app.api.conversations.conversation_service.send_message", _mock_send_message):
        resp = await api_client.post(
            f"/api/conversations/{conv_id}/messages",
            json={"content": "hello"},
        )

    assert resp.status_code == 202
    assert captured_args.get("model_profile_id") is None


# ─── 12.9: old Agent model config migrates to ModelProfiles ───────────────────


async def test_migrate_agent_model_profiles(db, test_user, monkeypatch):
    """_migrate_agent_model_profiles should create profiles from old agent model config."""
    from sqlalchemy import text

    from app.db.engine import get_db
    from app.db.models import ModelProfile
    from app.utils.clock import now_ms

    now = now_ms()

    # Simulate pre-migration: agents with baked-in model config columns.
    # We use raw SQL because the ORM no longer has these columns.
    async with get_db() as session:
        # Add model columns back temporarily for the test
        for stmt in [
            "ALTER TABLE agents ADD COLUMN model_provider VARCHAR",
            "ALTER TABLE agents ADD COLUMN model_id VARCHAR",
            "ALTER TABLE agents ADD COLUMN api_key VARCHAR",
            "ALTER TABLE agents ADD COLUMN api_base_url VARCHAR",
            "ALTER TABLE agents ADD COLUMN supports_vision BOOLEAN",
        ]:
            with contextlib.suppress(Exception):
                await session.execute(text(stmt))

        # Insert agent with baked-in model config
        await session.execute(text(
            "INSERT INTO agents (id, user_id, name, avatar, description, "
            "system_prompt, adapter_name, is_builtin, is_orchestrator, is_guide, "
            "memory_enabled, created_at, capabilities, tool_names, skill_names, "
            "hook_names, mcp_server_ids, custom_args, "
            "model_provider, model_id, api_key, api_base_url, supports_vision) "
            "VALUES ("  # noqa: E501
            "'ag_legacy_model', :uid, 'Legacy', 'L', 'legacy agent', "
            "'prompt', 'custom', 0, 0, 0, 0, :now, '[]', '[]', '[]', '[]', '[]', '[]', "
            "'deepseek', 'deepseek-chat', 'sk-legacy-key', 'https://api.deepseek.com/v1', 0)"
        ), {"uid": test_user["id"], "now": now})

        # Insert a second agent with same model config (dedup test)
        await session.execute(text(
            "INSERT INTO agents (id, user_id, name, avatar, description, "
            "system_prompt, adapter_name, is_builtin, is_orchestrator, is_guide, "
            "memory_enabled, created_at, capabilities, tool_names, skill_names, "
            "hook_names, mcp_server_ids, custom_args, "
            "model_provider, model_id, api_key, api_base_url, supports_vision) "
            "VALUES ("  # noqa: E501
            "'ag_legacy_dup', :uid, 'Dup', 'D', 'dup agent', "
            "'prompt', 'custom', 0, 0, 0, 0, :now, '[]', '[]', '[]', '[]', '[]', '[]', "
            "'deepseek', 'deepseek-chat', 'sk-legacy-key', 'https://api.deepseek.com/v1', 0)"
        ), {"uid": test_user["id"], "now": now})

        # Insert builtin agent (user_id IS NULL) — should be skipped
        await session.execute(text(
            "INSERT INTO agents (id, user_id, name, avatar, description, "
            "system_prompt, adapter_name, is_builtin, is_orchestrator, is_guide, "
            "memory_enabled, created_at, capabilities, tool_names, skill_names, "
            "hook_names, mcp_server_ids, custom_args, "
            "model_provider, model_id, api_key, api_base_url, supports_vision) "
            "VALUES ("  # noqa: E501
            "'ag_builtin_model', NULL, 'Builtin', 'B', 'builtin agent', "
            "'prompt', 'mock', 1, 0, 0, 0, :now, '[]', '[]', '[]', '[]', '[]', '[]', "
            "'anthropic', 'claude-opus', 'sk-builtin', 'https://api.anthropic.com', 1)"
        ), {"now": now})

    # Run the migration
    from app.main import _migrate_agent_model_profiles

    await _migrate_agent_model_profiles()

    # Verify profiles were created
    from sqlalchemy import select

    async with get_db() as session:
        profiles = (
            await session.execute(
                select(ModelProfile).where(ModelProfile.user_id == test_user["id"])
            )
        ).scalars().all()

        # Two agents with same config → one deduplicated profile
        assert len(profiles) == 1
        profile = profiles[0]
        assert profile.provider == "deepseek"
        assert profile.model_id == "deepseek-chat"
        assert profile.api_key == "sk-legacy-key"
        assert profile.api_base_url == "https://api.deepseek.com/v1"
        assert profile.is_default is True  # first profile → default

        # Builtin agent (user_id IS NULL) should NOT have a profile
        builtin_profiles = (
            await session.execute(
                select(ModelProfile).where(ModelProfile.user_id.is_(None))
            )
        ).scalars().all()
        assert len(builtin_profiles) == 0
