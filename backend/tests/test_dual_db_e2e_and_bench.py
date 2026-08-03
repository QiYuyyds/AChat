"""E2E and benchmark tests for dual-DB mode.

Task 5.7: End-to-end: full Agent run (from run start to message complete)
Task 5.8: Performance benchmark: single DB vs dual DB per-token latency
Task 5.9: Parallel subtask write lock contention benchmark
"""

import asyncio
import time

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.db.models import Agent, AgentRun, Conversation, Message
from app.schemas.events import (
    MessageEndEvent,
    MessageStartEvent,
    PartStartEvent,
)
from app.services.agent_runner import persist_event
from app.utils.clock import now_ms

# ─── Shared fixtures ──────────────────────────────────────────────────────


async def _seed_dual(dual_db):
    """Seed conversation + agent + run for dual-DB E2E tests."""
    now = now_ms()
    async with dual_db.get_local_db() as session:
        conv = Conversation(
            id="conv_e2e", user_id="u1", title="E2E", mode="single",
            created_at=now, updated_at=now,
        )
        conv.agent_ids_list = []
        conv.pinned_message_ids_list = []
        conv.bookmarked_message_ids_list = []
        session.add(conv)

        agent = Agent(
            id="ag_e2e", name="E2E Agent", avatar="E", description="e2e",
            system_prompt="test", adapter_name="mock", is_builtin=False,
            is_orchestrator=False,
            created_at=now, user_id="u1",
        )
        agent.capabilities_list = []
        agent.tool_names_list = []
        session.add(agent)

        session.add(AgentRun(
            id="run_e2e", conversation_id="conv_e2e", agent_id="ag_e2e",
            status="running", started_at=now,
        ))


async def _seed_single(single_db):
    """Seed conversation + agent + run for single-DB E2E tests."""
    now = now_ms()
    async with single_db.get_local_db() as session:
        conv = Conversation(
            id="conv_e2e", user_id="u1", title="E2E", mode="single",
            created_at=now, updated_at=now,
        )
        conv.agent_ids_list = []
        conv.pinned_message_ids_list = []
        conv.bookmarked_message_ids_list = []
        session.add(conv)

        agent = Agent(
            id="ag_e2e", name="E2E Agent", avatar="E", description="e2e",
            system_prompt="test", adapter_name="mock", is_builtin=False,
            is_orchestrator=False,
            created_at=now, user_id="u1",
        )
        agent.capabilities_list = []
        agent.tool_names_list = []
        session.add(agent)

        session.add(AgentRun(
            id="run_e2e", conversation_id="conv_e2e", agent_id="ag_e2e",
            status="running", started_at=now,
        ))


@pytest_asyncio.fixture
async def dual_db(tmp_path, monkeypatch):
    local_db = tmp_path / "local.db"
    remote_db = tmp_path / "remote.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{remote_db.as_posix()}")
    monkeypatch.setenv("DATABASE_LOCAL_URL", f"sqlite+aiosqlite:///{local_db.as_posix()}")
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "ws"))
    monkeypatch.setenv("JWT_SECRET", "test-secret-at-least-32-characters-long!!")
    from app.config import get_settings
    get_settings.cache_clear()
    from app.db import engine as engine_mod
    await engine_mod.init_db()
    try:
        yield engine_mod
    finally:
        await engine_mod.close_db()
        get_settings.cache_clear()


@pytest_asyncio.fixture
async def single_db(tmp_path, monkeypatch):
    db_file = tmp_path / "single.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_file.as_posix()}")
    monkeypatch.delenv("DATABASE_LOCAL_URL", raising=False)
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "ws"))
    monkeypatch.setenv("JWT_SECRET", "test-secret-at-least-32-characters-long!!")
    from app.config import get_settings
    get_settings.cache_clear()
    from app.db import engine as engine_mod
    await engine_mod.init_db()
    try:
        yield engine_mod
    finally:
        await engine_mod.close_db()
        get_settings.cache_clear()


