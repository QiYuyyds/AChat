"""Tests for the runs_misc router (abort / search / usage / platform / hints / download).

A dedicated app fixture mounts ONLY this router under the /api prefix (mirroring
how main.py wires routers) so the tests are independent of the Integrate stage
and of the pre-existing search/runs stub routers that would otherwise shadow
/api/search and /api/runs/{id}/abort.
"""

import httpx
import pytest_asyncio

from app.db.engine import get_db
from app.db.models import AgentRun, Conversation, Message, Workspace
from app.utils.clock import now_ms
from app.utils.ids import (
    new_conversation_id,
    new_message_id,
    new_run_id,
    new_workspace_id,
)


@pytest_asyncio.fixture
async def client(db, test_user):
    """httpx client over a FastAPI app mounting only runs_misc.router at /api.

    The runs_misc routes require JWT auth (added in 62407ef), so the client
    authenticates as the shared test user.
    """
    from fastapi import FastAPI

    from app.api import runs_misc

    app = FastAPI()
    app.include_router(runs_misc.router, prefix="/api")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        c.headers["Authorization"] = f"Bearer {test_user['token']}"
        yield c


async def _seed_conversation(agent_id: str, tmp_path, title: str = "T") -> str:
    conv_id = new_conversation_id()
    now = now_ms()
    ws_root = tmp_path / f"ws_{conv_id}"
    ws_root.mkdir(parents=True, exist_ok=True)
    async with get_db() as session:
        conv = Conversation(
            id=conv_id,
            title=title,
            mode="single",
            archived=False,
            fs_write_approval_mode="auto",
            created_at=now,
            updated_at=now,
        )
        conv.agent_ids_list = [agent_id]
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
    return conv_id


async def _seed_message(conv_id: str, agent_id: str | None, role: str, text: str) -> str:
    mid = new_message_id()
    async with get_db() as session:
        msg = Message(
            id=mid,
            conversation_id=conv_id,
            role=role,
            agent_id=agent_id,
            status="complete",
            created_at=now_ms(),
        )
        msg.parts_list = [{"type": "text", "content": text}]
        msg.mentioned_agent_ids_list = []
        session.add(msg)
    return mid


async def _seed_run(conv_id: str, agent_id: str, usage: dict | None, started_at: int) -> str:
    rid = new_run_id()
    async with get_db() as session:
        run = AgentRun(
            id=rid,
            conversation_id=conv_id,
            agent_id=agent_id,
            status="finished",
            started_at=started_at,
            finished_at=started_at,
        )
        run.usage_dict = usage
        session.add(run)
    return rid


async def _seed_run_with_status(
    conv_id: str,
    agent_id: str,
    status: str,
    started_at: int,
    *,
    dispatch_plan: dict | None = None,
    dispatch_results: dict | None = None,
    finished_at: int | None = None,
    parent_run_id: str | None = None,
) -> str:
    rid = new_run_id()
    async with get_db() as session:
        session.add(
            AgentRun(
                id=rid,
                conversation_id=conv_id,
                agent_id=agent_id,
                status=status,
                started_at=started_at,
                finished_at=finished_at,
                parent_run_id=parent_run_id,
                dispatch_plan=dispatch_plan,
                dispatch_results=dispatch_results,
            )
        )
    return rid


def _register_memory_run(run_id: str, conv_id: str, agent_id: str, started_at: int) -> None:
    """Insert a fake in-memory active run (as execute_run registration would)."""
    from app.services import agent_runner

    agent_runner._active_run_meta[run_id] = {
        "agent_id": agent_id,
        "conversation_id": conv_id,
        "trigger_message_id": None,
        "parent_run_id": None,
        "started_at": started_at,
    }


def _unregister_memory_run(run_id: str) -> None:
    from app.services import agent_runner

    agent_runner._active_run_meta.pop(run_id, None)
    agent_runner._active_runs.pop(run_id, None)


# ─── GET /conversations/{id}/runs ────────────────────────────────────────────
async def test_list_runs_empty(client, agents, tmp_path):
    conv_id = await _seed_conversation(agents["alice"], tmp_path)
    resp = await client.get(f"/api/conversations/{conv_id}/runs")
    assert resp.status_code == 200
    assert resp.json() == {"runs": []}


