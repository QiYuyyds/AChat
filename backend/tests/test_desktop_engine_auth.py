"""Tests for desktop local-engine token/origin middleware."""

from __future__ import annotations

import os

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.desktop.middleware import EngineAuthMiddleware
from app.desktop.runtime import (
    DesktopRuntime,
    assert_loopback_bind,
    set_desktop_runtime,
)


async def _ok(_request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


async def _healthz(_request: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


def _app() -> Starlette:
    app = Starlette(
        routes=[
            Route("/healthz", _healthz),
            Route("/api/runs", _ok, methods=["POST"]),
        ]
    )
    app.add_middleware(EngineAuthMiddleware)
    return app


@pytest.fixture()
def desktop_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ACHAT_RUNTIME", "desktop")
    monkeypatch.setenv("ACHAT_ENGINE_TOKEN", "test-token-abc")
    monkeypatch.setenv("ACHAT_ALLOWED_ORIGINS", "https://app.example.com")
    rt = DesktopRuntime(
        bind="127.0.0.1",
        port=0,
        data_dir=tmp_path,
        engine_token="test-token-abc",
        official_api_url="https://api.example.com",
        allowed_origins=["https://app.example.com"],
    )
    set_desktop_runtime(rt)
    yield rt
    # clear module runtime
    import app.desktop.runtime as runtime_mod

    runtime_mod._RUNTIME = None
    for key in (
        "ACHAT_RUNTIME",
        "ACHAT_ENGINE_TOKEN",
        "ACHAT_ALLOWED_ORIGINS",
        "ACHAT_OFFICIAL_API_URL",
        "ACHAT_DATA_DIR",
    ):
        os.environ.pop(key, None)


def test_rejects_missing_token(desktop_env):
    client = TestClient(_app())
    res = client.post(
        "/api/runs",
        headers={"Origin": "https://app.example.com"},
    )
    assert res.status_code == 401


def test_rejects_bad_origin(desktop_env):
    client = TestClient(_app())
    res = client.post(
        "/api/runs",
        headers={
            "Origin": "https://evil.example",
            "X-Engine-Token": "test-token-abc",
        },
    )
    assert res.status_code == 403


def test_accepts_valid_token_and_origin(desktop_env):
    client = TestClient(_app())
    res = client.post(
        "/api/runs",
        headers={
            "Origin": "https://app.example.com",
            "X-Engine-Token": "test-token-abc",
        },
    )
    assert res.status_code == 200
    assert res.json()["ok"] is True


def test_healthz_public(desktop_env):
    client = TestClient(_app())
    res = client.get("/healthz")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_loopback_bind_guard():
    assert_loopback_bind("127.0.0.1")
    with pytest.raises(SystemExit):
        assert_loopback_bind("0.0.0.0")


def test_data_dir_layout(tmp_path):
    rt = DesktopRuntime(data_dir=tmp_path / "AChat")
    rt.ensure_layout()
    for name in ("logs", "sqlite", "runtime", "workspaces"):
        assert (rt.data_dir / name).is_dir()
    path = rt.write_handshake(54321, 99)
    assert path.is_file()
    assert "54321" in path.read_text(encoding="utf-8")
