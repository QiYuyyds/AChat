"""Tests for event persistence — synchronous DB writes.

判定注记（A 类，stream-events 重构）：Redis Stream 写后路径（deferrable 事件
XADD 到 Stream、DBWriterConsumer 消费者回刷）已从产品代码移除，
``_persist_or_stream`` 现在总是直接写 DB。原"Stream vs DB 路由"用例随之失效，
已删除；保留的用例覆盖现存契约：事件部件直接落库、message.start/end 同步写。
"""

import pytest
from sqlalchemy import select

from app.db.engine import get_db
from app.db.models import Agent, Conversation, Message
from app.schemas.events import (
    MessageEndEvent,
    MessageStartEvent,
    PartStartEvent,
)
from app.services.agent_runner import (
    _persist_or_stream,
    persist_event,
)
from app.utils.clock import now_ms


async def _seed_conversation(conv_id: str = "conv_test") -> None:
    """Seed a conversation so messages can reference it."""
    async with get_db() as session:
        conv = Conversation(
            id=conv_id,
            title="Test",
            mode="single",
            archived=False,
            fs_write_approval_mode="auto",
            created_at=now_ms(),
            updated_at=now_ms(),
        )
        conv.agent_ids_list = []
        conv.pinned_message_ids_list = []
        conv.bookmarked_message_ids_list = []
        session.add(conv)


async def _seed_agent(agent_id: str = "ag_1") -> None:
    """Seed a minimal agent so FK constraints on messages pass."""
    async with get_db() as session:
        agent = Agent(
            id=agent_id,
            name="Test Agent",
            avatar="T",
            description="test",
            system_prompt="test",
            adapter_name="mock",
            is_builtin=False,
            is_orchestrator=False,
            created_at=now_ms(),
        )
        agent.capabilities_list = []
        agent.tool_names_list = []
        session.add(agent)


@pytest.mark.asyncio
async def test_deferrable_event_writes_directly_to_db(db, test_user):
    """Part events write their parts straight to the DB (no Stream buffering)."""
    await _seed_conversation("conv_sync")
    await _seed_agent("ag_1")

    async with get_db() as session:
        msg = Message(
            id="msg_sync_test",
            conversation_id="conv_sync",
            role="agent",
            agent_id="ag_1",
            status="streaming",
            run_id="run_sync",
            created_at=now_ms(),
        )
        msg.parts_list = []
        session.add(msg)

    event = PartStartEvent(
        conversation_id="conv_sync",
        timestamp=now_ms(),
        message_id="msg_sync_test",
        part_index=0,
        part={"type": "text", "content": "sync hello"},
    )

    await _persist_or_stream(None, "run_sync", event, [event.part], False)

    async with get_db() as session:
        result = await session.execute(
            select(Message).where(Message.id == "msg_sync_test")
        )
        msg = result.scalar_one_or_none()
        assert msg is not None
        assert msg.parts_list == [{"type": "text", "content": "sync hello"}]


@pytest.mark.asyncio
async def test_message_start_always_synchronous(db, test_user):
    """message.start always does a synchronous INSERT, with no Stream writes."""
    await _seed_conversation("conv_start")
    await _seed_agent("ag_1")

    event = MessageStartEvent(
        conversation_id="conv_start",
        timestamp=now_ms(),
        message_id="msg_start_test",
        agent_id="ag_1",
        run_id="run_start",
    )

    parts_buffer: dict[str, list[dict]] = {}
    output_message_ids: list[str] = []

    await persist_event(
        event, parts_buffer, "run_start", "ag_1", output_message_ids, [], False
    )

    async with get_db() as session:
        result = await session.execute(
            select(Message).where(Message.id == "msg_start_test")
        )
        msg = result.scalar_one_or_none()
        assert msg is not None
        assert msg.status == "streaming"


@pytest.mark.asyncio
async def test_message_end_always_synchronous(db, test_user):
    """message.end always does a synchronous status UPDATE + final parts flush."""
    await _seed_conversation("conv_end")
    await _seed_agent("ag_1")

    async with get_db() as session:
        msg = Message(
            id="msg_end_test",
            conversation_id="conv_end",
            role="agent",
            agent_id="ag_1",
            status="streaming",
            run_id="run_end",
            created_at=now_ms(),
        )
        msg.parts_list = []
        session.add(msg)

    event = MessageEndEvent(
        conversation_id="conv_end",
        timestamp=now_ms(),
        message_id="msg_end_test",
    )

    parts_buffer: dict[str, list[dict]] = {
        "msg_end_test": [{"type": "text", "content": "final text"}]
    }

    await persist_event(
        event, parts_buffer, "run_end", "ag_1", [], [], False
    )

    async with get_db() as session:
        result = await session.execute(
            select(Message).where(Message.id == "msg_end_test")
        )
        msg = result.scalar_one_or_none()
        assert msg is not None
        assert msg.status == "complete"
        assert msg.parts_list == [{"type": "text", "content": "final text"}]
