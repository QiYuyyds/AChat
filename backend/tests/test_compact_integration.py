"""Integration tests for auto-compaction and manual compaction (Tasks 5.1, 5.2).

Task 5.1: >10 complete messages → auto-compact hook → new ContextSummary,
          no role=system "已压缩" message.
Task 5.2: Manual /compact path (silent=False) → role=system announcement +
          MessageAddedEvent broadcast.
"""

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.db.engine import get_db
from app.db.models import Agent, ContextSummary, Conversation, Message
from app.schemas.events import MessageAddedEvent
from app.services import context_compaction_service
from app.services.agent_runner import _maybe_auto_compact_hook
from app.services.context_compaction_service import compact_conversation
from app.services.event_bus import event_bus
from app.utils.clock import now_ms

# Long enough text to exceed MIN_COMPACT_TOKENS (800 tokens ≈ 3200 chars)
_LONG_TEXT = "这是一段用于测试自动压缩的对话内容。" * 200  # ~5200 chars → ~1300 tokens


async def _seed_setup(db) -> tuple[str, str]:
    """Seed a model-backed custom agent + conversation; return (agent_id, conv_id)."""
    now = now_ms()
    agent_id = "ag_integration"
    conv_id = "conv_integration"
    async with get_db() as session:
        agent = Agent(
            id=agent_id,
            name="Integration",
            avatar="I",
            description="integration test agent",
            system_prompt="test system prompt",
            adapter_name="custom",
            model_provider="openai",
            model_id="gpt-4o-mini",
            is_builtin=False,
            is_orchestrator=False,
            supports_vision=False,
            created_at=now,
        )
        agent.capabilities_list = []
        agent.tool_names_list = []

        conv = Conversation(
            id=conv_id,
            title="integration test",
            mode="single",
            archived=False,
            fs_write_approval_mode="review",
            created_at=now,
            updated_at=now,
        )
        conv.agent_ids_list = [agent_id]
        conv.pinned_message_ids_list = []
        session.add(agent)
        session.add(conv)
    return agent_id, conv_id


async def _add_messages(conv_id: str, count: int) -> None:
    """Add ``count`` complete messages with long text content."""
    base = now_ms()
    for i in range(count):
        async with get_db() as session:
            m = Message(
                id=f"msg_int_{i}",
                conversation_id=conv_id,
                role="user" if i % 2 == 0 else "agent",
                agent_id="ag_integration" if i % 2 == 1 else None,
                status="complete",
                created_at=base + i * 100,
            )
            m.parts_list = [{"type": "text", "content": _LONG_TEXT}]
            m.mentioned_agent_ids_list = []
            session.add(m)


@pytest.mark.asyncio
async def test_5_1_auto_compact_produces_summary_no_system_message(db):
    """Task 5.1: >10 complete messages → auto-compact → new ContextSummary,
    no role=system '已压缩' message."""
    agent_id, conv_id = await _seed_setup(db)
    # Add 12 complete messages (above watermark of 10)
    await _add_messages(conv_id, 12)

    # Mock the LLM summariser
    with patch.object(
        context_compaction_service, "_summarise", new_callable=AsyncMock
    ) as mock_summarise:
        mock_summarise.return_value = "自动压缩生成的摘要内容。"
        # Spy on event_bus to ensure no MessageAddedEvent
        with patch.object(event_bus, "publish") as mock_publish:
            # Call the auto-compact hook directly (simulating post-run)
            await _maybe_auto_compact_hook(conv_id, override_prompt=None)

    # Assert: new ContextSummary row exists
    async with get_db() as session:
        summaries = (
            await session.execute(
                select(ContextSummary).where(
                    ContextSummary.conversation_id == conv_id
                )
            )
        ).scalars().all()
        assert len(summaries) == 1, "Auto-compact should produce exactly one ContextSummary"
        assert summaries[0].summary == "自动压缩生成的摘要内容。"

    # Assert: no role=system "已压缩" message
    async with get_db() as session:
        sys_msgs = (
            await session.execute(
                select(Message).where(
                    Message.conversation_id == conv_id,
                    Message.role == "system",
                )
            )
        ).scalars().all()
        assert len(sys_msgs) == 0, "No system '已压缩' message should be persisted"

    # Assert: no MessageAddedEvent was broadcast
    for call in mock_publish.call_args_list:
        event = call.args[0] if call.args else None
        assert not isinstance(event, MessageAddedEvent), (
            "MessageAddedEvent should NOT be broadcast during auto-compact"
        )


@pytest.mark.asyncio
async def test_5_2_manual_compact_retains_announcement(db):
    """Task 5.2: Manual /compact path (silent=False) → role=system announcement +
    MessageAddedEvent broadcast."""
    agent_id, conv_id = await _seed_setup(db)
    await _add_messages(conv_id, 12)

    with patch.object(
        context_compaction_service, "_summarise", new_callable=AsyncMock
    ) as mock_summarise:
        mock_summarise.return_value = "手动压缩生成的摘要内容。"
        with patch.object(event_bus, "publish") as mock_publish:
            result = await compact_conversation(conv_id, silent=False)

    # Assert: system message was inserted
    assert result.message is not None
    assert result.message.role == "system"
    assert "已将" in result.message.parts[0]["content"]
    assert "压缩为上下文摘要" in result.message.parts[0]["content"]

    # Assert: MessageAddedEvent was broadcast
    broadcast_events = [
        call.args[0]
        for call in mock_publish.call_args_list
        if call.args and isinstance(call.args[0], MessageAddedEvent)
    ]
    assert len(broadcast_events) >= 1, (
        "MessageAddedEvent should be broadcast during manual compact"
    )

    # Assert: system message persisted in DB
    async with get_db() as session:
        sys_msgs = (
            await session.execute(
                select(Message).where(
                    Message.conversation_id == conv_id,
                    Message.role == "system",
                )
            )
        ).scalars().all()
        assert len(sys_msgs) == 1, "System announcement message should be persisted"

    # Assert: ContextSummary also persisted
    async with get_db() as session:
        summaries = (
            await session.execute(
                select(ContextSummary).where(
                    ContextSummary.conversation_id == conv_id
                )
            )
        ).scalars().all()
        assert len(summaries) == 1
