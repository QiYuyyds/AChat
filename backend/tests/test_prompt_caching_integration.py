"""Integration tests for prompt caching architecture.

Covers:
- Task 9.4: agent_runner injects static to system prompt and dynamic to user message
- Task 9.5: _summarise() receives and uses parent_system_prompt and does not pass tools
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio


# ─── Task 9.4: agent_runner static/dynamic injection ────────────────────────


@pytest_asyncio.fixture
async def custom_agent_setup(db, tmp_path):
    """Create a custom agent, conversation, and workspace for build_adapter_input tests."""
    from app.db.engine import get_db
    from app.db.models import Agent, Conversation, Workspace
    from app.utils.clock import now_ms
    from app.utils.ids import new_conversation_id, new_workspace_id

    ws_root = tmp_path / "ws"
    ws_root.mkdir(parents=True, exist_ok=True)
    conv_id = new_conversation_id()
    now = now_ms()

    async with get_db() as session:
        agent = Agent(
            id="ag_custom_test",
            name="CustomAgent",
            avatar="C",
            description="test custom agent",
            system_prompt="You are a test agent.",
            adapter_name="custom",
            is_builtin=False,
            is_orchestrator=False,
            supports_vision=False,
            model_provider="openai",
            model_id="gpt-4",
            created_at=now,
        )
        agent.capabilities_list = []
        agent.tool_names_list = []

        conv = Conversation(
            id=conv_id,
            title="Test",
            mode="single",
            archived=False,
            fs_write_approval_mode="auto",
            created_at=now,
            updated_at=now,
        )
        conv.agent_ids_list = [agent.id]
        conv.pinned_message_ids_list = []
        conv.bookmarked_message_ids_list = []

        session.add(agent)
        session.add(conv)
        session.add(
            Workspace(
                id=new_workspace_id(),
                conversation_id=conv_id,
                root_path=str(ws_root),
                mode="sandbox",
                bound_path=None,
                created_at=now,
            )
        )

    return {
        "agent_id": "ag_custom_test",
        "conversation_id": conv_id,
        "workspace_root": str(ws_root),
    }


async def test_build_adapter_input_static_to_system_dynamic_to_prompt(db, custom_agent_setup, monkeypatch):
    """build_adapter_input injects render_static() to system prompt and
    render_dynamic() to user prompt (Task 9.4)."""
    from app.services.prompt_assembler import (
        CHAT_SCHEMA, REACT_SCHEMA, ContextItem, FilledSlot,
        RuntimeContext, SlotConstraints, SlotPlanner,
    )
    from app.services import agent_runner

    # Build a RuntimeContext with known static and dynamic content
    test_ctx = RuntimeContext(
        schema=REACT_SCHEMA,
        filled=[
            FilledSlot(kind=SlotConstraints, items=[ContextItem(text="STATIC_RULE_CONTENT")]),
            FilledSlot(kind=SlotPlanner, items=[ContextItem(text="DYNAMIC_PLANNER_CONTENT")]),
        ],
    )

    # Mock assembler that returns our test context
    mock_assembler = MagicMock()
    mock_assembler.assemble = AsyncMock(return_value=test_ctx)

    # Monkeypatch _get_prompt_assembler to return our mock
    monkeypatch.setattr(agent_runner, "_get_prompt_assembler", lambda: mock_assembler)

    # Load the agent from DB
    from app.db.engine import get_db
    from app.db.models import Agent, Workspace
    from app.services.agent_runner import RunArgs, build_adapter_input

    async with get_db() as session:
        agent = await session.get(Agent, custom_agent_setup["agent_id"])
        workspace = (
            await session.execute(
                __import__("sqlalchemy").select(Workspace).where(
                    Workspace.conversation_id == custom_agent_setup["conversation_id"]
                )
            )
        ).scalars().first()

    args = RunArgs(
        agent_id=custom_agent_setup["agent_id"],
        conversation_id=custom_agent_setup["conversation_id"],
        trigger_message_id="msg_test",
    )

    result = await build_adapter_input(
        args=args,
        agent=agent,
        run_id="run_test",
        prompt="Hello world",
        workspace=workspace,
        tool_names=[],
        system_prompt_override=None,
        attachments=[],
    )

    # Static content goes to system prompt
    assert "STATIC_RULE_CONTENT" in result.system_prompt
    # Dynamic content does NOT go to system prompt
    assert "DYNAMIC_PLANNER_CONTENT" not in result.system_prompt
    # Dynamic content goes to user prompt (wrapped in system-reminder)
    assert "DYNAMIC_PLANNER_CONTENT" in result.prompt
    assert "<system-reminder>" in result.prompt
    # Original prompt is preserved
    assert "Hello world" in result.prompt


async def test_build_adapter_input_empty_dynamic_leaves_prompt_unchanged(db, custom_agent_setup, monkeypatch):
    """When render_dynamic() returns empty, the prompt is unchanged (Task 9.4)."""
    from app.services.prompt_assembler import (
        CHAT_SCHEMA, ContextItem, FilledSlot,
        RuntimeContext, SlotConstraints,
    )
    from app.services import agent_runner

    # Build a RuntimeContext with only static content (no dynamic)
    test_ctx = RuntimeContext(
        schema=CHAT_SCHEMA,
        filled=[
            FilledSlot(kind=SlotConstraints, items=[ContextItem(text="STATIC_ONLY")]),
        ],
    )

    mock_assembler = MagicMock()
    mock_assembler.assemble = AsyncMock(return_value=test_ctx)
    monkeypatch.setattr(agent_runner, "_get_prompt_assembler", lambda: mock_assembler)

    from sqlalchemy import select
    from app.db.engine import get_db
    from app.db.models import Agent, Workspace
    from app.services.agent_runner import RunArgs, build_adapter_input

    async with get_db() as session:
        agent = await session.get(Agent, custom_agent_setup["agent_id"])
        workspace = (
            await session.execute(
                select(Workspace).where(
                    Workspace.conversation_id == custom_agent_setup["conversation_id"]
                )
            )
        ).scalars().first()

    args = RunArgs(
        agent_id=custom_agent_setup["agent_id"],
        conversation_id=custom_agent_setup["conversation_id"],
        trigger_message_id="msg_test",
    )

    result = await build_adapter_input(
        args=args,
        agent=agent,
        run_id="run_test",
        prompt="Hello world",
        workspace=workspace,
        tool_names=[],
        system_prompt_override=None,
        attachments=[],
    )

    # Static content in system prompt
    assert "STATIC_ONLY" in result.system_prompt
    # Prompt is unchanged (no system-reminder wrapping)
    assert result.prompt == "Hello world"
    assert "<system-reminder>" not in result.prompt


# ─── Task 9.5: _summarise() with parent_system_prompt ───────────────────────


class TestSummariseCacheSafe:
    def test_summarise_accepts_parent_system_prompt_param(self):
        """_summarise() signature includes parent_system_prompt parameter."""
        import inspect
        from app.services.context_compaction_service import _summarise

        sig = inspect.signature(_summarise)
        assert "parent_system_prompt" in sig.parameters
        assert sig.parameters["parent_system_prompt"].default == ""

    @pytest.mark.asyncio
    async def test_summarise_uses_parent_system_prompt_in_messages(self):
        """When parent_system_prompt is provided, it's sent as a system message."""
        from app.services.context_compaction_service import _summarise

        # Mock the OpenAI client and config resolver
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Summary result"

        mock_completions = MagicMock()
        mock_completions.create = AsyncMock(return_value=mock_response)

        mock_chat = MagicMock()
        mock_chat.completions = mock_completions

        mock_client = MagicMock()
        mock_client.chat = mock_chat

        with (
            patch("openai.AsyncOpenAI", return_value=mock_client),
            patch(
                "app.adapters.custom_provider_client.resolve_custom_provider_client_config",
                return_value=MagicMock(api_key="sk-test", base_url="http://test"),
            ),
        ):
            result = await _summarise(
                transcript="User: Hello\nAgent: Hi there",
                prior_summary=None,
                model_provider="openai",
                model_id="gpt-4",
                api_key="sk-test",
                api_base_url=None,
                parent_system_prompt="You are a helpful agent.",
            )

        assert result == "Summary result"

        # Verify the create call included a system message with parent_system_prompt
        create_kwargs = mock_completions.create.call_args.kwargs
        messages = create_kwargs["messages"]
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are a helpful agent."
        assert messages[1]["role"] == "user"

    @pytest.mark.asyncio
    async def test_summarise_does_not_pass_tools(self):
        """_summarise() does not pass tools parameter to the LLM call."""
        from app.services.context_compaction_service import _summarise

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Summary"

        mock_completions = MagicMock()
        mock_completions.create = AsyncMock(return_value=mock_response)

        mock_client = MagicMock()
        mock_client.chat.completions = mock_completions

        with (
            patch("openai.AsyncOpenAI", return_value=mock_client),
            patch(
                "app.adapters.custom_provider_client.resolve_custom_provider_client_config",
                return_value=MagicMock(api_key="sk-test", base_url="http://test"),
            ),
        ):
            await _summarise(
                transcript="User: Hello",
                prior_summary=None,
                model_provider="openai",
                model_id="gpt-4",
                api_key="sk-test",
                api_base_url=None,
                parent_system_prompt="System prompt",
            )

        create_kwargs = mock_completions.create.call_args.kwargs
        assert "tools" not in create_kwargs

    @pytest.mark.asyncio
    async def test_summarise_without_parent_system_prompt(self):
        """When parent_system_prompt is empty, no system message is added."""
        from app.services.context_compaction_service import _summarise

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Summary"

        mock_completions = MagicMock()
        mock_completions.create = AsyncMock(return_value=mock_response)

        mock_client = MagicMock()
        mock_client.chat.completions = mock_completions

        with (
            patch("openai.AsyncOpenAI", return_value=mock_client),
            patch(
                "app.adapters.custom_provider_client.resolve_custom_provider_client_config",
                return_value=MagicMock(api_key="sk-test", base_url="http://test"),
            ),
        ):
            await _summarise(
                transcript="User: Hello",
                prior_summary=None,
                model_provider="openai",
                model_id="gpt-4",
                api_key="sk-test",
                api_base_url=None,
                parent_system_prompt="",
            )

        create_kwargs = mock_completions.create.call_args.kwargs
        messages = create_kwargs["messages"]
        # Only a user message, no system message
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
