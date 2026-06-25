"""Tests for deployment asset serving + zip downloads (阶段 6)."""

from __future__ import annotations

import io
import zipfile

import pytest

from app.services import deployment_service as ds


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    data_dir = str(tmp_path / "data")
    monkeypatch.setenv("AGENTHUB_DATA_DIR", data_dir)
    record = ds.create_local_static_deployment(
        id="dep_abc123",
        artifact_id="art_1",
        title="My Site",
        version=2,
        content={
            "type": "web_app",
            "files": {
                "index.html": "<h1>Hello</h1>",
                "style.css": "body{color:red}",
            },
            "entry": "index.html",
        },
        created_at=1_700_000_000_000,
        data_dir=data_dir,
    )
    return record, data_dir


# ─── read_deployment_asset ────────────────────────────────────────────────────
def test_read_asset_default_runtime_entry(deployment):
    _, data_dir = deployment
    res = ds.read_deployment_asset("dep_abc123", None, data_dir)
    assert res.ok
    assert res.content_type == "text/html; charset=utf-8"
    assert b"Hello" in res.body
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    assert res.headers["Cache-Control"] == "no-store"
    assert "sandbox allow-scripts" in res.headers["Content-Security-Policy"]


def test_read_asset_specific_file(deployment):
    _, data_dir = deployment
    res = ds.read_deployment_asset("dep_abc123", ["style.css"], data_dir)
    assert res.ok
    assert res.content_type == "text/css; charset=utf-8"
    assert res.body == b"body{color:red}"
    assert "Content-Security-Policy" not in res.headers


def test_read_asset_invalid_deployment_id(deployment):
    _, data_dir = deployment
    res = ds.read_deployment_asset("not-a-dep", None, data_dir)
    assert not res.ok
    assert res.status == 404
    assert res.error == "Deployment not found"


def test_read_asset_unknown_deployment(deployment):
    _, data_dir = deployment
    res = ds.read_deployment_asset("dep_missing", None, data_dir)
    assert not res.ok
    assert res.status == 404
    assert res.error == "Deployment not found"


def test_read_asset_missing_file(deployment):
    _, data_dir = deployment
    res = ds.read_deployment_asset("dep_abc123", ["nope.js"], data_dir)
    assert not res.ok
    assert res.status == 404
    assert res.error == "Deployment asset not found"


def test_read_asset_private_dir_blocked(deployment):
    _, data_dir = deployment
    res = ds.read_deployment_asset("dep_abc123", [".agenthub", "manifest.json"], data_dir)
    assert not res.ok
    assert res.status == 404
    assert res.error == "Deployment asset not found"


def test_read_asset_traversal_rejected(deployment):
    _, data_dir = deployment
    res = ds.read_deployment_asset("dep_abc123", ["..", "evil.txt"], data_dir)
    assert not res.ok
    assert res.status == 400
    assert res.error == "Invalid deployment path"


# ─── build_deployment_source_zip ──────────────────────────────────────────────
def test_source_zip(deployment):
    record, data_dir = deployment
    dl = ds.build_deployment_source_zip("dep_abc123", data_dir)
    assert dl is not None
    assert dl.content_type == "application/zip"
    assert dl.file_name == "My_Site-v2-dep_abc123-source.zip"
    with zipfile.ZipFile(io.BytesIO(dl.body)) as zf:
        names = set(zf.namelist())
        assert names == {"index.html", "style.css", "README.txt"}
        assert zf.read("index.html") == b"<h1>Hello</h1>"
        readme = zf.read("README.txt").decode()
        assert "Artifact: My Site" in readme
        assert "Version: v2" in readme
        assert "2023-11-14T22:13:20.000Z" in readme


def test_source_zip_unknown_deployment(deployment):
    _, data_dir = deployment
    assert ds.build_deployment_source_zip("dep_missing", data_dir) is None


# ─── build_deployment_container_zip ───────────────────────────────────────────
def test_container_zip(deployment):
    _, data_dir = deployment
    dl = ds.build_deployment_container_zip("dep_abc123", data_dir)
    assert dl is not None
    assert dl.content_type == "application/zip"
    assert dl.file_name == "My_Site-v2-dep_abc123-container.zip"
    with zipfile.ZipFile(io.BytesIO(dl.body)) as zf:
        names = set(zf.namelist())
        assert "Dockerfile" in names
        assert "nginx.conf" in names
        assert "README.txt" in names
        assert "app/index.html" in names
        assert "app/style.css" in names
        # private dir must not leak into the container bundle
        assert not any(n.startswith("app/.agenthub") for n in names)
        assert "nginx:1.27-alpine" in zf.read("Dockerfile").decode()
        container_readme = zf.read("README.txt").decode()
        assert "docker build -t agenthub-dep_abc123" in container_readme


def test_container_zip_unknown_deployment(deployment):
    _, data_dir = deployment
    assert ds.build_deployment_container_zip("dep_missing", data_dir) is None
