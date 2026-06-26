"""Tests for the documents API routes.

Covers:
- GET    /api/documents                   (list)
- POST   /api/documents                   (create / update)
- GET    /api/documents/{id}              (get document + latest version)
- GET    /api/documents/{id}/versions     (version history)
- DELETE /api/documents/{id}              (soft-delete)
- POST   /api/documents/{id}/ingest       (ingest to RAG)
- POST   /api/documents/upload            (upload file one-stop)
"""

import pytest_asyncio


@pytest_asyncio.fixture
async def api_client(db):
    """An httpx AsyncClient with documents router and DocumentService initialized."""
    import httpx
    from fastapi import FastAPI

    from app.api import documents
    from app.services.document_service import DocumentService

    # Initialize DocumentService with test DB and no RAG (rag=None)
    import app.main as main_mod
    main_mod._document_service = DocumentService(db=None, rag=None)

    app = FastAPI()
    app.include_router(documents.router, prefix="/api")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    main_mod._document_service = None


async def test_create_and_list_document(api_client):
    """POST /documents creates a document; GET /documents lists it."""
    # Create
    resp = await api_client.post("/api/documents", json={
        "title": "测试文档",
        "docType": "note",
        "contentMd": "# 测试\n这是内容",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] is True
    doc = body["document"]
    assert doc["title"] == "测试文档"
    assert doc["docType"] == "note"
    assert doc["status"] == "active"
    assert doc["latestVersion"] == 1
    ver = body["version"]
    assert ver["version"] == 1
    assert ver["contentMd"] == "# 测试\n这是内容"
    doc_id = doc["id"]

    # List
    resp = await api_client.get("/api/documents")
    assert resp.status_code == 200
    docs = resp.json()["documents"]
    assert len(docs) == 1
    assert docs[0]["id"] == doc_id
    assert docs[0]["title"] == "测试文档"


async def test_update_creates_new_version(api_client):
    """POST /documents with existing document_id creates a new version."""
    # Create v1
    resp = await api_client.post("/api/documents", json={
        "title": "版本测试",
        "contentMd": "版本1内容",
    })
    doc_id = resp.json()["document"]["id"]
    assert resp.json()["version"]["version"] == 1

    # Update → v2
    resp = await api_client.post("/api/documents", json={
        "documentId": doc_id,
        "title": "版本测试",
        "contentMd": "版本2内容",
        "summary": "更新了内容",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] is False
    assert body["version"]["version"] == 2
    assert body["document"]["latestVersion"] == 2

    # List versions
    resp = await api_client.get(f"/api/documents/{doc_id}/versions")
    assert resp.status_code == 200
    versions = resp.json()["versions"]
    assert len(versions) == 2
    assert versions[0]["version"] == 2  # DESC order
    assert versions[1]["version"] == 1


async def test_get_document_detail(api_client):
    """GET /documents/{id} returns document + latest version."""
    resp = await api_client.post("/api/documents", json={
        "title": "详情测试",
        "contentMd": "详情内容",
    })
    doc_id = resp.json()["document"]["id"]

    resp = await api_client.get(f"/api/documents/{doc_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["document"]["id"] == doc_id
    assert body["version"]["contentMd"] == "详情内容"


async def test_delete_document_soft_delete(api_client):
    """DELETE /documents/{id} soft-deletes the document."""
    resp = await api_client.post("/api/documents", json={
        "title": "删除测试",
        "contentMd": "待删除",
    })
    doc_id = resp.json()["document"]["id"]

    # Delete
    resp = await api_client.delete(f"/api/documents/{doc_id}")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # List should not include deleted doc
    resp = await api_client.get("/api/documents")
    docs = resp.json()["documents"]
    assert len(docs) == 0

    # Versions should still exist
    resp = await api_client.get(f"/api/documents/{doc_id}/versions")
    assert resp.status_code == 200
    assert len(resp.json()["versions"]) == 1


async def test_upload_text_file_one_stop(api_client):
    """POST /documents/upload parses text file and creates document."""
    content = b"# uploaded doc\nThis is a test document."
    resp = await api_client.post(
        "/api/documents/upload",
        files={"file": ("test.md", content, "text/markdown")},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["parser"] == "plain_text"
    assert body["needsOcr"] is False
    assert body["document"]["title"] == "test.md"
    assert body["document"]["source"] == "user_upload"
    assert body["version"]["contentMd"].startswith("# uploaded doc")


async def test_upload_empty_file_returns_error(api_client):
    """Empty file should raise ValueError."""
    resp = await api_client.post(
        "/api/documents/upload",
        files={"file": ("empty.txt", b"", "text/plain")},
    )
    assert resp.status_code == 400


async def test_get_nonexistent_document_returns_404(api_client):
    """GET /documents/nonexistent returns 404."""
    resp = await api_client.get("/api/documents/doc_nonexistent")
    assert resp.status_code == 404


async def test_ingest_version_without_rag(api_client):
    """POST /documents/{id}/ingest works even without RAG (returns 0 chunks)."""
    resp = await api_client.post("/api/documents", json={
        "title": "入库测试",
        "contentMd": "入库内容",
    })
    doc_id = resp.json()["document"]["id"]
    version_id = resp.json()["version"]["id"]

    resp = await api_client.post(
        f"/api/documents/{doc_id}/ingest",
        json={"versionId": version_id},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["versionId"] == version_id
    assert body["chunkCount"] == 0  # No RAG service in test
    assert len(body["docHash"]) == 16
