"""Tests for the attachments API routes.

Covers:
- POST   /api/conversations/{id}/attachments  (multipart upload)
- GET    /api/conversations/{id}/attachments  (list)
- GET    /api/attachments/{id}                (serve bytes)
- DELETE /api/attachments/{id}                (remove)
"""

import pytest_asyncio


@pytest_asyncio.fixture
async def api_client(db):
    """An httpx AsyncClient over an app that includes the attachments router.

    The shared conftest `api_client` uses the integrated `create_app()`; until the
    Integrate stage wires `app.api.attachments`, this local fixture mounts just
    this router under `/api` against the same isolated `db` fixture so the routes
    are reachable in isolation.
    """
    import httpx
    from fastapi import FastAPI

    from app.api import attachments

    app = FastAPI()
    app.include_router(attachments.router, prefix="/api")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def conversation(agents):
    """Create a single-agent conversation (and its workspace) for attachments."""
    from app.services import conversation_service

    conv = await conversation_service.create_conversation(
        mode="single",
        agent_ids=[agents["alice"]],
        title="Attach test",
    )
    return conv.id


async def _upload(api_client, conv_id, *, name="hello.txt", content=b"hi there", mime="text/plain"):
    return await api_client.post(
        f"/api/conversations/{conv_id}/attachments",
        files={"file": (name, content, mime)},
    )


async def test_upload_returns_201_with_attachment(api_client, conversation):
    resp = await _upload(api_client, conversation)
    assert resp.status_code == 201
    body = resp.json()
    att = body["attachment"]
    assert att["conversationId"] == conversation
    assert att["fileName"] == "hello.txt"
    assert att["kind"] == "file"
    assert att["mimeType"] == "text/plain"
    assert att["size"] == len(b"hi there")
    assert att["filePath"].startswith("uploads/")
    assert "createdAt" in att and isinstance(att["createdAt"], int)


async def test_upload_image_kind(api_client, conversation):
    resp = await _upload(
        api_client, conversation, name="pic.png", content=b"\x89PNG\r\n", mime="image/png"
    )
    assert resp.status_code == 201
    assert resp.json()["attachment"]["kind"] == "image"


async def test_upload_missing_file_returns_400(api_client, conversation):
    resp = await api_client.post(
        f"/api/conversations/{conversation}/attachments",
        data={"notfile": "x"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"] == "Missing file"


async def test_upload_empty_file_returns_400(api_client, conversation):
    resp = await _upload(api_client, conversation, content=b"")
    assert resp.status_code == 400
    assert "error" in resp.json()


async def test_list_returns_uploaded(api_client, conversation):
    await _upload(api_client, conversation, name="a.txt")
    await _upload(api_client, conversation, name="b.txt")
    resp = await api_client.get(f"/api/conversations/{conversation}/attachments")
    assert resp.status_code == 200
    atts = resp.json()["attachments"]
    assert len(atts) == 2
    # Newest first.
    assert {a["fileName"] for a in atts} == {"a.txt", "b.txt"}


async def test_list_empty(api_client, conversation):
    resp = await api_client.get(f"/api/conversations/{conversation}/attachments")
    assert resp.status_code == 200
    assert resp.json() == {"attachments": []}


async def test_serve_file_returns_bytes(api_client, conversation):
    up = await _upload(api_client, conversation, name="doc.txt", content=b"payload")
    att_id = up.json()["attachment"]["id"]
    resp = await api_client.get(f"/api/attachments/{att_id}")
    assert resp.status_code == 200
    assert resp.content == b"payload"
    assert resp.headers["content-type"].startswith("text/plain")
    assert resp.headers["content-disposition"].startswith("attachment;")
    assert "doc.txt" in resp.headers["content-disposition"]
    assert resp.headers["cache-control"] == "private, max-age=3600"


async def test_serve_image_inline(api_client, conversation):
    up = await _upload(
        api_client, conversation, name="pic.png", content=b"\x89PNG", mime="image/png"
    )
    att_id = up.json()["attachment"]["id"]
    resp = await api_client.get(f"/api/attachments/{att_id}")
    assert resp.status_code == 200
    assert resp.headers["content-disposition"].startswith("inline;")


async def test_serve_not_found(api_client, conversation):
    resp = await api_client.get("/api/attachments/att_does_not_exist")
    assert resp.status_code == 404
    assert resp.json() == {"error": "Not found"}


async def test_serve_file_missing_on_disk_returns_410(api_client, conversation):
    import os

    from app.services import attachment_service

    up = await _upload(api_client, conversation, name="gone.txt", content=b"x")
    att_id = up.json()["attachment"]["id"]
    abs_path = await attachment_service.get_attachment_absolute_path(att_id)
    os.remove(abs_path)
    resp = await api_client.get(f"/api/attachments/{att_id}")
    assert resp.status_code == 410
    assert resp.json() == {"error": "File missing on disk"}


async def test_delete_returns_ok(api_client, conversation):
    up = await _upload(api_client, conversation)
    att_id = up.json()["attachment"]["id"]
    resp = await api_client.delete(f"/api/attachments/{att_id}")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    # Gone afterwards.
    assert (await api_client.get(f"/api/attachments/{att_id}")).status_code == 404


async def test_delete_not_found_returns_404(api_client, conversation):
    resp = await api_client.delete("/api/attachments/att_missing")
    assert resp.status_code == 404
    assert "error" in resp.json()
