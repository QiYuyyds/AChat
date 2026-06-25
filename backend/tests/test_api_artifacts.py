"""Tests for the artifacts API router (phase 6).

Uses the `api_client` httpx fixture (over ASGITransport) plus the shared `db`
and `agents` fixtures. Each route is covered for the happy path plus at least
one error path, matching the TS HTTP contract.
"""

from __future__ import annotations

import io
import os
import zipfile
from urllib.parse import quote

import pytest_asyncio

from app.db.engine import get_db
from app.db.models import Artifact, Conversation, Workspace
from app.utils.clock import now_ms
from app.utils.ids import new_artifact_id, new_conversation_id, new_workspace_id


async def _make_conversation(title: str = "Conv") -> str:
    conv_id = new_conversation_id()
    async with get_db() as db:
        ts = now_ms()
        conv = Conversation(
            id=conv_id, title=title, mode="single", created_at=ts, updated_at=ts
        )
        conv.agent_ids_list = ["ag_alice"]
        conv.pinned_message_ids_list = []
        db.add(conv)
        ws = Workspace(
            id=new_workspace_id(),
            conversation_id=conv_id,
            mode="sandbox",
            root_path=f"/ws/{conv_id}",
            created_at=ts,
        )
        db.add(ws)
    return conv_id


async def _make_artifact(
    conversation_id: str,
    *,
    artifact_type: str = "document",
    title: str = "Doc",
    content: dict | None = None,
    version: int = 1,
    parent_artifact_id: str | None = None,
) -> str:
    aid = new_artifact_id()
    async with get_db() as db:
        art = Artifact(
            id=aid,
            conversation_id=conversation_id,
            type=artifact_type,
            title=title,
            version=version,
            parent_artifact_id=parent_artifact_id,
            created_by_agent_id="ag_alice",
            created_at=now_ms(),
        )
        art.content_dict = content or {
            "type": "document",
            "format": "markdown",
            "content": "hello",
        }
        db.add(art)
    return aid


@pytest_asyncio.fixture
async def conv(db, agents):
    return await _make_conversation("My Conversation")


# ─── GET /api/artifacts ──────────────────────────────────────────────────────
async def test_list_artifacts(api_client, conv):
    await _make_artifact(conv, title="A")
    await _make_artifact(conv, title="B")

    resp = await api_client.get("/api/artifacts")
    assert resp.status_code == 200
    body = resp.json()
    assert "artifacts" in body
    titles = {a["title"] for a in body["artifacts"]}
    assert {"A", "B"} <= titles
    # camelCase wire shape, joined conversation title
    first = body["artifacts"][0]
    assert "conversationId" in first
    assert first["conversationTitle"] == "My Conversation"
    assert "createdAt" in first


async def test_list_artifacts_empty(api_client, db, agents):
    resp = await api_client.get("/api/artifacts")
    assert resp.status_code == 200
    assert resp.json() == {"artifacts": []}


# ─── GET /api/artifacts/{id} ─────────────────────────────────────────────────
async def test_get_artifact(api_client, conv):
    aid = await _make_artifact(conv, title="Mine")
    resp = await api_client.get(f"/api/artifacts/{aid}")
    assert resp.status_code == 200
    art = resp.json()["artifact"]
    assert art["id"] == aid
    assert art["title"] == "Mine"
    assert art["content"]["type"] == "document"


async def test_get_artifact_not_found(api_client, db, agents):
    resp = await api_client.get("/api/artifacts/art_missing")
    assert resp.status_code == 404
    assert resp.json() == {"error": "Not found"}


# ─── DELETE /api/artifacts/{id} ──────────────────────────────────────────────
async def test_delete_artifact(api_client, conv):
    aid = await _make_artifact(conv)
    resp = await api_client.delete(f"/api/artifacts/{aid}")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    # really gone
    assert (await api_client.get(f"/api/artifacts/{aid}")).status_code == 404


async def test_delete_artifact_not_found(api_client, db, agents):
    resp = await api_client.delete("/api/artifacts/art_missing")
    assert resp.status_code == 404
    assert "error" in resp.json()


# ─── GET /api/artifacts/{id}/versions ────────────────────────────────────────
async def test_list_versions(api_client, conv):
    v1 = await _make_artifact(conv, title="Chain", version=1)
    v2 = await _make_artifact(
        conv, title="Chain", version=2, parent_artifact_id=v1
    )

    resp = await api_client.get(f"/api/artifacts/{v2}/versions")
    assert resp.status_code == 200
    versions = resp.json()["versions"]
    ids = [v["id"] for v in versions]
    # full chain, ascending by version, regardless of which member we asked for
    assert ids == [v1, v2]


async def test_list_versions_not_found(api_client, db, agents):
    resp = await api_client.get("/api/artifacts/art_missing/versions")
    assert resp.status_code == 404
    assert resp.json() == {"error": "Artifact not found"}


