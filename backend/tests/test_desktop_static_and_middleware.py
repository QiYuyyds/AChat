"""Static UI exemptions + loopback origin for desktop middleware."""

from __future__ import annotations

import os

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.desktop.middleware import EngineAuthMiddleware
from app.desktop.runtime import DesktopRuntime, set_desktop_runtime


async def _ok(_request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


async def _index(_request: Request) -> PlainTextResponse:
    return PlainTextResponse("<html>ui</html>")


def _app() -> Starlette:
    app = Starlette(
        routes=[
            Route("/healthz", _ok),
            Route("/", _index),
            Route("/app/settings", _index),
            Route("/api/agents", _ok, methods=["GET"]),
        ]
    )
    app.add_middleware(EngineAuthMiddleware)
    return app


@pytest.fixture()
def desktop_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ACHAT_RUNTIME", "desktop")
    monkeypatch.setenv("ACHAT_ENGINE_TOKEN", "tok")
    monkeypatch.setenv("ACHAT_ALLOWED_ORIGINS", "http://127.0.0.1:9999")
    rt = DesktopRuntime(
        bind="127.0.0.1",
        port=9999,
        data_dir=tmp_path,
        engine_token="tok",
        allowed_origins=["http://127.0.0.1:9999"],
    )
    set_desktop_runtime(rt)
    yield rt
    import app.desktop.runtime as runtime_mod

    runtime_mod._RUNTIME = None
    for key in (
        "ACHAT_RUNTIME",
        "ACHAT_ENGINE_TOKEN",
        "ACHAT_ALLOWED_ORIGINS",
        "ACHAT_DATA_DIR",
    ):
        os.environ.pop(key, None)


def test_static_get_without_token(desktop_env):
    client = TestClient(_app())
    res = client.get("/")
    assert res.status_code == 200
    res2 = client.get("/app/settings")
    assert res2.status_code == 200


def test_api_still_needs_token(desktop_env):
    client = TestClient(_app())
    res = client.get("/api/agents", headers={"Origin": "http://127.0.0.1:9999"})
    assert res.status_code == 401
    res_ok = client.get(
        "/api/agents",
        headers={"Origin": "http://127.0.0.1:9999", "X-Engine-Token": "tok"},
    )
    assert res_ok.status_code == 200


def test_loopback_origin_allowed_even_if_not_listed(desktop_env, monkeypatch):
    monkeypatch.setenv("ACHAT_ALLOWED_ORIGINS", "https://remote.example")
    client = TestClient(_app())
    res = client.get(
        "/api/agents",
        headers={"Origin": "http://127.0.0.1:4242", "X-Engine-Token": "tok"},
    )
    assert res.status_code == 200
