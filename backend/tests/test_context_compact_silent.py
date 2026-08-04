"""Unit tests for compact_conversation silent parameter (Task 2.4).

Verifies:
- silent=True: no system message inserted, no MessageAddedEvent broadcast, message=None
- silent=False: retains existing announcement behavior
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.db.engine import get_db
from app.db.models import Agent, ContextSummary, Conversation, Message, ModelProfile
from app.schemas.events import MessageAddedEvent
from app.services import context_compaction_service
from app.services.context_compaction_service import compact_conversation
from app.services.event_bus import event_bus
from app.utils.clock import now_ms

# Long enough text to exceed MIN_COMPACT_TOKENS (800 tokens ≈ 3200 chars)
_LONG_TEXT = "这是一段用于测试压缩的对话内容。" * 200  # ~5200 chars → ~1300 tokens


async def _seed_custom_agent_and_conversation(db) -> tuple[str, str]:
    """Seed a model-backed custom agent + a conversation; return (agent_id, conv_id)."""
    now = now_ms()
    agent_id = "ag_compact_test"
    conv_id = "conv_compact_test"
    async with get_db() as session:
        agent = Agent(
            id=agent_id,
            name="Compacter",
            avatar="C",
            description="test agent",
            system_prompt="test system prompt",
            adapter_name="custom",
            is_builtin=False,
            is_orchestrator=False,
            created_at=now,
        )
        agent.capabilities_list = []
        agent.tool_names_list = []

        profile = ModelProfile(
            id="mp_compact_test",
            name="test-profile",
            provider="openai",
            model_id="gpt-4o-mini",
            api_key="sk-test",
            api_base_url=None,
            is_default=True,
            supports_vision=False,
            last_test_status="untested",
            last_tested_at=None,
            created_at=now,
            updated_at=now,
        )

        conv = Conversation(
            id=conv_id,
            title="compact silent test",
            mode="single",
            archived=False,
            fs_write_approval_mode="review",
            created_at=now,
            updated_at=now,
        )
        conv.agent_ids_list = [agent_id]
        conv.pinned_message_ids_list = []
        session.add(agent)
        session.add(profile)
        session.add(conv)
    return agent_id, conv_id


async def _add_messages(conv_id: str, count: int) -> None:
    """Add ``count`` complete messages with long text content."""
    base = now_ms()
    for i in range(count):
        async with get_db() as session:
            m = Message(
                id=f"msg_cpt_{i}",
                conversation_id=conv_id,
                role="user" if i % 2 == 0 else "agent",
                agent_id="ag_compact_test" if i % 2 == 1 else None,
                status="complete",
                created_at=base + i * 100,
            )
            m.parts_list = [{"type": "text", "content": _LONG_TEXT}]
            m.mentioned_agent_ids_list = []
            session.add(m)


@pytest.mark.asyncio
async def test_silent_true_no_system_message_no_broadcast(db):
    """silent=True: no system message, no event broadcast, message=None."""
    agent_id, conv_id = await _seed_custom_agent_and_conversation(db)
    # Need > KEEP_RECENT_MESSAGES(6) + MIN_COMPACTABLE(2) = 8 messages with enough tokens
    await _add_messages(conv_id, 10)

    # Mock the LLM summariser to return a fixed summary
    with patch.object(
        context_compaction_service, "_summarise", new_callable=AsyncMock
    ) as mock_summarise:
        mock_summarise.return_value = "这是静默压缩生成的摘要。"
        # Spy on event_bus.publish
        with patch.object(event_bus, "publish") as mock_publish:
            result = await compact_conversation(conv_id, silent=True)

    # Assert: message is None
    assert result.message is None
    # Assert: summary is returned
    assert result.summary is not None
    assert result.summary.summary == "这是静默压缩生成的摘要。"
    # Assert: ctx_before / ctx_after are returned
    assert result.ctx_before > 0
    assert result.ctx_after > 0
    # Assert: no MessageAddedEvent was broadcast
    for call in mock_publish.call_args_list:
        event = call.args[0] if call.args else call.kwargs.get("event")
        assert not isinstance(event, MessageAddedEvent), (
            "MessageAddedEvent should NOT be broadcast in silent mode"
        )

    # Assert: no role=system message was persisted in DB
    from sqlalchemy import select

    async with get_db() as session:
        sys_msgs = (
            await session.execute(
                select(Message).where(
                    Message.conversation_id == conv_id,
                    Message.role == "system",
                )
            )
        ).scalars().all()
        assert len(sys_msgs) == 0, "No system message should be persisted in silent mode"

    # Assert: ContextSummary was persisted
    async with get_db() as session:
        summaries = (
            await session.execute(
                select(ContextSummary).where(
                    ContextSummary.conversation_id == conv_id
                )
            )
        ).scalars().all()
        assert len(summaries) == 1


@pytest.mark.asyncio
async def test_silent_false_retains_announcement(db):
    """silent=False (default): system message inserted and event broadcast."""
    agent_id, conv_id = await _seed_custom_agent_and_conversation(db)
    await _add_messages(conv_id, 10)

    with patch.object(
        context_compaction_service, "_summarise", new_callable=AsyncMock
    ) as mock_summarise:
        mock_summarise.return_value = "这是非静默压缩生成的摘要。"
        with patch.object(event_bus, "publish") as mock_publish:
            result = await compact_conversation(conv_id, silent=False)

    # Assert: message is not None
    assert result.message is not None
    assert result.message.role == "system"
    # Assert: MessageAddedEvent was broadcast
    broadcast_events = [
        call.args[0]
        for call in mock_publish.call_args_list
        if call.args and isinstance(call.args[0], MessageAddedEvent)
    ]
    assert len(broadcast_events) >= 1, "MessageAddedEvent should be broadcast in non-silent mode"

    # Assert: role=system message was persisted in DB
    from sqlalchemy import select

    async with get_db() as session:
        sys_msgs = (
            await session.execute(
                select(Message).where(
                    Message.conversation_id == conv_id,
                    Message.role == "system",
                )
            )
        ).scalars().all()
        assert len(sys_msgs) == 1, "System message should be persisted in non-silent mode"


@pytest.mark.asyncio
async def test_silent_default_is_false(db):
    """Default call (no silent kwarg) retains announcement behavior."""
    agent_id, conv_id = await _seed_custom_agent_and_conversation(db)
    await _add_messages(conv_id, 10)

    with patch.object(
        context_compaction_service, "_summarise", new_callable=AsyncMock
    ) as mock_summarise:
        mock_summarise.return_value = "默认压缩摘要。"
        with patch.object(event_bus, "publish") as mock_publish:
            result = await compact_conversation(conv_id)

    # Default behavior = non-silent = has message
    assert result.message is not None
    assert result.message.role == "system"
