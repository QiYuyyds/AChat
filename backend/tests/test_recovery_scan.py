"""Tests for crash recovery scan.

The scan is DB-only (the Redis Stream replay was removed with the dual-DB
migration) and threshold-free: at startup any non-terminal AgentRun row and
any streaming Message row must be leftover from the previous process.
"""

import pytest
from sqlalchemy import select

from app.db.engine import get_db
from app.db.models import AgentRun, Conversation, Message
from app.services.recovery_scan import scan_interrupted_messages
from app.utils.clock import now_ms
from app.utils.ids import new_run_id


async def _seed_conversation(conv_id: str = "conv_stuck") -> None:
    now = now_ms()
    async with get_db() as session:
        conv = Conversation(
            id=conv_id,
            title="Recovery Test",
            mode="single",
            archived=False,
            fs_write_approval_mode="auto",
            created_at=now,
            updated_at=now,
        )
        conv.agent_ids_list = []
        conv.pinned_message_ids_list = []
        conv.bookmarked_message_ids_list = []
        session.add(conv)


async def _seed_streaming_message(
    msg_id: str = "msg_stuck",
    run_id: str = "run_stuck",
    conv_id: str = "conv_stuck",
    age_ms: int = 10 * 60 * 1000,
) -> None:
    """Seed a message with status=streaming (age is irrelevant post-threshold)."""
    old_ts = now_ms() - age_ms
    async with get_db() as session:
        msg = Message(
            id=msg_id,
            conversation_id=conv_id,
            role="agent",
            agent_id=None,
            status="streaming",
            run_id=run_id,
            created_at=old_ts,
        )
        msg.parts_list = [{"type": "text", "content": "partial"}]
        session.add(msg)


async def _seed_agent_run(
    conv_id: str,
    status: str,
    *,
    agent_id: str,
    run_id: str | None = None,
    parent_run_id: str | None = None,
) -> str:
    rid = run_id or new_run_id()
    async with get_db() as session:
        session.add(
            AgentRun(
                id=rid,
                conversation_id=conv_id,
                agent_id=agent_id,
                status=status,
                parent_run_id=parent_run_id,
                started_at=now_ms(),
            )
        )
    return rid


@pytest.mark.asyncio
async def test_no_orphaned_records(db, test_user):
    """When nothing is orphaned, scan returns 0 and does nothing."""
    count = await scan_interrupted_messages()
    assert count == 0


@pytest.mark.asyncio
async def test_streaming_message_marked_interrupted(db, test_user):
    """Streaming messages are marked interrupted (no age threshold)."""
    await _seed_conversation()
    await _seed_streaming_message()

    count = await scan_interrupted_messages()
    assert count == 1

    async with get_db() as session:
        msg = (await session.execute(
            select(Message).where(Message.id == "msg_stuck")
        )).scalar_one_or_none()
        assert msg is not None
        assert msg.status == "interrupted"
        # Persisted partial parts are left untouched.
        assert msg.parts_list == [{"type": "text", "content": "partial"}]


@pytest.mark.asyncio
async def test_fresh_crash_marked_interrupted(db, test_user):
    """A message crashed seconds ago (inside the old 5-min grace window) is
    interrupted too — at startup no message can still be streaming."""
    await _seed_conversation()
    await _seed_streaming_message(msg_id="msg_fresh", age_ms=30 * 1000)

    count = await scan_interrupted_messages()
    assert count == 1

    async with get_db() as session:
        msg = (await session.execute(
            select(Message).where(Message.id == "msg_fresh")
        )).scalar_one()
        assert msg.status == "interrupted"


@pytest.mark.asyncio
async def test_orphaned_runs_marked_interrupted(db, test_user, agents):
    """Non-terminal AgentRun rows (running / queued) become interrupted with
    an error note; terminal rows are left alone."""
    await _seed_conversation()
    agent_id = agents["alice"]
    running_id = await _seed_agent_run("conv_stuck", "running", agent_id=agent_id)
    queued_id = await _seed_agent_run("conv_stuck", "queued", agent_id=agent_id)
    await _seed_agent_run(
        "conv_stuck", "complete", agent_id=agent_id, run_id="run_terminal_done"
    )

    count = await scan_interrupted_messages()
    assert count == 2

    async with get_db() as session:
        running = (await session.execute(
            select(AgentRun).where(AgentRun.id == running_id)
        )).scalar_one()
        assert running.status == "interrupted"
        assert running.error == "interrupted by restart"
        assert running.finished_at is not None

        queued = (await session.execute(
            select(AgentRun).where(AgentRun.id == queued_id)
        )).scalar_one()
        assert queued.status == "interrupted"

        done = (await session.execute(
            select(AgentRun).where(AgentRun.id == "run_terminal_done")
        )).scalar_one()
        assert done.status == "complete"
        assert done.error is None


@pytest.mark.asyncio
async def test_recovery_covers_both_tables(db, test_user, agents):
    """Residual running run + streaming message are both resolved in one scan."""
    await _seed_conversation()
    agent_id = agents["alice"]
    orphan_run_id = await _seed_agent_run("conv_stuck", "running", agent_id=agent_id)
    await _seed_streaming_message()

    count = await scan_interrupted_messages()
    assert count == 2

    async with get_db() as session:
        run = (await session.execute(
            select(AgentRun).where(AgentRun.id == orphan_run_id)
        )).scalar_one()
        assert run.status == "interrupted"
        msg = (await session.execute(
            select(Message).where(Message.id == "msg_stuck")
        )).scalar_one()
        assert msg.status == "interrupted"


@pytest.mark.asyncio
async def test_post_restart_message_not_queued(db, test_user, agents):
    """Integration: after the scan, the has_active_runs gate (same predicate as
    conversation_service) sees no active top-level runs for the conversation —
    a message sent after restart runs immediately instead of being queued."""
    conv_id = "conv_stuck"
    await _seed_conversation(conv_id)
    orphan_parent = await _seed_agent_run(
        conv_id, "running", agent_id=agents["alice"]
    )
    await _seed_agent_run(
        conv_id, "running", agent_id=agents["orch"], parent_run_id=orphan_parent
    )
    await _seed_streaming_message(conv_id=conv_id)

    await scan_interrupted_messages()

    # The exact predicate conversation_service uses to decide queue vs run.
    async with get_db() as session:
        active = (await session.execute(
            select(AgentRun.id).where(
                AgentRun.conversation_id == conv_id,
                AgentRun.status.in_(["running", "queued"]),
                AgentRun.parent_run_id.is_(None),
            )
        )).first()
    assert active is None
