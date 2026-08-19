"""API tests for the memory management router (app/api/memory.py).

Covers:
- GET /api/memory/files (list + bucket filter: all / procedure / wiki / daily)
- GET /api/memory/preferences (list)
- PUT /api/memory/preferences/{key} (upsert)
- DELETE /api/memory/preferences/{key}
- GET /api/memory/session/{conversation_id} (view)
- GET /api/memory/sessions (list all)

Uses the api_client fixture (isolated test DB) and patches
``app.main._memory_service`` with a real MemoryService instance.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
async def memory_svc(db, tmp_path):
    """Create and initialize a real MemoryService with an isolated workspace."""
    from app.config import Settings
    from app.memory.memory_service import MemoryService

    settings = Settings(memory_workspace_dir=str(tmp_path / "memory"))
    svc = MemoryService(settings)
    await svc.initialize()
    with patch("app.main._memory_service", svc):
        yield svc


def _seed_file_native_memories(memory_svc) -> None:
    """Seed one procedure, one wiki, and one daily card under the workspace."""
    from app.memory.file_store.frontmatter import MemoryFrontmatter
    from app.memory.file_store.markdown_io import write_markdown

    ws = memory_svc.workspace
    write_markdown(
        ws.digest_path("procedure", "how-to-deploy"),
        MemoryFrontmatter(
            name="how-to-deploy",
            description="Deploy steps",
            bucket="procedure",
            tags=["deploy"],
            importance=0.8,
        ),
        "Procedure body",
    )
    write_markdown(
        ws.digest_path("wiki", "react-hooks"),
        MemoryFrontmatter(
            name="react-hooks",
            description="Hooks knowledge",
            bucket="wiki",
            tags=["react"],
            importance=0.7,
        ),
        "Wiki body",
    )
    write_markdown(
        ws.daily_file_path("session_abc", "2026-08-04"),
        MemoryFrontmatter(
            name="session_abc",
            description="Daily card",
            tags=["session"],
            importance=0.4,
        ),
        "Daily body",
    )


# ─── Files: GET /api/memory/files ───────────────────────────────────────────


async def test_list_files_empty(memory_svc, api_client):
    resp = await api_client.get("/api/memory/files")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


async def test_list_files_all_includes_daily_and_digest(memory_svc, api_client):
    _seed_file_native_memories(memory_svc)

    resp = await api_client.get("/api/memory/files")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    buckets = {item["bucket"] for item in body["items"]}
    assert buckets == {"procedure", "wiki", "daily"}
    names = {item["name"] for item in body["items"]}
    assert names == {"how-to-deploy", "react-hooks", "session_abc"}


async def test_list_files_procedure_excludes_daily(memory_svc, api_client):
    _seed_file_native_memories(memory_svc)

    resp = await api_client.get("/api/memory/files?bucket=procedure")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["bucket"] == "procedure"
    assert body["items"][0]["name"] == "how-to-deploy"
    assert not any(item["bucket"] == "daily" for item in body["items"])
    assert not any(item["path"].replace("\\", "/").startswith("daily/") for item in body["items"])


async def test_list_files_wiki_excludes_daily(memory_svc, api_client):
    _seed_file_native_memories(memory_svc)

    resp = await api_client.get("/api/memory/files?bucket=wiki")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["bucket"] == "wiki"
    assert body["items"][0]["name"] == "react-hooks"
    assert not any(item["bucket"] == "daily" for item in body["items"])
    assert not any(item["path"].replace("\\", "/").startswith("daily/") for item in body["items"])


async def test_list_files_daily_excludes_digest(memory_svc, api_client):
    _seed_file_native_memories(memory_svc)

    resp = await api_client.get("/api/memory/files?bucket=daily")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["bucket"] == "daily"
    assert body["items"][0]["name"] == "session_abc"
    assert body["items"][0]["path"].replace("\\", "/").startswith("daily/")
    assert not any(item["bucket"] in ("procedure", "wiki") for item in body["items"])


async def test_list_files_unknown_bucket_empty(memory_svc, api_client):
    _seed_file_native_memories(memory_svc)

    resp = await api_client.get("/api/memory/files?bucket=personal")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


# ─── Search: GET /api/memory/search ────────────────────────────────────────


async def test_search_returns_relative_path_openable(memory_svc, api_client):
    """Search hits use workspace-relative paths that open via files API."""
    _seed_file_native_memories(memory_svc)
    memory_svc.auto_index.full_reindex()

    resp = await api_client.get("/api/memory/search", params={"query": "react hooks"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1

    hit = next(item for item in body["items"] if item["name"] == "react-hooks")
    path = hit["path"]
    assert not Path(path).is_absolute()
    assert "react-hooks" in path.replace("\\", "/")

    detail = await api_client.get(f"/api/memory/files/{path}")
    assert detail.status_code == 200
    assert detail.json()["name"] == "react-hooks"
    assert "Wiki body" in detail.json()["body"]


# ─── Preferences: GET /api/memory/preferences ─────────────────────────────


async def test_list_preferences_empty(memory_svc, api_client):
    resp = await api_client.get("/api/memory/preferences")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


async def test_list_preferences_with_data(memory_svc, api_client, test_user):
    from app.memory.preference import Preference

    # Preferences are user-scoped; seed against the authenticated test user.
    pref = Preference(user_id=test_user["id"])
    await pref.set("喜好", "TypeScript")
    await pref.set("姓名", "Alice")

    resp = await api_client.get("/api/memory/preferences")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    keys = {item["key"] for item in body["items"]}
    assert "喜好" in keys
    assert "姓名" in keys


# ─── Preferences: PUT /api/memory/preferences/{key} ────────────────────────


async def test_update_preference(memory_svc, api_client, test_user):
    from app.memory.preference import Preference

    resp = await api_client.put(
        "/api/memory/preferences/喜好",
        json={"value": "Python"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # verify via list endpoint (same authenticated user)
    resp2 = await api_client.get("/api/memory/preferences")
    items = {item["key"]: item["value"] for item in resp2.json()["items"]}
    assert items["喜好"] == "Python"

    # verify DB-backed Preference for this user
    pref = Preference(user_id=test_user["id"])
    await pref.load_from_storage()
    assert pref.get("喜好") == "Python"


async def test_update_preference_overwrite(memory_svc, api_client, test_user):
    from app.memory.preference import Preference

    pref = Preference(user_id=test_user["id"])
    await pref.set("喜好", "Java")

    resp = await api_client.put(
        "/api/memory/preferences/喜好",
        json={"value": "Rust"},
    )
    assert resp.status_code == 200

    await pref.load_from_storage()
    assert pref.get("喜好") == "Rust"


# ─── Preferences: DELETE /api/memory/preferences/{key} ─────────────────────


async def test_delete_preference(memory_svc, api_client, test_user):
    from app.memory.preference import Preference

    pref = Preference(user_id=test_user["id"])
    await pref.set("喜好", "Go")

    resp = await api_client.delete("/api/memory/preferences/喜好")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    await pref.load_from_storage()
    assert pref.get("喜好") == ""


async def test_delete_preference_not_found(memory_svc, api_client):
    resp = await api_client.delete("/api/memory/preferences/nonexistent_key")
    assert resp.status_code == 404


# ─── Session Memory: GET /api/memory/session/{conversation_id} ─────────────


async def _seed_conversation(conv_id: str = "conv_test", title: str = "Test Conv") -> None:
    from app.db.engine import get_db
    from app.db.models import Conversation
    from app.utils.clock import now_ms

    async with get_db() as session:
        conv = Conversation(
            id=conv_id,
            title=title,
            mode="single",
            agent_ids_list=[],
            pinned_message_ids_list=[],
            bookmarked_message_ids_list=[],
            created_at=now_ms(),
            updated_at=now_ms(),
        )
        session.add(conv)


async def _seed_session_memory(conv_id: str, summary: str = "Test summary") -> None:
    from app.db.engine import get_db
    from app.db.models import ContextSummary
    from app.utils.clock import now_ms
    from app.utils.ids import new_context_summary_id

    async with get_db() as session:
        row = ContextSummary(
            id=new_context_summary_id(),
            conversation_id=conv_id,
            summary=summary,
            covered_until_message_id="session",
            covered_until_created_at=int(time.time()),
            source_message_count=5,
            token_estimate=100,
            model_provider=None,
            model_id=None,
            summary_type="session",
            covers_up_to=time.time(),
            created_at=now_ms(),
        )
        session.add(row)


async def test_get_session_memory(memory_svc, api_client):
    await _seed_conversation("conv_1", "My Conversation")
    await _seed_session_memory("conv_1", "User discussed TypeScript project setup.")

    resp = await api_client.get("/api/memory/session/conv_1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["conversationId"] == "conv_1"
    assert body["title"] == "My Conversation"
    assert "TypeScript" in body["summary"]
    assert body["coversUpTo"] is not None


async def test_get_session_memory_not_found(memory_svc, api_client):
    resp = await api_client.get("/api/memory/session/nonexistent_conv")
    assert resp.status_code == 404


# ─── Session Memory: GET /api/memory/sessions ──────────────────────────────


async def test_list_session_memories(memory_svc, api_client):
    await _seed_conversation("conv_a", "Conv A")
    await _seed_conversation("conv_b", "Conv B")
    await _seed_session_memory("conv_a", "Summary A")
    await _seed_session_memory("conv_b", "Summary B")

    resp = await api_client.get("/api/memory/sessions")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    titles = {item["title"] for item in body["items"]}
    assert "Conv A" in titles
    assert "Conv B" in titles


async def test_list_session_memories_empty(memory_svc, api_client):
    resp = await api_client.get("/api/memory/sessions")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


# ─── Graph: GET /api/memory/graph ─────────────────────────────────────────


def _seed_graph_memories(memory_svc) -> None:
    """Seed memory files with wikilinks for graph API testing."""
    from app.memory.file_store.frontmatter import MemoryFrontmatter
    from app.memory.file_store.markdown_io import write_markdown

    ws = memory_svc.workspace
    write_markdown(
        ws.digest_path("wiki", "python"),
        MemoryFrontmatter(name="Python", description="Python language", bucket="wiki", tags=["python"], importance=0.8),
        "Python is a language. [[asyncio]] [[threading]]",
    )
    write_markdown(
        ws.digest_path("wiki", "asyncio"),
        MemoryFrontmatter(name="AsyncIO", description="Async IO", bucket="wiki", tags=["async"], importance=0.7),
        "AsyncIO module. [[python]]",
    )
    write_markdown(
        ws.digest_path("procedure", "deploy"),
        MemoryFrontmatter(name="Deploy", description="Deploy steps", bucket="procedure", tags=["deploy"], importance=0.9),
        "Deploy steps. [[python]]",
    )
    memory_svc.auto_index.full_reindex()


async def test_graph_empty(memory_svc, api_client):
    resp = await api_client.get("/api/memory/graph")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"nodes": [], "edges": []}


async def test_graph_full(memory_svc, api_client):
    _seed_graph_memories(memory_svc)
    resp = await api_client.get("/api/memory/graph")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["nodes"]) == 3
    assert len(body["edges"]) >= 3
    node = body["nodes"][0]
    assert "path" in node
    assert "name" in node
    assert "bucket" in node
    assert "degree" in node
    edge = body["edges"][0]
    assert "source" in edge
    assert "target" in edge
    assert "predicate" in edge


async def test_graph_bucket_filter(memory_svc, api_client):
    _seed_graph_memories(memory_svc)
    resp = await api_client.get("/api/memory/graph?bucket=wiki")
    assert resp.status_code == 200
    body = resp.json()
    assert all(n["bucket"] == "wiki" for n in body["nodes"])
    assert not any("procedure" in n["path"] for n in body["nodes"])


async def test_graph_503_when_uninitialized(api_client):
    """When MemoryService is not wired, the endpoint returns 503."""
    from unittest.mock import patch

    with patch("app.main._memory_service", None):
        resp = await api_client.get("/api/memory/graph")
        assert resp.status_code == 503
