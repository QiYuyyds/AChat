"""Tests for the mobile companion API routes (app/api/mobile/routes.py).

These tests mount ONLY the mobile router into a fresh FastAPI app (the Integrate
stage wires it into app.main; until then we mount it directly), sharing the
isolated `db` fixture's SQLite DB. A valid mobile bearer token is set per-test.
"""

import httpx
import pytest_asyncio

from app.utils.clock import now_ms

TOKEN = "test-mobile-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest_asyncio.fixture
async def mobile_client(db, monkeypatch):
    """httpx client over an app mounting only the mobile router, token configured."""
    from fastapi import FastAPI

    from app.api.mobile.routes import router as mobile_router

    monkeypatch.setenv("AGENTHUB_MOBILE_TOKEN", TOKEN)
    monkeypatch.delenv("AGENTHUB_MOBILE_DEV_TOKEN", raising=False)

    app = FastAPI()
    app.include_router(mobile_router, prefix="/api")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def conversation(agents):
    """A single-agent conversation with one user message."""
    from app.services import conversation_service

    conv = await conversation_service.create_conversation(
        mode="single", agent_ids=[agents["alice"]], title="Mobile Test"
    )
    await conversation_service.send_message(
        conversation_id=conv.id, content="hello from desktop"
    )
    return conv


async def _seed_artifact(conversation_id: str, agent_id: str) -> str:
    from app.db.engine import get_db
    from app.db.models import Artifact

    artifact_id = "art_mobile_1"
    async with get_db() as session:
        art = Artifact(
            id=artifact_id,
            conversation_id=conversation_id,
            type="document",
            title="Doc",
            version=1,
            parent_artifact_id=None,
            created_by_agent_id=agent_id,
            created_at=now_ms(),
        )
        art.content_dict = {"type": "document", "markdown": "# Hi"}
        session.add(art)
    return artifact_id


# ─── Auth ───────────────────────────────────────────────────────────────────
async def test_snapshot_unauthorized_without_token(mobile_client):
    resp = await mobile_client.get("/api/mobile/snapshot")
    assert resp.status_code == 401
    assert resp.json() == {"error": "Unauthorized"}


async def test_snapshot_503_when_not_configured(db, monkeypatch):
    from fastapi import FastAPI

    from app.api.mobile.routes import router as mobile_router

    monkeypatch.delenv("AGENTHUB_MOBILE_TOKEN", raising=False)
    monkeypatch.delenv("AGENTHUB_MOBILE_DEV_TOKEN", raising=False)
    app = FastAPI()
    app.include_router(mobile_router, prefix="/api")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/mobile/snapshot", headers=AUTH)
    assert resp.status_code == 503
    assert "not configured" in resp.json()["error"]


