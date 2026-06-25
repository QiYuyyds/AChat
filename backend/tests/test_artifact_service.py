"""Tests for artifact_service CRUD / version-chain / export helpers (阶段 6)."""

from __future__ import annotations

import io
import json
import zipfile

import pytest
import pytest_asyncio

from app.db.engine import get_db
from app.db.models import Artifact, Conversation, Workspace
from app.services import artifact_service as svc
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
            created_at=now_ms(),
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


# ─── list / get / delete ─────────────────────────────────────────────────────
async def test_list_artifacts_empty(db, agents):
    assert await svc.list_artifacts() == []


async def test_list_artifacts_joins_conversation_title(conv):
    aid = await _make_artifact(conv, title="Doc A")
    rows = await svc.list_artifacts()
    assert len(rows) == 1
    row = rows[0]
    assert row.id == aid
    assert row.conversation_title == "My Conversation"
    camel = row.to_camel()
    assert camel["conversationTitle"] == "My Conversation"
    assert camel["conversationId"] == conv
    assert camel["parentArtifactId"] is None


async def test_list_artifacts_orders_newest_first(conv):
    first = await _make_artifact(conv, title="first")
    # ensure a distinct, later created_at
    second = new_artifact_id()
    async with get_db() as db:
        art = Artifact(
            id=second,
            conversation_id=conv,
            type="document",
            title="second",
            version=1,
            parent_artifact_id=None,
            created_by_agent_id="ag_alice",
            created_at=now_ms() + 1000,
        )
        art.content_dict = {"type": "document", "format": "markdown", "content": "x"}
        db.add(art)
    rows = await svc.list_artifacts()
    assert [r.id for r in rows] == [second, first]


async def test_get_artifact_returns_camel_dict(conv):
    aid = await _make_artifact(conv, title="Doc")
    got = await svc.get_artifact(aid)
    assert got is not None
    assert got["id"] == aid
    assert got["conversationId"] == conv
    assert got["createdByAgentId"] == "ag_alice"
    assert got["content"] == {"type": "document", "format": "markdown", "content": "hello"}


async def test_get_artifact_missing(db, agents):
    assert await svc.get_artifact("ar_missing") is None


async def test_delete_artifact(conv):
    aid = await _make_artifact(conv)
    await svc.delete_artifact(aid)
    assert await svc.get_artifact(aid) is None


async def test_delete_artifact_missing_raises(db, agents):
    with pytest.raises(ValueError, match="Artifact not found"):
        await svc.delete_artifact("ar_missing")


# ─── version creation ────────────────────────────────────────────────────────
async def test_create_artifact_version_increments_and_links(conv):
    parent = await _make_artifact(conv, title="Original")
    res = await svc.create_artifact_version(
        parent, {"content": "new body"}, title="Edited"
    )
    assert res.ok is True
    assert res.artifact is not None
    assert res.artifact["version"] == 2
    assert res.artifact["parentArtifactId"] == parent
    assert res.artifact["title"] == "Edited"
    assert res.artifact["conversationId"] == conv
    assert res.artifact["createdByAgentId"] == "ag_alice"
    assert res.artifact["content"]["content"] == "new body"


async def test_create_artifact_version_inherits_parent_title_when_blank(conv):
    parent = await _make_artifact(conv, title="Keep Me")
    res = await svc.create_artifact_version(parent, {"content": "x"}, title="   ")
    assert res.ok is True
    assert res.artifact["title"] == "Keep Me"


async def test_create_artifact_version_parent_missing(db, agents):
    res = await svc.create_artifact_version("ar_missing", {"content": "x"})
    assert res.ok is False
    assert res.status == 404
    assert "not found" in res.error.lower()


async def test_create_artifact_version_invalid_content(conv):
    parent = await _make_artifact(conv, artifact_type="web_app", title="App",
                                  content={"type": "web_app",
                                           "files": {"index.html": "<p>hi</p>"},
                                           "entry": "index.html"})
    # web_app with a dict carrying no usable files/html/content/code → invalid
    res = await svc.create_artifact_version(parent, {"nope": 1})
    assert res.ok is False
    assert res.status == 400
    assert res.error


# ─── version chain ───────────────────────────────────────────────────────────
async def test_list_artifact_versions_full_chain(conv):
    v1 = await _make_artifact(conv, title="v1", version=1)
    v2 = await _make_artifact(conv, title="v2", version=2, parent_artifact_id=v1)
    v3 = await _make_artifact(conv, title="v3", version=3, parent_artifact_id=v2)

    # query from the middle node — should still return the whole chain ascending
    chain = await svc.list_artifact_versions(v2)
    assert chain is not None
    assert [a["id"] for a in chain] == [v1, v2, v3]
    assert [a["version"] for a in chain] == [1, 2, 3]