async def test_list_runs_unknown_conversation_returns_404(client):
    resp = await client.get("/api/conversations/conv_missing/runs")
    assert resp.status_code == 404


async def test_list_runs_orphaned_db_run_returned(client, agents, tmp_path):
    """A run row left 'running' by a previous process is served from the DB."""
    conv_id = await _seed_conversation(agents["alice"], tmp_path)
    now = now_ms()
    rid = await _seed_run_with_status(
        conv_id, agents["alice"], "running", now - 1000
    )

    resp = await client.get(f"/api/conversations/{conv_id}/runs")
    assert resp.status_code == 200
    runs = resp.json()["runs"]
    assert len(runs) == 1
    run = runs[0]
    assert run["id"] == rid
    assert run["status"] == "running"
    assert run["conversationId"] == conv_id
    assert run["agentId"] == agents["alice"]
    assert run["dispatchPlan"] is None
    assert run["dispatchResults"] is None


async def test_list_runs_active_memory_state_wins(client, agents, tmp_path):
    """A live run is served from memory; dispatch JSONB comes from the DB row."""
    conv_id = await _seed_conversation(agents["alice"], tmp_path)
    now = now_ms()
    rid = await _seed_run_with_status(
        conv_id,
        agents["alice"],
        "running",
        now - 5000,
        dispatch_plan={"dagId": "dag1", "visibility": "hidden", "tasks": [{"id": "t1"}]},
        dispatch_results={"taskStatus": {"t1": "running"}, "childRunIds": {}},
    )
    _register_memory_run(rid, conv_id, agents["alice"], now - 4000)
    try:
        resp = await client.get(f"/api/conversations/{conv_id}/runs")
        assert resp.status_code == 200
        runs = resp.json()["runs"]
        assert len(runs) == 1
        run = runs[0]
        # memory truth: startedAt from registration, status running
        assert run["startedAt"] == now - 4000
        assert run["status"] == "running"
        # dispatch JSONB attached from the DB row
        assert run["dispatchPlan"]["dagId"] == "dag1"
        assert run["dispatchResults"]["taskStatus"] == {"t1": "running"}
    finally:
        _unregister_memory_run(rid)


async def test_list_runs_dedupe_memory_first(client, agents, tmp_path):
    """The same run id in memory and DB is not duplicated; memory status wins."""
    conv_id = await _seed_conversation(agents["alice"], tmp_path)
    now = now_ms()
    rid = await _seed_run_with_status(conv_id, agents["alice"], "complete", now - 5000)
    _register_memory_run(rid, conv_id, agents["alice"], now - 6000)
    try:
        resp = await client.get(f"/api/conversations/{conv_id}/runs")
        runs = resp.json()["runs"]
        assert len(runs) == 1
        assert runs[0]["status"] == "running"
    finally:
        _unregister_memory_run(rid)


async def test_list_runs_includes_old_dispatch_run_and_excludes_old_plain(client, agents, tmp_path):
    """Old terminal runs restore only when they carry dispatch data."""
    conv_id = await _seed_conversation(agents["alice"], tmp_path)
    now = now_ms()
    month_ago = now - 30 * 24 * 60 * 60 * 1000
    old_dispatch = await _seed_run_with_status(
        conv_id,
        agents["alice"],
        "complete",
        month_ago,
        dispatch_plan={"dagId": "dag-old", "visibility": "hidden", "tasks": [{"id": "t1"}]},
    )
    await _seed_run_with_status(
        conv_id, agents["alice"], "complete", month_ago, finished_at=month_ago
    )

    resp = await client.get(f"/api/conversations/{conv_id}/runs")
    runs = resp.json()["runs"]
    ids = [r["id"] for r in runs]
    assert old_dispatch in ids
    assert len(ids) == 1
    assert runs[0]["dispatchPlan"]["dagId"] == "dag-old"


# ─── abort ───────────────────────────────────────────────────────────────────
async def test_abort_unknown_run_returns_404(client):
    resp = await client.post("/api/runs/run_does_not_exist/abort")
    assert resp.status_code == 404
    assert resp.json() == {"error": "Run not found or already finished"}


