"""Tests for the fs API routes (app/api/fs.py).

Covers the workspace fs read/write/listdir routes and the global DirPicker
listdir, including the sandbox-error -> HTTP-status mapping mirrored from the
TS routes.
"""

import os

import pytest_asyncio

from app.services import conversation_service


@pytest_asyncio.fixture
async def api_client(db):
    """Client over an app that includes the fs router.

    main.py wiring of `app.api.fs` belongs to the Integrate stage, so until it
    lands the shared conftest app has no fs routes. This module-local fixture
    shadows the conftest one and mounts the fs router under /api so the routes
    are exercised exactly as they will be in production.
    """
    import httpx
    from fastapi import FastAPI

    from app.api import fs

    app = FastAPI()
    app.include_router(fs.router, prefix="/api", tags=["fs"])
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest_asyncio.fixture
async def conversation(agents):
    """A single-agent conversation with a sandbox workspace under the test root."""
    conv = await conversation_service.create_conversation(
        mode="single",
        agent_ids=[agents["alice"]],
        title="fs test",
    )
    return conv.id


# --- /conversations/{id}/fs/write + read --------------------------------------


async def test_write_then_read_roundtrip(api_client, conversation):
    write = await api_client.post(
        f"/api/conversations/{conversation}/fs/write",
        json={"path": "notes/hello.txt", "content": "hi there"},
    )
    assert write.status_code == 200
    wbody = write.json()
    assert wbody["path"] == "notes/hello.txt"
    assert wbody["bytes"] == len(b"hi there")
    assert "absolutePath" in wbody and "cwd" in wbody

    read = await api_client.get(
        f"/api/conversations/{conversation}/fs/read",
        params={"path": "notes/hello.txt"},
    )
    assert read.status_code == 200
    rbody = read.json()
    assert rbody["content"] == "hi there"
    assert rbody["path"] == "notes/hello.txt"
    assert rbody["truncated"] is False
    assert rbody["size"] == len(b"hi there")


async def test_write_invalid_body_returns_400(api_client, conversation):
    resp = await api_client.post(
        f"/api/conversations/{conversation}/fs/write",
        json={"content": "missing path"},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"] == "Invalid body"
    assert "issues" in body


async def test_write_missing_workspace_returns_404(api_client):
    resp = await api_client.post(
        "/api/conversations/nope/fs/write",
        json={"path": "a.txt", "content": "x"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"] == "Workspace not found"


async def test_write_path_escape_returns_403(api_client, conversation):
    resp = await api_client.post(
        f"/api/conversations/{conversation}/fs/write",
        json={"path": "../escape.txt", "content": "x"},
    )
    assert resp.status_code == 403
    assert "outside" in resp.json()["error"]


# --- /conversations/{id}/fs/read ----------------------------------------------


async def test_read_missing_path_param_returns_400(api_client, conversation):
    resp = await api_client.get(f"/api/conversations/{conversation}/fs/read")
    assert resp.status_code == 400
    assert resp.json()["error"] == "path required"


async def test_read_not_a_file_returns_400(api_client, conversation):
    # Create a directory, then try to read it as a file.
    await api_client.post(
        f"/api/conversations/{conversation}/fs/write",
        json={"path": "adir/inner.txt", "content": "x"},
    )
    resp = await api_client.get(
        f"/api/conversations/{conversation}/fs/read",
        params={"path": "adir"},
    )
    assert resp.status_code == 400
    assert "Not a file" in resp.json()["error"]


async def test_read_missing_workspace_returns_404(api_client):
    resp = await api_client.get(
        "/api/conversations/nope/fs/read", params={"path": "a.txt"}
    )
    assert resp.status_code == 404


# --- /conversations/{id}/fs/listdir -------------------------------------------


async def test_listdir_workspace_root(api_client, conversation):
    await api_client.post(
        f"/api/conversations/{conversation}/fs/write",
        json={"path": "sub/file.txt", "content": "data"},
    )
    resp = await api_client.get(f"/api/conversations/{conversation}/fs/listdir")
    assert resp.status_code == 200
    body = resp.json()
    assert body["relPath"] == ""
    assert body["parent"] is None
    names = {e["name"]: e for e in body["entries"]}
    assert "sub" in names
    assert names["sub"]["isDirectory"] is True


async def test_listdir_not_a_directory_returns_400(api_client, conversation):
    await api_client.post(
        f"/api/conversations/{conversation}/fs/write",
        json={"path": "afile.txt", "content": "x"},
    )
    resp = await api_client.get(
        f"/api/conversations/{conversation}/fs/listdir",
        params={"path": "afile.txt"},
    )
    assert resp.status_code == 400
    assert "Not a" in resp.json()["error"]


async def test_listdir_missing_workspace_returns_404(api_client):
    resp = await api_client.get("/api/conversations/nope/fs/listdir")
    assert resp.status_code == 404


# --- /fs/listdir (global DirPicker) -------------------------------------------


async def test_global_listdir_lists_subdirs(api_client, tmp_path):
    base = tmp_path / "picker_base"
    (base / "child_a").mkdir(parents=True)
    (base / "child_b").mkdir()
    (base / ".hidden").mkdir()
    (base / "a_file.txt").write_text("x")

    resp = await api_client.get("/api/fs/listdir", params={"path": str(base)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["path"] == os.path.abspath(str(base))
    names = [e["name"] for e in body["entries"]]
    assert names == ["child_a", "child_b"]  # sorted, dirs only, no dotfiles/files
    assert all(e["isDirectory"] for e in body["entries"])


async def test_global_listdir_relative_path_returns_400(api_client):
    resp = await api_client.get("/api/fs/listdir", params={"path": "relative/path"})
    assert resp.status_code == 400
    assert resp.json()["error"] == "path must be absolute"


async def test_global_listdir_nonexistent_returns_404(api_client, tmp_path):
    missing = tmp_path / "does_not_exist_here"
    resp = await api_client.get("/api/fs/listdir", params={"path": str(missing)})
    assert resp.status_code == 404
    assert resp.json()["error"] == "Path does not exist"


async def test_global_listdir_file_returns_400(api_client, tmp_path):
    f = tmp_path / "plain.txt"
    f.write_text("x")
    resp = await api_client.get("/api/fs/listdir", params={"path": str(f)})
    assert resp.status_code == 400
    assert resp.json()["error"] == "Not a directory"
