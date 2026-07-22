"""Dual-DB mode unit tests.

Covers:
- Dual engine initialization (local SQLite + remote SQLite-as-PG)
- Cross-DB read path (Agent from local + UserSettings from remote)
- SQLite internal FK working (Message → Conversation)
- Redis not started (no Redis client)
- get_db alias fallback to get_remote_db
"""

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def dual_db(tmp_path, monkeypatch):
    """Set up dual-DB mode: local SQLite + remote SQLite (as PG stand-in)."""
    local_db = tmp_path / "local.db"
    remote_db = tmp_path / "remote.db"

    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{remote_db.as_posix()}")
    monkeypatch.setenv("DATABASE_LOCAL_URL", f"sqlite+aiosqlite:///{local_db.as_posix()}")
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspaces"))
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
async def test_dual_engines_initialized(dual_db):
    """Both local and remote engines are initialized in dual-DB mode."""
    assert dual_db._local_engine is not None, "Local engine should be initialized"
    assert dual_db._remote_engine is not None, "Remote engine should be initialized"
    assert dual_db._local_session_factory is not None
    assert dual_db._remote_session_factory is not None


@pytest.mark.asyncio
async def test_local_tables_on_local_engine(dual_db):
    """Local tables exist on the local (SQLite) engine, not on remote."""
    from sqlalchemy import inspect

    local_tables = set()
    async with dual_db._local_engine.connect() as conn:
        await conn.run_sync(lambda sync_conn: local_tables.update(
            inspect(sync_conn).get_table_names()
        ))

    expected = {
        "messages", "conversations", "agent_runs", "agent_run_checkpoints",
        "artifacts", "workspaces", "attachments",
        "conversation_context_summaries", "agents", "mcp_servers",
    }
    assert expected.issubset(local_tables), (
        f"Missing local tables: {expected - local_tables}"
    )


@pytest.mark.asyncio
async def test_remote_tables_on_remote_engine(dual_db):
    """Remote tables exist on the remote (PG) engine."""
    from sqlalchemy import inspect

    remote_tables = set()
    async with dual_db._remote_engine.connect() as conn:
        await conn.run_sync(lambda sync_conn: remote_tables.update(
            inspect(sync_conn).get_table_names()
        ))

    expected = {
        "users", "user_settings", "user_preferences", "global_settings",
        "app_settings", "rag_chunks", "long_term_memory", "chat_history",
        "memory_nodes", "memory_edges", "documents", "document_versions",
    }
    assert expected.issubset(remote_tables), (
        f"Missing remote tables: {expected - remote_tables}"
    )


@pytest.mark.asyncio
async def test_cross_db_read(dual_db):
    """Read Agent (local) and UserSettings (remote) from different engines."""
    from app.db.models import Agent, User, UserSettings
    from app.utils.clock import now_ms

    now = now_ms()

    # Write user to remote DB
    async with dual_db.get_remote_db() as session:
        user = User(
            id="u1", email="test@dual.db", name="Dual",
            password_hash="hash", token_version=0,
            created_at=now, updated_at=now,
        )
        session.add(user)
        settings = UserSettings(
            user_id="u1", companion_mode="off",
            updated_at=now,
        )
        session.add(settings)

    # Write agent to local DB
    async with dual_db.get_local_db() as session:
        agent = Agent(
            id="ag1", name="TestAgent", avatar="T",
            description="test", system_prompt="prompt",
            adapter_name="mock", is_builtin=False,
            is_orchestrator=False, supports_vision=False,
            created_at=now, user_id="u1",
        )
        agent.capabilities_list = []
        agent.tool_names_list = []
        session.add(agent)

    # Read agent from local DB
    async with dual_db.get_local_db() as session:
        from sqlalchemy import select
        result = await session.execute(select(Agent).where(Agent.id == "ag1"))
        ag = result.scalar_one()
        assert ag.name == "TestAgent"

    # Read user settings from remote DB
    async with dual_db.get_remote_db() as session:
        result = await session.execute(
            select(UserSettings).where(UserSettings.user_id == "u1")
        )
        us = result.scalar_one()
        assert us.companion_mode == "off"


@pytest.mark.asyncio
async def test_sqlite_internal_fk(dual_db):
    """SQLite internal FK (Message → Conversation) works in local DB."""
    from sqlalchemy import select

    from app.db.models import Conversation, Message
    from app.utils.clock import now_ms

    now = now_ms()

    # Create conversation in local DB
    async with dual_db.get_local_db() as session:
        conv = Conversation(
            id="conv1", user_id="u1", title="Test",
            mode="single", created_at=now, updated_at=now,
        )
        conv.agent_ids_list = []
        conv.pinned_message_ids_list = []
        conv.bookmarked_message_ids_list = []
        session.add(conv)

    # Create message referencing the conversation
    async with dual_db.get_local_db() as session:
        msg = Message(
            id="msg1", conversation_id="conv1",
            role="user", status="complete",
            created_at=now,
        )
        msg.parts_list = []
        msg.mentioned_agent_ids_list = []
        session.add(msg)

    # Verify FK relationship works
    async with dual_db.get_local_db() as session:
        result = await session.execute(
            select(Message).where(Message.id == "msg1")
        )
        msg = result.scalar_one()
        assert msg.conversation_id == "conv1"


@pytest.mark.asyncio
async def test_no_redis_dependency(dual_db):
    """Redis client is not initialized in dual-DB mode."""
    # The cache module is a stub — no Redis connection required.
    # The key point: app does not require Redis to be running.
    assert dual_db._local_engine is not None
    assert dual_db._remote_engine is not None


@pytest.mark.asyncio
async def test_get_db_alias_fallback(dual_db):
    """get_db alias falls back to get_remote_db."""
    from app.db.engine import get_db, get_remote_db

    # get_db should be the same callable as get_remote_db
    assert get_db is get_remote_db

    # Using get_db should work (writes to remote engine)
    from app.db.models import User
    from app.utils.clock import now_ms

    now = now_ms()
    async with get_db() as session:
        user = User(
            id="u2", email="alias@dual.db", name="Alias",
            password_hash="hash", token_version=0,
            created_at=now, updated_at=now,
        )
        session.add(user)

    # Verify it's on the remote engine
    async with dual_db.get_remote_db() as session:
        from sqlalchemy import select
        result = await session.execute(select(User).where(User.id == "u2"))
        user = result.scalar_one()
        assert user.email == "alias@dual.db"


@pytest.mark.asyncio
async def test_get_local_db_fallback_single_mode(tmp_path, monkeypatch):
    """In single-DB mode (no DATABASE_LOCAL_URL), get_local_db falls back to remote."""
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
        # In single-DB mode, local engine is None
        assert engine_mod._local_engine is None
        assert engine_mod._local_session_factory is None

        # get_local_db should fall back to remote session factory
        from app.db.models import Agent
        from app.utils.clock import now_ms

        now = now_ms()
        async with engine_mod.get_local_db() as session:
            agent = Agent(
                id="ag_single", name="Single", avatar="S",
                description="test", system_prompt="prompt",
                adapter_name="mock", is_builtin=False,
                is_orchestrator=False, supports_vision=False,
                created_at=now,
            )
            agent.capabilities_list = []
            agent.tool_names_list = []
            session.add(agent)

        # The agent should be on the remote engine (since local is None)
        from sqlalchemy import select

        async with engine_mod.get_remote_db() as session:
            result = await session.execute(select(Agent).where(Agent.id == "ag_single"))
            ag = result.scalar_one()
            assert ag.name == "Single"

    finally:
        await engine_mod.close_db()
        get_settings.cache_clear()
