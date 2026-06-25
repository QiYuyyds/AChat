"""Tests for the deployment asset-serving route (GET /deployments/{id}/...).

The route serves the inlined preview site so the frontend's same-origin previewPath
(/deployments/{id}) resolves. read_deployment_asset reads AGENTHUB_DATA_DIR, which the
fixture points at a tmp dir.
"""

from __future__ import annotations

import httpx
import pytest
import pytest_asyncio

from app.services import deployment_service as ds


@pytest_asyncio.fixture
async def deployed(tmp_path, monkeypatch):
    data_dir = str(tmp_path / "data")
    monkeypatch.setenv("AGENTHUB_DATA_DIR", data_dir)
    ds.create_local_static_deployment(
        id="dep_routetest",
        artifact_id="art_1",
        title="Site",
        version=1,
        content={
            "type": "web_app",
            "files": {
                "index.html": "<h1>Hi</h1>",
                "style.css": "body{color:blue}",
            },
            "entry": "index.html",
        },
        created_at=1_700_000_000_000,
        data_dir=data_dir,
    )
    yield


async def _client():
    from app.main import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_serve_root_returns_index_html(deployed):
    async with await _client() as c:
        res = await c.get("/deployments/dep_routetest")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "<h1>Hi</h1>" in res.text


async def test_serve_specific_asset(deployed):
    async with await _client() as c:
        res = await c.get("/deployments/dep_routetest/style.css")
    assert res.status_code == 200
    assert "text/css" in res.headers["content-type"]
    assert "color:blue" in res.text


async def test_unknown_deployment_404(deployed):
    async with await _client() as c:
        res = await c.get("/deployments/dep_missing")
    assert res.status_code == 404


@pytest.mark.parametrize("bad", ["not-a-dep", "dep_routetest/../../etc"])
async def test_invalid_paths_rejected(deployed, bad):
    async with await _client() as c:
        res = await c.get(f"/deployments/{bad}")
    assert res.status_code in (400, 404)
