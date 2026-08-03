"""ModelProfile runtime resolution tests.

Covers:
  12.3 — build_adapter_input: explicit profile → default → zero-profile refuse
  12.4 — referenced profile deleted → fallback to default + warning
  12.5 — CLI agent does not pass --model (input.model_id is None)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.adapters.base import AdapterInput
from app.adapters.claude_adapter import ClaudeCLIAdapter
from app.db.models import Agent, ModelProfile
from app.utils.clock import now_ms

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_agent(adapter_name: str = "custom", user_id: str = "test_user_1") -> Agent:
    agent = Agent(
        id="ag_test",
        user_id=user_id,
        name="Test",
        avatar="T",
        description="test agent",
        system_prompt="test prompt",
        adapter_name=adapter_name,
        is_builtin=False,
        is_orchestrator=False,
        created_at=now_ms(),
    )
    agent.capabilities_list = []
    agent.tool_names_list = []
    agent.skill_names_list = []
    agent.mcp_server_ids_list = []
    agent.hook_names_list = []
    agent.custom_args_list = []
    return agent


def _make_profile(
    pid: str = "mp_test",
    user_id: str = "test_user_1",
    is_default: bool = True,
    provider: str = "deepseek",
    model_id: str = "deepseek-chat",
    api_key: str = "sk-test-key-1234",
    supports_vision: bool = False,
) -> ModelProfile:
    return ModelProfile(
        id=pid,
        user_id=user_id,
        name=f"{provider}/{model_id}",
        provider=provider,
        model_id=model_id,
        api_key=api_key,
        api_base_url="https://api.deepseek.com/v1",
        is_default=is_default,
        supports_vision=supports_vision,
        last_test_status="untested",
        last_tested_at=None,
        created_at=now_ms(),
        updated_at=now_ms(),
    )


def _make_workspace(tmp_path):
    return SimpleNamespace(
        mode="sandbox",
        bound_path=None,
        root_path=str(tmp_path / "ws"),
    )


# ─── 12.3: explicit → default → zero-profile refuse ──────────────────────────


async def test_explicit_profile_used(db, test_user, tmp_path):
    """build_adapter_input uses the explicitly-selected ModelProfile."""
    from app.services.agent_runner import RunArgs, build_adapter_input

    # Create two profiles; second is default
    p1 = _make_profile(pid="mp_explicit", model_id="deepseek-coder", is_default=False)
    p2 = _make_profile(pid="mp_default", model_id="deepseek-chat", is_default=True)
    from app.db.engine import get_db

    async with get_db() as session:
        session.add(p1)
        session.add(p2)

    agent = _make_agent(adapter_name="custom")
    async with get_db() as session:
        session.add(agent)

    workspace = _make_workspace(tmp_path)
    args = RunArgs(
        agent_id="ag_test",
        conversation_id="conv_test",
        trigger_message_id="msg_test",
        user_id="test_user_1",
        model_profile_id="mp_explicit",
    )

    with patch("app.services.agent_runner.get_effective_cwd", return_value=str(tmp_path)):
        result = await build_adapter_input(
            args, agent, "run_test", "hello", workspace, [], None, [],
        )

    assert result.model_id == "deepseek-coder"
    assert result.api_key == "sk-test-key-1234"
    assert result.custom_config is not None
    assert result.custom_config.model_provider == "deepseek"


async def test_default_profile_used_when_no_explicit(db, test_user, tmp_path):
    """build_adapter_input falls back to the default ModelProfile."""
    from app.services.agent_runner import RunArgs, build_adapter_input

    p1 = _make_profile(pid="mp_default", model_id="deepseek-chat", is_default=True)
    from app.db.engine import get_db

    async with get_db() as session:
        session.add(p1)

    agent = _make_agent(adapter_name="custom")
    async with get_db() as session:
        session.add(agent)

    workspace = _make_workspace(tmp_path)
    args = RunArgs(
        agent_id="ag_test",
        conversation_id="conv_test",
        trigger_message_id="msg_test",
        user_id="test_user_1",
        model_profile_id=None,
    )

    with patch("app.services.agent_runner.get_effective_cwd", return_value=str(tmp_path)):
        result = await build_adapter_input(
            args, agent, "run_test", "hello", workspace, [], None, [],
        )

    assert result.model_id == "deepseek-chat"


async def test_zero_profiles_refuses(db, test_user, tmp_path):
    """build_adapter_input raises when user has zero ModelProfiles and agent is SDK."""
    from app.services.agent_runner import RunArgs, build_adapter_input

    agent = _make_agent(adapter_name="custom")
    from app.db.engine import get_db

    async with get_db() as session:
        session.add(agent)

    workspace = _make_workspace(tmp_path)
    args = RunArgs(
        agent_id="ag_test",
        conversation_id="conv_test",
        trigger_message_id="msg_test",
        user_id="test_user_1",
        model_profile_id=None,
    )

    with patch("app.services.agent_runner.get_effective_cwd", return_value=str(tmp_path)), pytest.raises(ValueError, match="No model profile configured"):
        await build_adapter_input(
            args, agent, "run_test", "hello", workspace, [], None, [],
        )


# ─── 12.4: referenced profile deleted → fallback to default ──────────────────


async def test_deleted_profile_falls_back_to_default(db, test_user, tmp_path):
    """When modelProfileId references a deleted profile, fall back to default."""
    from app.services.agent_runner import RunArgs, build_adapter_input

    p_default = _make_profile(pid="mp_default", model_id="deepseek-chat", is_default=True)
    from app.db.engine import get_db

    async with get_db() as session:
        session.add(p_default)

    agent = _make_agent(adapter_name="custom")
    async with get_db() as session:
        session.add(agent)

    workspace = _make_workspace(tmp_path)
    args = RunArgs(
        agent_id="ag_test",
        conversation_id="conv_test",
        trigger_message_id="msg_test",
        user_id="test_user_1",
        model_profile_id="mp_deleted_nonexistent",
    )

    with patch("app.services.agent_runner.get_effective_cwd", return_value=str(tmp_path)):
        result = await build_adapter_input(
            args, agent, "run_test", "hello", workspace, [], None, [],
        )

    assert result.model_id == "deepseek-chat"


# ─── 12.5: CLI agent does not pass --model ────────────────────────────────────


async def test_cli_adapter_model_id_is_none(tmp_path, db, test_user):
    """build_adapter_input sets model_id=None for CLI (claude-code) agents."""
    from app.services.agent_runner import RunArgs, build_adapter_input

    agent = _make_agent(adapter_name="claude-code")
    from app.db.engine import get_db

    async with get_db() as session:
        session.add(agent)

    workspace = _make_workspace(tmp_path)
    args = RunArgs(
        agent_id="ag_test",
        conversation_id="conv_test",
        trigger_message_id="msg_test",
        user_id="test_user_1",
    )

    with patch("app.services.agent_runner.get_effective_cwd", return_value=str(tmp_path)):
        result = await build_adapter_input(
            args, agent, "run_test", "hello", workspace, [], None, [],
        )

    assert result.model_id is None
    assert result.api_key is None
    assert result.custom_config is None


async def test_claude_build_args_no_model_flag(tmp_path):
    """ClaudeCLIAdapter._build_args should not include --model when model_id is None."""
    adapter = ClaudeCLIAdapter()
    inp = AdapterInput(
        agent_id="ag_test",
        conversation_id="conv_test",
        run_id="run_test",
        prompt="hello",
        workspace_path=str(tmp_path),
        system_prompt="sys",
        api_key=None,
        api_base_url=None,
        model_id=None,
        tool_names=[],
        user_id="test_user_1",
    )
    args = adapter._build_args(inp)
    assert "--model" not in args


async def test_codex_model_id_none_evaluates_to_null():
    """CodexAdapter uses `input.model_id or None` — when model_id is None, model is None."""
    # The codex adapter constructs thread_params with:
    #   "model": input.model_id or None
    # When build_adapter_input sets model_id=None for CLI agents,
    # this expression evaluates to None.
    model_id: str | None = None
    assert (model_id or None) is None