async def test_list_artifact_versions_single(conv):
    aid = await _make_artifact(conv, version=1)
    chain = await svc.list_artifact_versions(aid)
    assert [a["id"] for a in chain] == [aid]


async def test_list_artifact_versions_missing(db, agents):
    assert await svc.list_artifact_versions("ar_missing") is None


# ─── export serialisation ────────────────────────────────────────────────────
async def test_export_unsupported_mode(conv):
    aid = await _make_artifact(conv)
    out = await svc.serialize_artifact_export(aid, "weird")
    assert out.kind == "error"
    assert out.status == 400


async def test_export_missing(db, agents):
    out = await svc.serialize_artifact_export("ar_missing")
    assert out.kind == "error"
    assert out.status == 404


async def test_export_document_markdown(conv):
    aid = await _make_artifact(
        conv,
        title="My Doc",
        content={"type": "document", "format": "markdown", "content": "# Hi"},
    )
    out = await svc.serialize_artifact_export(aid)
    assert out.kind == "file"
    assert out.filename == "My_Doc-v1.md"
    assert out.content_type.startswith("text/markdown")
    assert out.body == b"# Hi"


async def test_export_web_app_zip(conv):
    aid = await _make_artifact(
        conv,
        artifact_type="web_app",
        title="Site",
        content={
            "type": "web_app",
            "files": {"index.html": "<h1>hi</h1>", "app.js": "console.log(1)"},
            "entry": "index.html",
        },
    )
    out = await svc.serialize_artifact_export(aid)
    assert out.kind == "file"
    assert out.filename == "Site-v1.zip"
    assert out.content_type == "application/zip"
    with zipfile.ZipFile(io.BytesIO(out.body)) as zf:
        names = set(zf.namelist())
        assert {"index.html", "app.js", "README.txt"} <= names
        assert zf.read("index.html").decode() == "<h1>hi</h1>"


async def test_export_image_redirect(conv):
    aid = await _make_artifact(
        conv,
        artifact_type="image",
        title="Pic",
        content={"type": "image", "url": "https://example.com/x.png", "alt": ""},
    )
    out = await svc.serialize_artifact_export(aid)
    assert out.kind == "redirect"
    assert out.redirect_url == "https://example.com/x.png"


async def test_export_diagram_mmd(conv):
    aid = await _make_artifact(
        conv,
        artifact_type="diagram",
        title="Flow",
        content={"type": "diagram", "syntax": "mermaid", "source": "graph TD\nA-->B"},
    )
    out = await svc.serialize_artifact_export(aid)
    assert out.kind == "file"
    assert out.filename == "Flow-v1.mmd"
    assert out.body == b"graph TD\nA-->B"


async def test_export_ppt_visual_not_supported(conv):
    aid = await _make_artifact(
        conv,
        artifact_type="ppt",
        title="Deck",
        content={"type": "ppt", "slides": [{"title": "S1"}]},
    )
    out = await svc.serialize_artifact_export(aid, "visual")
    assert out.kind == "error"
    assert out.status == 501


async def test_export_ppt_editable_deferred(conv):
    aid = await _make_artifact(
        conv,
        artifact_type="ppt",
        title="Deck",
        content={"type": "ppt", "slides": [{"title": "S1"}]},
    )
    out = await svc.serialize_artifact_export(aid)
    assert out.kind == "deferred"
    assert out.deferred_kind == "ppt"
    assert out.base_name == "Deck-v1"


async def test_export_project_deferred(conv):
    aid = await _make_artifact(
        conv,
        artifact_type="project",
        title="Proj",
        content={"type": "project", "files": [{"path": "a.ts", "sizeBytes": 1}]},
    )
    out = await svc.serialize_artifact_export(aid)
    assert out.kind == "deferred"
    assert out.deferred_kind == "project"


async def test_export_code_file_json_fallback(conv):
    content = {
        "type": "code_file",
        "workspacePath": "src/a.ts",
        "language": "typescript",
        "sizeBytes": 10,
        "checksum": "",
    }
    aid = await _make_artifact(
        conv, artifact_type="code_file", title="Code", content=content
    )
    out = await svc.serialize_artifact_export(aid)
    assert out.kind == "file"
    assert out.filename == "Code-v1.json"
    assert out.content_type == "application/json"
    assert json.loads(out.body.decode()) == content