# ─── Task 5.7: End-to-end agent run ───────────────────────────────────────


@pytest.mark.asyncio
async def test_e2e_full_agent_run_dual_db(dual_db):
    """Full agent run lifecycle in dual-DB mode.

    Steps: run start → message.start → part.start → message.end → verify.
    """
    await _seed_dual(dual_db)
    parts_buffer: dict[str, list[dict]] = {}
    now = now_ms()

    # 1. message.start — creates Message in streaming status
    await persist_event(
        MessageStartEvent(
            type="message.start",
            conversationId="conv_e2e",
            messageId="msg_e2e",
            agentId="ag_e2e",
            runId="run_e2e",
            timestamp=now,
        ),
        parts_buffer, "run_e2e", "ag_e2e",
        output_message_ids=[], artifact_ids=[],
    )

    # Verify streaming status
    async with dual_db.get_local_db() as session:
        result = await session.execute(select(Message).where(Message.id == "msg_e2e"))
        msg = result.scalar_one()
        assert msg.status == "streaming"

    # 2. part.start — adds a text part
    await persist_event(
        PartStartEvent(
            type="part.start",
            conversationId="conv_e2e",
            messageId="msg_e2e",
            partIndex=0,
            part={"type": "text", "content": ""},
            timestamp=now_ms(),
        ),
        parts_buffer, "run_e2e", "ag_e2e",
        output_message_ids=[], artifact_ids=[],
    )

    # Simulate content accumulation (part.delta would update parts_buffer)
    parts_buffer["msg_e2e"][0]["content"] = "Hello from E2E!"

    # 3. message.end — finalizes message with complete status
    await persist_event(
        MessageEndEvent(
            type="message.end",
            conversationId="conv_e2e",
            messageId="msg_e2e",
            agentId="ag_e2e",
            timestamp=now_ms(),
        ),
        parts_buffer, "run_e2e", "ag_e2e",
        output_message_ids=[], artifact_ids=[],
    )

    # Verify final state
    async with dual_db.get_local_db() as session:
        result = await session.execute(select(Message).where(Message.id == "msg_e2e"))
        msg = result.scalar_one()
        assert msg.status == "complete"
        assert len(msg.parts) == 1
        assert msg.parts[0]["content"] == "Hello from E2E!"

    # Verify messages table doesn't exist on the remote engine
    from sqlalchemy import inspect as sa_inspect
    async with dual_db._remote_engine.connect() as conn:
        tables = await conn.run_sync(
            lambda sync_conn: set(sa_inspect(sync_conn).get_table_names())
        )
    assert "messages" not in tables


# ─── Task 5.8: Performance benchmark ──────────────────────────────────────


@pytest.mark.asyncio
async def test_benchmark_dual_db(dual_db):
    """Benchmark: per-message write latency in dual-DB mode.

    Writes N messages and measures total time. Verifies no excessive overhead
    from the dual-engine routing logic (both engines are SQLite in tests).
    """
    N = 20
    parts_buffer: dict[str, list[dict]] = {}
    now = now_ms()

    await _seed_dual(dual_db)
    t0 = time.perf_counter()
    for i in range(N):
        await persist_event(
            MessageStartEvent(
                type="message.start",
                conversationId="conv_e2e",
                messageId=f"msg_dual_{i}",
                agentId="ag_e2e",
                runId="run_e2e",
                timestamp=now + i,
            ),
            parts_buffer, "run_e2e", "ag_e2e",
            output_message_ids=[], artifact_ids=[],
        )
    dual_time = time.perf_counter() - t0

    # Verify all messages persisted to local DB
    async with dual_db.get_local_db() as session:
        for i in range(N):
            result = await session.execute(select(Message).where(Message.id == f"msg_dual_{i}"))
            assert result.scalar_one() is not None

    # Sanity check: 20 writes should complete in under 5 seconds
    assert dual_time < 5.0, f"Dual-DB writes took {dual_time:.3f}s for {N} messages"


