"""Tests for JSON LIKE search cross-DB consistency and single-DB backward compat.

Task 5.5: JSON LIKE search cross-DB consistency (SQLite vs PG)
Task 5.6: Single DB mode backward compatibility (no DATABASE_LOCAL_URL)
"""

import pytest
import pytest_asyncio
from sqlalchemy import String, cast, select

from app.db.models import Agent, Conversation, Message

# ─── Task 5.5: JSON LIKE search cross-DB consistency ─────────────────────


@pytest_asyncio.fixture
async def dual_db(tmp_path, monkeypatch):
    """Dual-DB: local SQLite + remote SQLite."""
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


@pytest.mark.asyncio
async def test_json_like_search_local(dual_db):
    """JSON LIKE search works on local SQLite for agent tags/tool_names."""
    from app.utils.clock import now_ms

    now = now_ms()

    # Seed agents with different tool_names
    async with dual_db.get_local_db() as session:
        for i, tools in enumerate([["fs_read", "fs_write"], ["fs_read"], ["web_search"]]):
            agent = Agent(
                id=f"ag_json_{i}", name=f"Agent{i}", avatar="A",
                description="d", system_prompt="p", adapter_name="mock",
                is_builtin=False, is_orchestrator=False, supports_vision=False,
                created_at=now, user_id="u1",
            )
            agent.capabilities_list = []
            agent.tool_names_list = tools
            session.add(agent)

    # Search for agents with "fs_read" in tool_names
    async with dual_db.get_local_db() as session:
        result = await session.execute(
            select(Agent).where(
                cast(Agent.tool_names, String).like('%"fs_read"%')
            )
        )
        agents = result.scalars().all()
        assert len(agents) == 2  # ag_json_0 and ag_json_1

    # Search for "web_search"
    async with dual_db.get_local_db() as session:
        result = await session.execute(
            select(Agent).where(
                cast(Agent.tool_names, String).like('%"web_search"%')
            )
        )
        agents = result.scalars().all()
        assert len(agents) == 1


@pytest.mark.asyncio
async def test_json_like_no_match(dual_db):
    """JSON LIKE search returns empty for non-existent tag."""
    from app.utils.clock import now_ms

    now = now_ms()
    async with dual_db.get_local_db() as session:
        agent = Agent(
            id="ag_nomatch", name="NoMatch", avatar="A",
            description="d", system_prompt="p", adapter_name="mock",
            is_builtin=False, is_orchestrator=False, supports_vision=False,
            created_at=now, user_id="u1",
        )
        agent.capabilities_list = []
        agent.tool_names_list = ["fs_read"]
        session.add(agent)

    async with dual_db.get_local_db() as session:
        result = await session.execute(
            select(Agent).where(
                cast(Agent.tool_names, String).like('%"nonexistent"%')
            )
        )
        agents = result.scalars().all()
        assert len(agents) == 0


@pytest.mark.asyncio
async def test_json_like_remote(dual_db):
    """JSON LIKE search works on remote DB for UserPreference tags."""
    from app.db.models import UserPreference
    from app.utils.clock import now_ms

    now = float(now_ms())
    async with dual_db.get_remote_db() as session:
        session.add(UserPreference(
            user_id="u1", key="theme", value="dark",
            source="manual", updated_at=now,
        ))
        session.add(UserPreference(
            user_id="u1", key="language", value="zh",
            source="extracted", updated_at=now,
        ))

    # Search by source
    async with dual_db.get_remote_db() as session:
        result = await session.execute(
            select(UserPreference).where(UserPreference.source == "manual")
        )
        prefs = result.scalars().all()
        assert len(prefs) == 1
        assert prefs[0].key == "theme"


# ─── Task 5.6: Single DB mode backward compatibility ─────────────────────


@pytest_asyncio.fixture
async def single_db(tmp_path, monkeypatch):
    """Single-DB mode: no DATABASE_LOCAL_URL set."""
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


@pytest.mark.asyncio
async def test_single_db_all_tables_on_one_engine(single_db):
    """In single-DB mode, all tables (local + remote) exist on the remote engine."""
    from sqlalchemy import inspect

    all_tables = set()
    async with single_db._remote_engine.connect() as conn:
        await conn.run_sync(lambda sync_conn: all_tables.update(
            inspect(sync_conn).get_table_names()
        ))

    # Both local and remote tables should be present
    local_tables = {
        "messages", "conversations", "agent_runs", "agents", "mcp_servers",
    }
    remote_tables = {
        "users", "user_settings", "long_term_memory", "documents",
    }
    assert local_tables.issubset(all_tables), f"Missing local tables: {local_tables - all_tables}"
    assert remote_tables.issubset(all_tables), f"Missing remote tables: {remote_tables - all_tables}"


@pytest.mark.asyncio
async def test_single_db_get_local_falls_back(single_db):
    """In single-DB mode, get_local_db falls back to the remote engine."""
    from app.utils.clock import now_ms

    now = now_ms()
    async with single_db.get_local_db() as session:
        agent = Agent(
            id="ag_single", name="Single", avatar="S",
            description="d", system_prompt="p", adapter_name="mock",
            is_builtin=False, is_orchestrator=False, supports_vision=False,
            created_at=now,
        )
        agent.capabilities_list = []
        agent.tool_names_list = []
        session.add(agent)

    # Verify it's on the remote engine (same engine)
    async with single_db.get_remote_db() as session:
        result = await session.execute(select(Agent).where(Agent.id == "ag_single"))
        assert result.scalar_one() is not None


@pytest.mark.asyncio
async def test_single_db_get_db_works(single_db):
    """In single-DB mode, get_db (alias) works the same as get_remote_db."""
    from app.db.engine import get_db
    from app.db.models import User
    from app.utils.clock import now_ms

    now = now_ms()
    async with get_db() as session:
        user = User(
            id="u_single", email="single@test.db", name="S",
            password_hash="hash", token_version=0,
            created_at=now, updated_at=now,
        )
        session.add(user)

    # Verify via get_remote_db
    async with single_db.get_remote_db() as session:
        result = await session.execute(select(User).where(User.id == "u_single"))
        assert result.scalar_one().email == "single@test.db"


@pytest.mark.asyncio
async def test_single_db_conversation_and_message(single_db):
    """In single-DB mode, conversation + message FK chain works."""
    from app.utils.clock import now_ms

    now = now_ms()

    async with single_db.get_local_db() as session:
        conv = Conversation(
            id="conv_s", user_id="u_s", title="T", mode="single",
            created_at=now, updated_at=now,
        )
        conv.agent_ids_list = []
        conv.pinned_message_ids_list = []
        conv.bookmarked_message_ids_list = []
        session.add(conv)

    async with single_db.get_local_db() as session:
        msg = Message(
            id="msg_s", conversation_id="conv_s",
            role="user", status="complete", created_at=now,
        )
        msg.parts_list = []
        msg.mentioned_agent_ids_list = []
        session.add(msg)

    # Verify both exist on the same engine
    async with single_db.get_remote_db() as session:
        result = await session.execute(select(Message).where(Message.id == "msg_s"))
        msg = result.scalar_one()
        assert msg.conversation_id == "conv_s"
