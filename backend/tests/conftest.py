"""Shared pytest fixtures for the phase-2 service-layer tests.

Each test gets an isolated file-based SQLite DB and workspace root under a fresh
tmp_path, with the FK pragma enabled (so cascade deletes work) and two seeded
agents (a plain one and an orchestrator).
"""

import pytest_asyncio


@pytest_asyncio.fixture
async def api_client(db):
    """An httpx AsyncClient bound to the FastAPI app over ASGITransport.

    Shares the same isolated test DB/workspace as the `db` fixture (which sets the
    DATABASE_URL/WORKSPACE_ROOT env and initialises the schema before the app is
    built). The lifespan's only side effect besides init_db is importing
    agent_runner to wire the real runner into runner_registry, so we import it
    here explicitly and skip running the lifespan (init_db already ran via `db`).

    Usage::

        async def test_health(api_client):
            resp = await api_client.get("/health")
            assert resp.status_code == 200
    """
    import httpx

    import app.services.agent_runner  # noqa: F401  wires runner into registry
    from app.main import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def db(tmp_path, monkeypatch):
    """Initialise an isolated test database; tear it down afterwards."""
    db_file = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspaces"))

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
async def agents(db):
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
            supports_vision=False,
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
            supports_vision=False,
            created_at=now,
        )
        orch.capabilities_list = []
        orch.tool_names_list = []

        session.add(alice)
        session.add(orch)

    return {"alice": "ag_alice", "orch": "ag_orch"}