@pytest.mark.asyncio
async def test_benchmark_single_db(single_db):
    """Benchmark: per-message write latency in single-DB mode."""
    N = 20
    parts_buffer: dict[str, list[dict]] = {}
    now = now_ms()

    await _seed_single(single_db)
    t0 = time.perf_counter()
    for i in range(N):
        await persist_event(
            MessageStartEvent(
                type="message.start",
                conversationId="conv_e2e",
                messageId=f"msg_single_{i}",
                agentId="ag_e2e",
                runId="run_e2e",
                timestamp=now + i,
            ),
            parts_buffer, "run_e2e", "ag_e2e",
            output_message_ids=[], artifact_ids=[],
        )
    single_time = time.perf_counter() - t0

    # Verify all messages persisted
    async with single_db.get_remote_db() as session:
        for i in range(N):
            result = await session.execute(select(Message).where(Message.id == f"msg_single_{i}"))
            assert result.scalar_one() is not None

    # Sanity check: 20 writes should complete in under 5 seconds
    assert single_time < 5.0, f"Single-DB writes took {single_time:.3f}s for {N} messages"


# ─── Task 5.9: Parallel subtask write lock contention ─────────────────────


@pytest.mark.asyncio
async def test_parallel_writes_no_lock_timeout(dual_db):
    """Multiple parallel writes to SQLite do not time out.

    Simulates an orchestrator dispatching N parallel subtasks that each
    write a message to the local SQLite. Verifies busy_timeout=5000ms
    prevents write lock contention errors.
    """
    await _seed_dual(dual_db)
    now = now_ms()

    async def _write_message(idx: int) -> str:
        """Write a single message.start event and return the message ID."""
        msg_id = f"msg_parallel_{idx}"
        await persist_event(
            MessageStartEvent(
                type="message.start",
                conversationId="conv_e2e",
                messageId=msg_id,
                agentId="ag_e2e",
                runId="run_e2e",
                timestamp=now + idx,
            ),
            {}, "run_e2e", "ag_e2e",
            output_message_ids=[], artifact_ids=[],
        )
        return msg_id

    N = 5
    results = await asyncio.gather(*[_write_message(i) for i in range(N)])

    # All writes should succeed (no SQLite "database is locked" errors)
    assert len(results) == N

    # Verify all messages exist
    async with dual_db.get_local_db() as session:
        for msg_id in results:
            result = await session.execute(select(Message).where(Message.id == msg_id))
            assert result.scalar_one() is not None


@pytest.mark.asyncio
async def test_parallel_writes_with_content(dual_db):
    """Parallel writes with message.end (status update) don't conflict."""
    await _seed_dual(dual_db)
    now = now_ms()

    async def _full_message_cycle(idx: int) -> str:
        """Full cycle: message.start → message.end for one message."""
        msg_id = f"msg_cycle_{idx}"
        parts_buffer: dict[str, list[dict]] = {}

        await persist_event(
            MessageStartEvent(
                type="message.start",
                conversationId="conv_e2e",
                messageId=msg_id,
                agentId="ag_e2e",
                runId="run_e2e",
                timestamp=now,
            ),
            parts_buffer, "run_e2e", "ag_e2e",
            output_message_ids=[], artifact_ids=[],
        )

        parts_buffer[msg_id] = [{"type": "text", "content": f"Output {idx}"}]

        await persist_event(
            MessageEndEvent(
                type="message.end",
                conversationId="conv_e2e",
                messageId=msg_id,
                agentId="ag_e2e",
                timestamp=now_ms(),
            ),
            parts_buffer, "run_e2e", "ag_e2e",
            output_message_ids=[], artifact_ids=[],
        )
        return msg_id

    N = 3
    results = await asyncio.gather(*[_full_message_cycle(i) for i in range(N)])

    # Verify all messages are complete
    async with dual_db.get_local_db() as session:
        for msg_id in results:
            result = await session.execute(select(Message).where(Message.id == msg_id))
            msg = result.scalar_one()
            assert msg.status == "complete"
            assert len(msg.parts) == 1
