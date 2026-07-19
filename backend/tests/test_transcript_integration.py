"""Integration tests for transcript renderer in Tier 2/3 compaction.

Verifies:
  - compact_conversation passes a tool-aware transcript to the LLM summariser
  - estimate_uncompacted_tokens counts tool_result parts (not just text)
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.db.engine import get_db
from app.db.models import Agent, Conversation, Message
from app.services import context_compaction_service
from app.services.context_compaction_service import (
    compact_conversation,
    estimate_uncompacted_tokens,
)
from app.utils.clock import now_ms
from app.utils.model_registry import estimate_tokens

# Long enough text to exceed MIN_COMPACT_TOKENS (800 tokens ≈ 3200 chars)
_LONG_TEXT = "这是一段用于测试压缩的对话内容。" * 200  # ~5200 chars → ~1300 tokens


async def _seed_agent_and_conversation(db, user_id: str) -> tuple[str, str]:
    """Seed a model-backed custom agent + a conversation; return (agent_id, conv_id)."""
    now = now_ms()
    agent_id = "ag_transcript_test"
    conv_id = "conv_transcript_test"
    async with get_db() as session:
        agent = Agent(
            id=agent_id,
            name="Compacter",
            avatar="C",
            description="test agent",
            system_prompt="test system prompt",
            adapter_name="custom",
            model_provider="openai",
            model_id="gpt-4o-mini",
            is_builtin=False,
            is_orchestrator=False,
            supports_vision=False,
            created_at=now,
            user_id=user_id,
        )
        agent.capabilities_list = []
        agent.tool_names_list = []

        conv = Conversation(
            id=conv_id,
            title="transcript integration test",
            mode="single",
            archived=False,
            fs_write_approval_mode="review",
            created_at=now,
            updated_at=now,
            user_id=user_id,
        )
        conv.agent_ids_list = [agent_id]
        conv.pinned_message_ids_list = []
        session.add(agent)
        session.add(conv)
    return agent_id, conv_id


async def _add_messages_with_tools(conv_id: str, agent_id: str, count: int) -> None:
    """Add messages with tool_use + tool_result parts."""
    base = now_ms()
    for i in range(count):
        async with get_db() as session:
            if i % 2 == 0:
                # User message
                m = Message(
                    id=f"msg_ti_{i}",
                    conversation_id=conv_id,
                    role="user",
                    agent_id=None,
                    status="complete",
                    created_at=base + i * 100,
                )
                m.parts_list = [{"type": "text", "content": _LONG_TEXT}]
            else:
                # Agent message with tool_use + tool_result
                m = Message(
                    id=f"msg_ti_{i}",
                    conversation_id=conv_id,
                    role="agent",
                    agent_id=agent_id,
                    status="complete",
                    created_at=base + i * 100,
                )
                m.parts_list = [
                    {"type": "text", "content": _LONG_TEXT},
                    {
                        "type": "tool_use",
                        "callId": f"call_{i}",
                        "toolName": "fs_list",
                        "args": {"path": "src", "depth": 3},
                    },
                    {
                        "type": "tool_result",
                        "callId": f"call_{i}",
                        "result": [{"name": f"file{j}.ts", "relativePath": f"src/file{j}.ts", "isDirectory": False} for j in range(10)],
                        "isError": False,
                    },
                ]
            m.mentioned_agent_ids_list = []
            session.add(m)


# ─── compact_conversation: tool-aware transcript ─────────────────────────────


@pytest.mark.asyncio
async def test_compact_transcript_contains_tool_info(db, test_user):
    """compact_conversation passes a tool-aware transcript to _summarise.

    The transcript should contain ↳ tool_use: and ↳ tool_result: lines
    when the conversation includes tool calls.
    """
    agent_id, conv_id = await _seed_agent_and_conversation(db, test_user["id"])
    # Need > KEEP_RECENT_MESSAGES(6) + MIN_COMPACTABLE(2) = 8 messages
    await _add_messages_with_tools(conv_id, agent_id, 10)

    captured_transcript: list[str] = []

    async def mock_summarise(transcript, *args, **kwargs):
        captured_transcript.append(transcript)
        return "这是包含工具信息的摘要。"

    with patch.object(
        context_compaction_service, "_summarise", new_callable=AsyncMock
    ) as mock_summ:
        mock_summ.side_effect = mock_summarise
        with patch.object(context_compaction_service, "event_bus"):
            result = await compact_conversation(conv_id, silent=True)

    assert result.summary is not None
    assert len(captured_transcript) > 0

    transcript = captured_transcript[0]
    assert "↳ tool_use: fs_list" in transcript
    assert "↳ tool_result: [fs_list]" in transcript


# ─── estimate_uncompacted_tokens: includes tool parts ────────────────────────


@pytest.mark.asyncio
async def test_estimate_uncompacted_tokens_includes_tool_parts(db, test_user):
    """estimate_uncompacted_tokens counts tool_result tokens, not just text."""
    agent_id, conv_id = await _seed_agent_and_conversation(db, test_user["id"])

    # Add one message with small text but large tool_result
    now = now_ms()
    large_tool_result = "x" * 20000  # ~5000 tokens
    async with get_db() as session:
        m = Message(
            id="msg_est_1",
            conversation_id=conv_id,
            role="agent",
            agent_id=agent_id,
            status="complete",
            created_at=now,
        )
        m.parts_list = [
            {"type": "text", "content": "small text"},  # ~2 tokens
            {
                "type": "tool_use",
                "callId": "c1",
                "toolName": "fs_read",
                "args": {"path": "big.ts", "mode": "full"},
            },
            {
                "type": "tool_result",
                "callId": "c1",
                "result": large_tool_result,
                "isError": False,
            },
        ]
        m.mentioned_agent_ids_list = []
        session.add(m)

    total = await estimate_uncompacted_tokens(conv_id)

    # Should be ~5000+ tokens (tool_result dominates), way more than text-only (~2)
    assert total > 4000

    # Verify it's significantly more than text-only estimate
    text_only = estimate_tokens("small text")
    assert total > text_only * 100
