"""End-to-end test for the SIMPLE-run path of agent_runner (phase 5 Core-A).

Drives a real run through a MOCK adapter: create a conversation + workspace +
mock agent, kick off runner.run(...), await the spawned task, then assert the
agent_runs row finished, an agent message was persisted with text parts, and
StreamEvents were published on the bus.
"""

import asyncio

import pytest_asyncio
from sqlalchemy import select


@pytest_asyncio.fixture
async def simple_setup(db, agents, tmp_path):
    """Create a conversation, sandbox workspace, and a trigger user message."""
    from app.db.engine import get_db
    from app.db.models import Conversation, Message, Workspace
    from app.utils.clock import now_ms
    from app.utils.ids import new_conversation_id, new_message_id, new_workspace_id

    ws_root = tmp_path / "ws"
    ws_root.mkdir(parents=True, exist_ok=True)
    conv_id = new_conversation_id()
    msg_id = new_message_id()
    now = now_ms()

    async with get_db() as session:
        conv = Conversation(
            id=conv_id,
            title="T",
            mode="single",
            archived=False,
            fs_write_approval_mode="auto",
            created_at=now,
            updated_at=now,
        )
        conv.agent_ids_list = [agents["alice"]]
        conv.pinned_message_ids_list = []
        conv.bookmarked_message_ids_list = []
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
        trigger = Message(
            id=msg_id,
            conversation_id=conv_id,
            role="user",
            agent_id=None,
            status="complete",
            run_id=None,
            created_at=now,
        )
        trigger.parts_list = [{"type": "text", "content": "你好"}]
        trigger.mentioned_agent_ids_list = []
        session.add(trigger)

    return {
        "conversation_id": conv_id,
        "agent_id": agents["alice"],
        "trigger_message_id": msg_id,
    }


async def _await_run(run_id: str) -> None:
    """Await the spawned run task to completion (it self-removes when done)."""
    from app.services import agent_runner as ar

    entry = ar._active_runs.get(run_id)
    if entry is not None:
        await entry[0]


async def test_simple_run_end_to_end(simple_setup):
    from app.db.engine import get_db
    from app.db.models import AgentRun, Message
    from app.services.agent_runner import AgentRunnerImpl
    from app.services.event_bus import event_bus

    collected = []

    async def _drain(queue):
        try:
            while True:
                collected.append(await asyncio.wait_for(queue.get(), timeout=2.0))
        except TimeoutError:
            return

    async with event_bus.subscribe() as queue:
        drainer = asyncio.create_task(_drain(queue))

        runner = AgentRunnerImpl()
        handle = runner.run(
            agent_id=simple_setup["agent_id"],
            conversation_id=simple_setup["conversation_id"],
            trigger_message_id=simple_setup["trigger_message_id"],
        )
        await _await_run(handle.run_id)
        # let the final run.end event land in the queue before stopping the drainer
        await asyncio.sleep(0.05)
        await drainer

    # (a) the agent_runs row finished 'complete'
    async with get_db() as session:
        run = (
            await session.execute(select(AgentRun).where(AgentRun.id == handle.run_id))
        ).scalar_one()
        assert run.status == "complete"
        assert run.finished_at is not None

        # (b) an agent message was persisted with text parts
        agent_msgs = (
            await session.execute(
                select(Message).where(
                    Message.conversation_id == simple_setup["conversation_id"],
                    Message.role == "agent",
                )
            )
        ).scalars().all()
    assert len(agent_msgs) >= 1
    msg = agent_msgs[0]
    assert msg.status == "complete"
    assert msg.run_id == handle.run_id
    assert any(p.get("type") == "text" for p in msg.parts_list)

    # (c) events were published on the bus
    event_types = {getattr(e, "type", None) for e in collected}
    assert "run.start" in event_types
    assert "message.start" in event_types
    assert "run.end" in event_types


async def test_simple_run_missing_workspace_finalizes_failed(simple_setup):
    """A valid agent with no workspace -> finalize 'failed' (run.end + error msg).

    The agent/workspace/trigger preflight runs *before* insert_run, so a failed
    preflight publishes run.end and an error message but writes no agent_runs row
    (faithful to the TS finalizeFailed ordering).
    """
    from sqlalchemy import delete

    from app.db.engine import get_db
    from app.db.models import Message, Workspace
    from app.services.agent_runner import AgentRunnerImpl
    from app.services.event_bus import event_bus

    async with get_db() as session:
        await session.execute(
            delete(Workspace).where(
                Workspace.conversation_id == simple_setup["conversation_id"]
            )
        )

    run_end = []

    async def _drain(queue):
        try:
            while True:
                ev = await asyncio.wait_for(queue.get(), timeout=2.0)
                if getattr(ev, "type", None) == "run.end":
                    run_end.append(ev)
        except TimeoutError:
            return

    async with event_bus.subscribe() as queue:
        drainer = asyncio.create_task(_drain(queue))
        runner = AgentRunnerImpl()
        handle = runner.run(
            agent_id=simple_setup["agent_id"],
            conversation_id=simple_setup["conversation_id"],
            trigger_message_id=simple_setup["trigger_message_id"],
        )
        await _await_run(handle.run_id)
        await asyncio.sleep(0.05)
        await drainer

    assert run_end and run_end[0].status == "failed"
    assert run_end[0].error and "Workspace not found" in run_end[0].error

    # error visualisation: a fresh error message was persisted for this run
    async with get_db() as session:
        err_msgs = (
            await session.execute(
                select(Message).where(
                    Message.run_id == handle.run_id, Message.status == "error"
                )
            )
        ).scalars().all()
    assert len(err_msgs) == 1
    assert any(p.get("type") == "text" for p in err_msgs[0].parts_list)
