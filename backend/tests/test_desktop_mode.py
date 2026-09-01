"""桌面模式（AGENTHUB_DESKTOP=1）行为测试 — add-desktop-runtime 任务 4.2 / 4.6。

覆盖：
- 固定本地用户：受保护端点无 JWT 也以本地用户通过（web 模式 401 语义不变）
- /api/desktop/session 缓存标记探测
- /api/auth/* 云端代理：登录 2xx 写 cloud_session.json、Set-Cookie 去 Domain 透传、
  登出清标记、云端不可达 → 503 明确报错 / 登出仍清本地状态
"""

import json
from pathlib import Path

import httpx
import pytest

from app.api import auth_proxy

_TEST_JWT_SECRET = "test-secret-at-least-32-characters-long!!"


def _mock_cloud(handler):
    """构造 MockTransport 并注入代理模块。"""
    transport = httpx.MockTransport(handler)
    auth_proxy.set_test_transport(transport)
    return transport


@pytest.mark.asyncio
async def test_desktop_protected_endpoint_without_jwt_uses_local_user(desktop_client):
    """桌面模式：无 JWT 请求受保护端点 → 200（固定本地用户），MUST NOT 401。"""
    resp = await desktop_client.get("/api/conversations")
    assert resp.status_code == 200

    from app.db.engine import get_db
    from app.db.models import User

    async with get_db() as session:
        from sqlalchemy import select

        user = (
            await session.execute(select(User).where(User.id == "local_desktop_user"))
        ).scalar_one_or_none()
        assert user is not None


@pytest.mark.asyncio
async def test_web_mode_without_jwt_still_401(monkeypatch, tmp_path):
    """web 模式回归：同端点无 JWT → 401（桌面例外不得外溢）。"""
    db_file = tmp_path / "web.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_file.as_posix()}")
    monkeypatch.setenv("DATABASE_LOCAL_URL", "")
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    monkeypatch.setenv("JWT_SECRET", _TEST_JWT_SECRET)
    monkeypatch.delenv("AGENTHUB_DESKTOP", raising=False)

    from app.config import get_settings

    get_settings.cache_clear()
    from app.db import engine as engine_mod

    await engine_mod.init_db()
    try:
        import app.services.agent_runner  # noqa: F401
        from app.main import create_app

        app = create_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/conversations")
            assert resp.status_code == 401
    finally:
        await engine_mod.close_db()
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_desktop_session_endpoint_reflects_marker(desktop_client, desktop_env):
    resp = await desktop_client.get("/api/desktop/session")
    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "desktop"
    assert data["loggedIn"] is False
    assert data["user"] is None

    marker = Path(desktop_env) / "cloud_session.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"email": "u@x.com", "name": "U", "loggedInAt": 1}), encoding="utf-8")

    resp = await desktop_client.get("/api/desktop/session")
    data = resp.json()
    assert data["loggedIn"] is True
    assert data["user"]["email"] == "u@x.com"


@pytest.mark.asyncio
async def test_proxy_login_writes_marker_and_strips_cookie_domain(desktop_client, desktop_env):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "cloud.example.com"
        assert request.url.path == "/api/auth/login"
        return httpx.Response(
            200,
            json={"user": {"id": "cu1", "email": "u@x.com", "name": "U"}, "tokens": {"access_token": "t"}},
            headers={
                "Set-Cookie": "agenthub_token=cloudjwt; Domain=cloud.example.com; Path=/; HttpOnly",
            },
        )

    _mock_cloud(handler)
    resp = await desktop_client.post(
        "/api/auth/login",
        json={"email": "u@x.com", "password": "pw"},
    )
    assert resp.status_code == 200
    assert resp.json()["user"]["email"] == "u@x.com"

    # 缓存标记已写
    marker = json.loads((Path(desktop_env) / "cloud_session.json").read_text(encoding="utf-8"))
    assert marker["email"] == "u@x.com"

    # Set-Cookie 透传但 Domain 已去掉（否则浏览器会拒绝）
    set_cookie = resp.headers.get("set-cookie", "")
    assert "agenthub_token=cloudjwt" in set_cookie
    assert "domain=" not in set_cookie.lower()


@pytest.mark.asyncio
async def test_proxy_logout_clears_marker_even_when_cloud_down(desktop_client, desktop_env):
    marker = Path(desktop_env) / "cloud_session.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"email": "u@x.com", "name": "U", "loggedInAt": 1}), encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down", request=request)

    _mock_cloud(handler)
    resp = await desktop_client.post("/api/auth/logout")
    assert resp.status_code == 200
    assert not marker.exists()
    # 本地 cookie 也被清除
    assert "agenthub_token=" in resp.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_proxy_cloud_unreachable_returns_503_with_detail(desktop_client):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down", request=request)

    _mock_cloud(handler)
    resp = await desktop_client.post("/api/auth/login", json={"email": "u@x.com", "password": "pw"})
    assert resp.status_code == 503
    assert "云端不可达" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_proxy_without_cloud_config_returns_503(desktop_env, monkeypatch):
    """未配置 AGENTHUB_CLOUD_API_URL → 明确 503，不能静默或绕过。"""
    monkeypatch.setenv("AGENTHUB_CLOUD_API_URL", "")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        import app.services.agent_runner  # noqa: F401
        from app.main import create_app

        app = create_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/auth/login", json={"email": "u@x.com", "password": "pw"})
            assert resp.status_code == 503
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_local_user_survives_restart(desktop_client, desktop_env):
    """固定本地用户幂等 seed：关闭重开后仍解析同一用户（重启数据保留语义）。"""
    from app.config import get_settings
    from app.db import engine as engine_mod

    await engine_mod.close_db()
    await engine_mod.init_db()

    import app.services.agent_runner  # noqa: F401
    from app.main import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/conversations")
        assert resp.status_code == 200
        assert get_settings().agenthub_desktop is True
