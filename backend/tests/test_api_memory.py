"""API tests for the memory management router (app/api/memory.py).

Covers:
- GET /api/memory/long-term (list, filter, pagination)
- PUT /api/memory/long-term/{id} (edit content/importance/category/tags)
- DELETE /api/memory/long-term/{id}
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
from unittest.mock import patch

import pytest


@pytest.fixture
async def memory_svc(db):
    """Create and initialize a real MemoryService bound to the test DB."""
    from app.config import Settings
    from app.memory.memory_service import MemoryService

    settings = Settings()
    svc = MemoryService(settings)
    await svc.initialize()
    with patch("app.main._memory_service", svc):
        yield svc


async def _seed_ltm(
    memory_svc,
    content: str = "test memory",
    importance: float = 0.5,
    category: str = "general",
    tags: list[str] | None = None,
    scope: str = "global",
    agent_id: str = "",
) -> int:
    """Insert a LongTermMemory row into PG and sync the in-memory LTM cache."""
    from app.db.engine import get_db
    from app.db.models import LongTermMemory

    now = time.time()
    async with get_db() as session:
        row = LongTermMemory(
            content=content,
            importance=importance,
            embedding=None,
            created_at=now,
            last_accessed=now,
            category=category,
            tags=tags or [],
            slot_hint="",
            score=0.0,
            scope=scope,
            agent_id=agent_id or None,
        )
        session.add(row)
        await session.flush()
        mem_id = row.id

    # Reload in-memory cache so update_item/delete_item can find the item
    await memory_svc.ltm.load_from_storage()
    return mem_id


# ─── LTM: GET /api/memory/long-term ────────────────────────────────────────


async def test_list_ltm_empty(memory_svc, api_client):
    resp = await api_client.get("/api/memory/long-term")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


async def test_list_ltm_with_data(memory_svc, api_client):
    await _seed_ltm(memory_svc, content="first memory", category="fact", tags=["tech"])
    await _seed_ltm(memory_svc, content="second memory", category="preference", tags=["style"])

    resp = await api_client.get("/api/memory/long-term")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert len(body["items"]) == 2
    # embedding should NOT be in response
    assert "embedding" not in body["items"][0]
    # verify field names are camelCase
    assert "agentId" in body["items"][0]
    assert "createdAt" in body["items"][0]
    assert "lastAccessed" in body["items"][0]


async def test_list_ltm_filter_by_category(memory_svc, api_client):
    await _seed_ltm(memory_svc, content="fact 1", category="fact")
    await _seed_ltm(memory_svc, content="pref 1", category="preference")

    resp = await api_client.get("/api/memory/long-term?category=fact")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["content"] == "fact 1"


async def test_list_ltm_filter_by_tag(memory_svc, api_client):
    await _seed_ltm(memory_svc, content="tagged", tags=["python", "web"])
    await _seed_ltm(memory_svc, content="untagged", tags=[])

    resp = await api_client.get("/api/memory/long-term?tag=python")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["content"] == "tagged"


async def test_list_ltm_filter_by_agent_id(memory_svc, api_client):
    await _seed_ltm(memory_svc, content="global mem", scope="global")
    await _seed_ltm(memory_svc, content="agent mem", scope="agent", agent_id="agt_123")

    resp = await api_client.get("/api/memory/long-term?agent_id=agt_123")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["content"] == "agent mem"
    assert body["items"][0]["scope"] == "agent"
    assert body["items"][0]["agentId"] == "agt_123"


async def test_list_ltm_pagination(memory_svc, api_client):
    for i in range(5):
        await _seed_ltm(memory_svc, content=f"mem {i}")

    resp = await api_client.get("/api/memory/long-term?page=1&size=2")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2
    assert body["page"] == 1
    assert body["size"] == 2

    resp2 = await api_client.get("/api/memory/long-term?page=2&size=2")
    body2 = resp2.json()
    assert len(body2["items"]) == 2


# ─── LTM: PUT /api/memory/long-term/{id} ───────────────────────────────────


async def test_update_ltm_content(memory_svc, api_client):
    mem_id = await _seed_ltm(memory_svc, content="old content")

    resp = await api_client.put(
        f"/api/memory/long-term/{mem_id}",
        json={"content": "new content"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # verify in PG
    resp2 = await api_client.get("/api/memory/long-term")
    items = resp2.json()["items"]
    assert items[0]["content"] == "new content"


async def test_update_ltm_importance_only(memory_svc, api_client):
    mem_id = await _seed_ltm(memory_svc, content="keep content", importance=0.3)

    resp = await api_client.put(
        f"/api/memory/long-term/{mem_id}",
        json={"importance": 0.9},
    )
    assert resp.status_code == 200

    resp2 = await api_client.get("/api/memory/long-term")
    item = resp2.json()["items"][0]
    assert item["importance"] == 0.9
    assert item["content"] == "keep content"


async def test_update_ltm_not_found(memory_svc, api_client):
    resp = await api_client.put(
        "/api/memory/long-term/99999",
        json={"content": "nope"},
    )
    assert resp.status_code == 404


async def test_update_ltm_category_and_tags(memory_svc, api_client):
    mem_id = await _seed_ltm(memory_svc, content="test", category="general", tags=[])

    resp = await api_client.put(
        f"/api/memory/long-term/{mem_id}",
        json={"category": "fact", "tags": ["python", "ai"]},
    )
    assert resp.status_code == 200

    resp2 = await api_client.get("/api/memory/long-term")
    item = resp2.json()["items"][0]
    assert item["category"] == "fact"
    assert item["tags"] == ["python", "ai"]


# ─── LTM: DELETE /api/memory/long-term/{id} ────────────────────────────────


async def test_delete_ltm(memory_svc, api_client):
    mem_id = await _seed_ltm(memory_svc, content="to be deleted")

    resp = await api_client.delete(f"/api/memory/long-term/{mem_id}")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # verify gone
    resp2 = await api_client.get("/api/memory/long-term")
    assert resp2.json()["total"] == 0


async def test_delete_ltm_not_found(memory_svc, api_client):
    resp = await api_client.delete("/api/memory/long-term/99999")
    assert resp.status_code == 404


# ─── Preferences: GET /api/memory/preferences ─────────────────────────────


async def test_list_preferences_empty(memory_svc, api_client):
    resp = await api_client.get("/api/memory/preferences")
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


async def test_list_preferences_with_data(memory_svc, api_client):
    await memory_svc.preference.set("喜好", "TypeScript")
    await memory_svc.preference.set("姓名", "Alice")

    resp = await api_client.get("/api/memory/preferences")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    keys = {item["key"] for item in body["items"]}
    assert "喜好" in keys
    assert "姓名" in keys


# ─── Preferences: PUT /api/memory/preferences/{key} ────────────────────────


async def test_update_preference(memory_svc, api_client):
    resp = await api_client.put(
        "/api/memory/preferences/喜好",
        json={"value": "Python"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # verify in-memory
    assert memory_svc.preference.get("喜好") == "Python"

    # verify via list endpoint
    resp2 = await api_client.get("/api/memory/preferences")
    items = {item["key"]: item["value"] for item in resp2.json()["items"]}
    assert items["喜好"] == "Python"


async def test_update_preference_overwrite(memory_svc, api_client):
    await memory_svc.preference.set("喜好", "Java")

    resp = await api_client.put(
        "/api/memory/preferences/喜好",
        json={"value": "Rust"},
    )
    assert resp.status_code == 200
    assert memory_svc.preference.get("喜好") == "Rust"


# ─── Preferences: DELETE /api/memory/preferences/{key} ─────────────────────


async def test_delete_preference(memory_svc, api_client):
    await memory_svc.preference.set("喜好", "Go")

    resp = await api_client.delete("/api/memory/preferences/喜好")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert memory_svc.preference.get("喜好") == ""


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