# ─── search ──────────────────────────────────────────────────────────────────
async def test_search_finds_matching_message(client, agents, tmp_path):
    conv_id = await _seed_conversation(agents["alice"], tmp_path, title="Conv A")
    await _seed_message(conv_id, agents["alice"], "agent", "hello pineapple world")
    await _seed_message(conv_id, None, "user", "totally unrelated")

    resp = await client.get("/api/search", params={"q": "pineapple"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    data = body["data"]
    assert data["total"] == 1
    assert len(data["hits"]) == 1
    hit = data["hits"][0]
    assert hit["conversationId"] == conv_id
    assert hit["conversationTitle"] == "Conv A"
    assert hit["role"] == "agent"
    assert hit["agentId"] == agents["alice"]
    assert "tookMs" in data


async def test_search_role_filter(client, agents, tmp_path):
    conv_id = await _seed_conversation(agents["alice"], tmp_path)
    await _seed_message(conv_id, agents["alice"], "agent", "shared keyword agentside")
    await _seed_message(conv_id, None, "user", "shared keyword userside")

    resp = await client.get(
        "/api/search", params={"q": "shared keyword", "role": "user"}
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["total"] == 1
    assert data["hits"][0]["role"] == "user"


async def test_search_invalid_role_returns_400(client):
    resp = await client.get("/api/search", params={"q": "x", "role": "robot"})
    assert resp.status_code == 400
    body = resp.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "INVALID_QUERY"


async def test_search_missing_q_returns_422(client):
    # FastAPI Query(..., min_length=1) rejects an empty/absent q before the handler.
    resp = await client.get("/api/search")
    assert resp.status_code == 422


# ─── usage/summary ───────────────────────────────────────────────────────────
async def test_usage_summary_empty(client):
    resp = await client.get("/api/usage/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["allTime"]["totalTokens"] == 0
    assert body["topConversations"] == []
    assert body["byAgent"] == []
    assert body["byModel"] == []


async def test_usage_summary_aggregates(client, agents, tmp_path):
    conv_id = await _seed_conversation(agents["alice"], tmp_path, title="Usage Conv")
    now = now_ms()
    await _seed_run(
        conv_id,
        agents["alice"],
        {
            "inputTokens": 100,
            "outputTokens": 50,
            "cacheReadTokens": 10,
            "cacheCreationTokens": 5,
            "model": "claude-test",
        },
        now,
    )
    # An old run (outside the week window) still counts toward all-time only.
    await _seed_run(
        conv_id,
        agents["alice"],
        {
            "inputTokens": 1,
            "outputTokens": 1,
            "cacheReadTokens": 0,
            "cacheCreationTokens": 0,
            "model": "claude-test",
        },
        now - 30 * 24 * 60 * 60 * 1000,
    )

    resp = await client.get("/api/usage/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["allTime"]["totalTokens"] == 165 + 2
    assert body["allTime"]["runs"] == 2
    assert body["today"]["totalTokens"] == 165
    assert body["week"]["totalTokens"] == 165
    assert body["byAgent"][0]["agentId"] == agents["alice"]
    assert body["byAgent"][0]["name"] == "Alice"
    assert body["byModel"][0]["model"] == "claude-test"
    assert body["topConversations"][0]["id"] == conv_id
    assert body["topConversations"][0]["title"] == "Usage Conv"


# ─── platform ────────────────────────────────────────────────────────────────
async def test_platform(client):
    resp = await client.get("/api/platform")
    assert resp.status_code == 200
    assert resp.json()["platform"] in ("windows", "posix")


# ─── connection-hints ────────────────────────────────────────────────────────
async def test_connection_hints(client):
    resp = await client.get("/api/connection-hints")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["hints"], list)
    # local hint is always appended and sorts last.
    assert any(h["kind"] == "local" for h in body["hints"])
    assert body["companionMode"] == "off"
    assert body["mobileDeviceTokenConfigured"] is False


# ─── deployments download ────────────────────────────────────────────────────
async def test_download_deployment_unknown_source_returns_404(client):
    resp = await client.get("/api/deployments/dep_missing/download/source")
    assert resp.status_code == 404
    assert resp.json() == {"error": "Deployment not found"}


async def test_download_deployment_invalid_kind_returns_400(client):
    resp = await client.get("/api/deployments/dep_missing/download/banana")
    assert resp.status_code == 400
    assert resp.json() == {"error": "Invalid download kind"}