# ─── POST /api/artifacts/{id}/versions ───────────────────────────────────────
async def test_create_version(api_client, conv):
    parent = await _make_artifact(conv, title="Editable", version=1)
    resp = await api_client.post(
        f"/api/artifacts/{parent}/versions",
        json={
            "content": {"type": "document", "format": "markdown", "content": "v2"},
            "title": "Editable v2",
        },
    )
    assert resp.status_code == 200
    art = resp.json()["artifact"]
    assert art["version"] == 2
    assert art["parentArtifactId"] == parent
    assert art["title"] == "Editable v2"


async def test_create_version_parent_missing(api_client, db, agents):
    resp = await api_client.post(
        "/api/artifacts/art_missing/versions",
        json={"content": {"type": "document", "format": "markdown", "content": "x"}},
    )
    assert resp.status_code == 404
    assert "error" in resp.json()


async def test_create_version_invalid_content(api_client, conv):
    parent = await _make_artifact(conv, title="P", version=1)
    resp = await api_client.post(
        f"/api/artifacts/{parent}/versions",
        json={"content": {"type": "not_a_real_type"}},
    )
    assert resp.status_code == 400
    assert "error" in resp.json()


# ─── GET /api/artifacts/{id}/export ──────────────────────────────────────────
async def test_export_document(api_client, conv):
    aid = await _make_artifact(
        conv,
        artifact_type="document",
        title="My Doc",
        content={"type": "document", "format": "markdown", "content": "# Hi"},
    )
    resp = await api_client.get(f"/api/artifacts/{aid}/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    assert "attachment; filename=" in resp.headers["content-disposition"]
    assert resp.text == "# Hi"


async def test_export_web_app_zip(api_client, conv):
    aid = await _make_artifact(
        conv,
        artifact_type="web_app",
        title="Site",
        content={
            "type": "web_app",
            "entry": "index.html",
            "files": {"index.html": "<h1>hi</h1>"},
        },
    )
    resp = await api_client.get(f"/api/artifacts/{aid}/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = set(zf.namelist())
    assert "index.html" in names
    assert "README.txt" in names


async def test_export_image_redirect(api_client, conv):
    aid = await _make_artifact(
        conv,
        artifact_type="image",
        title="Pic",
        content={"type": "image", "url": "https://example.com/x.png"},
    )
    resp = await api_client.get(f"/api/artifacts/{aid}/export", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "https://example.com/x.png"


async def test_export_bad_mode(api_client, conv):
    aid = await _make_artifact(conv)
    resp = await api_client.get(f"/api/artifacts/{aid}/export?mode=bogus")
    assert resp.status_code == 400
    assert "error" in resp.json()


async def test_export_not_found(api_client, db, agents):
    resp = await api_client.get("/api/artifacts/art_missing/export")
    assert resp.status_code == 404
    assert "error" in resp.json()


async def test_export_filename_urlencoded(api_client, conv):
    aid = await _make_artifact(
        conv,
        artifact_type="document",
        title="名字 with space",
        content={"type": "document", "format": "markdown", "content": "x"},
    )
    resp = await api_client.get(f"/api/artifacts/{aid}/export")
    cd = resp.headers["content-disposition"]
    # base name is url-encoded, the .md extension stays literal
    assert cd.endswith('.md"')
    assert quote("名字_with_space", safe="") in cd


async def test_export_project_zip(api_client, conv, monkeypatch):
    # The project export zips live files from the workspace's effective cwd.
    # Point the workspace at a real tmp dir with one file present.
    import tempfile

    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, "main.py"), "w", encoding="utf-8") as fh:
        fh.write("print('hi')\n")

    # bind the conversation's workspace to the tmp dir (local mode → effective cwd)
    async with get_db() as db:
        from sqlalchemy import select

        ws = (
            await db.execute(
                select(Workspace).where(Workspace.conversation_id == conv)
            )
        ).scalar_one()
        ws.mode = "local"
        ws.bound_path = tmp

    aid = await _make_artifact(
        conv,
        artifact_type="project",
        title="Proj",
        content={
            "type": "project",
            "files": [{"path": "main.py", "sizeBytes": 12}],
        },
    )
    resp = await api_client.get(f"/api/artifacts/{aid}/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = set(zf.namelist())
    assert "main.py" in names
    assert "README.txt" in names


# ─── GET /api/artifacts/{id}/preview ─────────────────────────────────────────
async def test_preview_web_app(api_client, conv):
    aid = await _make_artifact(
        conv,
        artifact_type="web_app",
        title="Site",
        content={
            "type": "web_app",
            "entry": "index.html",
            "files": {"index.html": "<h1>preview me</h1>"},
        },
    )
    resp = await api_client.get(f"/api/artifacts/{aid}/preview")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "sandbox allow-scripts" in resp.headers["content-security-policy"]
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert "preview me" in resp.text


async def test_preview_non_web_app(api_client, conv):
    aid = await _make_artifact(conv, artifact_type="document")
    resp = await api_client.get(f"/api/artifacts/{aid}/preview")
    assert resp.status_code == 400
    assert resp.json() == {"error": "Artifact is not a web_app"}


async def test_preview_not_found(api_client, db, agents):
    resp = await api_client.get("/api/artifacts/art_missing/preview")
    assert resp.status_code == 404
    assert resp.json() == {"error": "Artifact not found"}
