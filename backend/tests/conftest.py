"""Shared pytest fixtures for the phase-2 service-layer tests.

Each test gets an isolated file-based SQLite DB and workspace root under a fresh
tmp_path, with the FK pragma enabled (so cascade deletes work) and two seeded
agents (a plain one and an orchestrator).

Auth: the `db` fixture sets `JWT_SECRET` so JWT creation/verification works.
The `api_client` fixture creates a test user and sets the auth cookie so all
existing tests authenticate transparently. Tests that need an unauthenticated
client can use `raw_client`.
"""

import pytest_asyncio

# agent_eval (formerly eval_harness) is consumed as an installed (editable)
# package — `pip install -e ../aeval/packages/agent-eval[api,cli]` — so no
# sys.path routing is needed here anymore.

_TEST_JWT_SECRET = "test-secret-at-least-32-characters-long!!"


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    """Initialise an isolated test database; tear it down afterwards."""
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("JWT_SECRET", _TEST_JWT_SECRET)
    monkeypatch.setenv("ALLOW_REGISTRATION", "true")
    # Single-DB mode (DATABASE_LOCAL_URL unset): all 27 tables live on one
    # engine, matching the desktop deployment. The engine falls back to the
    # remote session factory for local tables in this mode.
    monkeypatch.setenv("DATABASE_LOCAL_URL", "")

    from app.config import get_settings

    get_settings.cache_clear()

    from app.db import engine as engine_mod

    await engine_mod.init_db()
    try:
        yield engine_mod
    finally:
        # phase 5 wires a real AgentRunner that spawns detached run tasks; drain
        # any leftovers before tearing the DB down so they don't outlive it.
        await _drain_active_runs()
        await engine_mod.close_db()
        get_settings.cache_clear()


@pytest_asyncio.fixture
async def test_user(db):
    """Create a test user and return (user_id, email, access_token)."""
    from app.auth.jwt_handler import create_access_token
    from app.auth.password import hash_password
    from app.db.engine import get_db
    from app.db.models import User
    from app.utils.clock import now_ms

    now = now_ms()
    async with get_db() as session:
        user = User(
            id="test_user_1",
            email="test@example.com",
            name="Test User",
            password_hash=hash_password("testpass123"),
            token_version=0,
            created_at=now,
            updated_at=now,
        )
        session.add(user)

    token = create_access_token("test_user_1", "test@example.com", 0)
    return {"id": "test_user_1", "email": "test@example.com", "token": token}


@pytest_asyncio.fixture
async def api_client(db, test_user):
    """An httpx AsyncClient bound to the FastAPI app over ASGITransport.

    Authenticated as the test user (auth cookie pre-set) so all existing
    tests work transparently with the auth system.
    """
    import httpx

    import app.services.agent_runner  # noqa: F401  wires runner into registry
    from app.main import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers["Authorization"] = f"Bearer {test_user['token']}"
        yield client


@pytest_asyncio.fixture
async def raw_client(db):
    """An httpx AsyncClient without authentication (for auth endpoint tests)."""
    import httpx

    import app.services.agent_runner  # noqa: F401
    from app.main import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def _drain_active_runs() -> None:
    """Cancel and await any still-running AgentRunner tasks (test isolation)."""
    import contextlib

    try:
        from app.services import agent_runner as ar
    except ImportError:
        return

    entries = list(ar._active_runs.values())
    for task, cancel_event in entries:
        cancel_event.set()
        task.cancel()
    for task, _ in entries:
        with contextlib.suppress(BaseException):
            await task
    ar._active_runs.clear()


@pytest_asyncio.fixture
async def agents(db, test_user):
    """Seed two agents and return their ids: a normal one and an orchestrator."""
    from app.db.engine import get_db
    from app.db.models import Agent
    from app.utils.clock import now_ms

    now = now_ms()
    async with get_db() as session:
        alice = Agent(
            id="ag_alice",
            name="Alice",
            avatar="A",
            description="helper",
            system_prompt="alice prompt",
            adapter_name="mock",
            is_builtin=False,
            is_orchestrator=False,
            created_at=now,
        )
        alice.capabilities_list = []
        alice.tool_names_list = []

        orch = Agent(
            id="ag_orch",
            name="Orchestrator",
            avatar="O",
            description="orchestrator",
            system_prompt="orch prompt",
            adapter_name="mock",
            is_builtin=True,
            is_orchestrator=True,
            created_at=now,
        )
        orch.capabilities_list = []
        orch.tool_names_list = []

        session.add(alice)
        session.add(orch)

    return {"alice": "ag_alice", "orch": "ag_orch"}