# ─── Snapshot ───────────────────────────────────────────────────────────────
async def test_snapshot_happy(mobile_client, conversation):
    resp = await mobile_client.get("/api/mobile/snapshot", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert {
        "conversations",
        "agents",
        "runningRuns",
        "pendingWrites",
        "pendingQuestions",
        "server",
    } <= set(body.keys())
    assert body["server"]["version"] == "0.1.0"
    assert body["server"]["companionMode"] in ("lan", "tailnet")
    conv_ids = [c["id"] for c in body["conversations"]]
    assert conversation.id in conv_ids
    summary = next(c for c in body["conversations"] if c["id"] == conversation.id)
    assert summary["pendingWriteCount"] == 0
    assert {"id", "name", "avatar", "description", "isOrchestrator"} <= set(
        body["agents"][0].keys()
    )


async def test_snapshot_cors_preflight(mobile_client):
    resp = await mobile_client.options(
        "/api/mobile/snapshot", headers={"Origin": "capacitor://localhost"}
    )
    assert resp.status_code == 204
    assert resp.headers["access-control-allow-origin"] == "capacitor://localhost"
    assert "GET" in resp.headers["access-control-allow-methods"]


# ─── Conversation detail ────────────────────────────────────────────────────
async def test_conversation_detail_happy(mobile_client, conversation):
    resp = await mobile_client.get(
        f"/api/mobile/conversations/{conversation.id}", headers=AUTH
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["conversation"]["id"] == conversation.id
    assert body["conversation"]["agentIds"] == conversation.agent_ids
    assert any(p["type"] == "text" for m in body["messages"] for p in m["parts"])
    assert body["artifacts"] == []


async def test_conversation_detail_not_found(mobile_client):
    resp = await mobile_client.get(
        "/api/mobile/conversations/conv_missing", headers=AUTH
    )
    assert resp.status_code == 404
    assert "not found" in resp.json()["error"].lower()


# ─── Send message ───────────────────────────────────────────────────────────
async def test_send_message_happy(mobile_client, conversation):
    resp = await mobile_client.post(
        f"/api/mobile/conversations/{conversation.id}/messages",
        headers=AUTH,
        json={"content": "from mobile"},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert "messageId" in body and "runIds" in body


async def test_send_message_invalid_body(mobile_client, conversation):
    resp = await mobile_client.post(
        f"/api/mobile/conversations/{conversation.id}/messages",
        headers=AUTH,
        json={"content": ""},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "Invalid body"


async def test_send_message_conversation_not_found(mobile_client):
    resp = await mobile_client.post(
        "/api/mobile/conversations/conv_missing/messages",
        headers=AUTH,
        json={"content": "hi"},
    )
    assert resp.status_code == 400
    assert "error" in resp.json()


# ─── Edit message ───────────────────────────────────────────────────────────
async def test_edit_message_happy(mobile_client, conversation):
    msgs = await _list_messages(conversation.id)
    user_msg = next(m for m in msgs if m["role"] == "user")
    resp = await mobile_client.post(
        f"/api/mobile/conversations/{conversation.id}/messages/{user_msg['id']}/edit",
        headers=AUTH,
        json={"content": "edited content"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "deletedMessageIds" in body
    assert "newMessage" in body
    assert body["newMessage"]["parts"] == [{"type": "text", "content": "edited content"}]
    assert "runIds" in body


async def test_edit_message_not_found(mobile_client, conversation):
    resp = await mobile_client.post(
        f"/api/mobile/conversations/{conversation.id}/messages/msg_missing/edit",
        headers=AUTH,
        json={"content": "x"},
    )
    assert resp.status_code == 400


async def test_edit_message_invalid_body(mobile_client, conversation):
    resp = await mobile_client.post(
        f"/api/mobile/conversations/{conversation.id}/messages/whatever/edit",
        headers=AUTH,
        json={},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "Invalid body"


# ─── Withdraw message ───────────────────────────────────────────────────────
async def test_withdraw_message_happy(mobile_client, conversation):
    msgs = await _list_messages(conversation.id)
    user_msg = next(m for m in msgs if m["role"] == "user")
    resp = await mobile_client.post(
        f"/api/mobile/conversations/{conversation.id}/messages/{user_msg['id']}/withdraw",
        headers=AUTH,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert user_msg["id"] in body["deletedMessageIds"]
    assert "deletedArtifactIds" in body


async def test_withdraw_message_not_found(mobile_client, conversation):
    resp = await mobile_client.post(
        f"/api/mobile/conversations/{conversation.id}/messages/msg_missing/withdraw",
        headers=AUTH,
    )
    assert resp.status_code == 400


# ─── Regenerate ─────────────────────────────────────────────────────────────
async def test_regenerate_happy(mobile_client, conversation):
    resp = await mobile_client.post(
        f"/api/mobile/conversations/{conversation.id}/regenerate", headers=AUTH
    )
    assert resp.status_code == 202
    body = resp.json()
    assert "triggerMessageId" in body
    assert "runIds" in body


async def test_regenerate_not_found(mobile_client):
    resp = await mobile_client.post(
        "/api/mobile/conversations/conv_missing/regenerate", headers=AUTH
    )
    assert resp.status_code == 400


# ─── Artifact ───────────────────────────────────────────────────────────────
async def test_artifact_happy(mobile_client, conversation, agents):
    artifact_id = await _seed_artifact(conversation.id, agents["alice"])
    resp = await mobile_client.get(
        f"/api/mobile/artifacts/{artifact_id}", headers=AUTH
    )
    assert resp.status_code == 200
    art = resp.json()["artifact"]
    assert art["id"] == artifact_id
    assert art["conversationId"] == conversation.id
    assert art["content"]["markdown"] == "# Hi"
    assert art["createdByAgentId"] == agents["alice"]


async def test_artifact_not_found(mobile_client):
    resp = await mobile_client.get("/api/mobile/artifacts/art_missing", headers=AUTH)
    assert resp.status_code == 404
    assert "not found" in resp.json()["error"].lower()


# ─── Pending writes ─────────────────────────────────────────────────────────
async def test_pending_write_reject_happy(mobile_client, conversation, agents):
    from app.db.engine import get_db
    from app.db.models import Workspace
    from app.services.pending_writes import pending_writes

    async with get_db() as session:
        ws = (
            await session.execute(
                Workspace.__table__.select().where(
                    Workspace.conversation_id == conversation.id
                )
            )
        ).first()
    assert ws is not None
    workspace = Workspace(
        id=ws.id,
        conversation_id=ws.conversation_id,
        root_path=ws.root_path,
        mode=ws.mode,
        bound_path=ws.bound_path,
        created_at=ws.created_at,
    )
    write = pending_writes.register(
        conversation_id=conversation.id,
        agent_id=agents["alice"],
        run_id="run_1",
        path="a.txt",
        absolute_path="/tmp/a.txt",
        old_content=None,
        new_content="hi",
        workspace=workspace,
        skip_write=True,
    )
    resp = await mobile_client.post(
        f"/api/mobile/pending-writes/{write.id}",
        headers=AUTH,
        json={"action": "reject"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert pending_writes.get(write.id) is None


async def test_pending_write_not_found(mobile_client):
    resp = await mobile_client.post(
        "/api/mobile/pending-writes/pw_missing",
        headers=AUTH,
        json={"action": "approve"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"] == "Pending write not found"


async def test_pending_write_invalid_body(mobile_client):
    resp = await mobile_client.post(
        "/api/mobile/pending-writes/pw_x", headers=AUTH, json={"action": "nope"}
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "Invalid body"


# ─── Pending questions ──────────────────────────────────────────────────────
async def test_pending_question_answer_happy(mobile_client, conversation, agents):
    from app.schemas.dispatch import AskUserOption, AskUserQuestionItem
    from app.services.pending_questions import pending_questions

    q = pending_questions.register(
        conversation_id=conversation.id,
        agent_id=agents["alice"],
        run_id="run_1",
        questions=[
            AskUserQuestionItem(
                question="Pick one",
                header="Choice",
                options=[AskUserOption(label="A"), AskUserOption(label="B")],
                multi_select=False,
            )
        ],
    )
    resp = await mobile_client.post(
        f"/api/mobile/pending-questions/{q.id}",
        headers=AUTH,
        json={"answers": {"Pick one": {"selectedLabels": ["A"]}}},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert pending_questions.get(q.id) is None


async def test_pending_question_not_found(mobile_client):
    resp = await mobile_client.post(
        "/api/mobile/pending-questions/pq_missing",
        headers=AUTH,
        json={"answers": {"q": {"selectedLabels": []}}},
    )
    assert resp.status_code == 404
    assert resp.json()["error"] == "Pending question not found"


async def test_pending_question_invalid_body(mobile_client):
    resp = await mobile_client.post(
        "/api/mobile/pending-questions/pq_x", headers=AUTH, json={}
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "Invalid body"


# ─── helpers ────────────────────────────────────────────────────────────────
async def _list_messages(conversation_id: str):
    from app.services import conversation_service

    msgs = await conversation_service.list_messages(conversation_id)
    return [{"id": m.id, "role": m.role, "parts": m.parts} for m in msgs]
