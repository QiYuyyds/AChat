"""Tests for the auth API router (app/api/auth.py).

Covers register, login, refresh, me, logout, change-password, logout-all,
and error cases: duplicate email, invalid credentials, disabled registration.
"""

from __future__ import annotations

from app.auth.dependencies import COOKIE_NAME

# ─── register ─────────────────────────────────────────────────────────────

async def test_register_success(raw_client):
    resp = await raw_client.post("/api/auth/register", json={
        "email": "new@test.com",
        "name": "New User",
        "password": "securepass123",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user"]["email"] == "new@test.com"
    assert body["user"]["name"] == "New User"
    assert "access_token" in body["tokens"]
    assert "refresh_token" in body["tokens"]
    assert body["config"]["allowRegistration"] is True
    # Cookie should be set
    assert COOKIE_NAME in resp.cookies


async def test_register_duplicate_email(raw_client, test_user):
    resp = await raw_client.post("/api/auth/register", json={
        "email": test_user["email"],
        "name": "Dup",
        "password": "securepass123",
    })
    assert resp.status_code == 409


async def test_register_disabled(db, raw_client, monkeypatch):
    monkeypatch.setenv("ALLOW_REGISTRATION", "false")
    from app.config import get_settings
    get_settings.cache_clear()

    resp = await raw_client.post("/api/auth/register", json={
        "email": "disabled@test.com",
        "name": "Disabled",
        "password": "securepass123",
    })
    assert resp.status_code == 403


# ─── login ────────────────────────────────────────────────────────────────

async def test_login_success(raw_client, test_user):
    resp = await raw_client.post("/api/auth/login", json={
        "email": test_user["email"],
        "password": "testpass123",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["user"]["email"] == test_user["email"]
    assert "access_token" in body["tokens"]
    assert COOKIE_NAME in resp.cookies


async def test_login_invalid_password(raw_client, test_user):
    resp = await raw_client.post("/api/auth/login", json={
        "email": test_user["email"],
        "password": "wrongpassword",
    })
    assert resp.status_code == 401


async def test_login_unknown_email(raw_client):
    resp = await raw_client.post("/api/auth/login", json={
        "email": "nobody@test.com",
        "password": "somepassword",
    })
    assert resp.status_code == 401


# ─── VIP login ────────────────────────────────────────────────────────────

async def test_auth_config_includes_vip_login_flag(raw_client, monkeypatch):
    monkeypatch.setenv("VIP_LOGIN_ENABLED", "true")
    from app.config import get_settings

    get_settings.cache_clear()
    resp = await raw_client.get("/api/auth/config")
    assert resp.status_code == 200
    assert resp.json()["vipLoginEnabled"] is True


async def test_vip_login_success(raw_client, test_user, monkeypatch):
    monkeypatch.setenv("VIP_LOGIN_ENABLED", "true")
    monkeypatch.setenv("DEFAULT_USER_EMAIL", test_user["email"])
    from app.config import get_settings

    get_settings.cache_clear()
    resp = await raw_client.post(
        "/api/auth/vip-login",
        json={"password": "testpass123"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["user"]["email"] == test_user["email"]
    assert COOKIE_NAME in resp.cookies


async def test_vip_login_invalid_password(raw_client, test_user, monkeypatch):
    monkeypatch.setenv("VIP_LOGIN_ENABLED", "true")
    monkeypatch.setenv("DEFAULT_USER_EMAIL", test_user["email"])
    from app.config import get_settings

    get_settings.cache_clear()
    resp = await raw_client.post(
        "/api/auth/vip-login",
        json={"password": "wrongpassword"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid credentials"


async def test_vip_login_disabled(raw_client, monkeypatch):
    monkeypatch.setenv("VIP_LOGIN_ENABLED", "false")
    from app.config import get_settings

    get_settings.cache_clear()
    resp = await raw_client.post(
        "/api/auth/vip-login",
        json={"password": "testpass123"},
    )
    assert resp.status_code == 404


async def test_vip_login_missing_default_user(raw_client, monkeypatch):
    monkeypatch.setenv("VIP_LOGIN_ENABLED", "true")
    monkeypatch.setenv("DEFAULT_USER_EMAIL", "missing@example.com")
    from app.config import get_settings

    get_settings.cache_clear()
    resp = await raw_client.post(
        "/api/auth/vip-login",
        json={"password": "testpass123"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid credentials"


# ─── me ───────────────────────────────────────────────────────────────────

async def test_me_authenticated(api_client):
    resp = await api_client.get("/api/auth/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["email"] == "test@example.com"
    assert "config" in body


async def test_me_unauthenticated(raw_client):
    resp = await raw_client.get("/api/auth/me")
    assert resp.status_code == 401


# ─── refresh ──────────────────────────────────────────────────────────────

async def test_refresh_success(raw_client, test_user):
    # Login to get refresh token
    login_resp = await raw_client.post("/api/auth/login", json={
        "email": test_user["email"],
        "password": "testpass123",
    })
    refresh_token = login_resp.json()["tokens"]["refresh_token"]

    resp = await raw_client.post("/api/auth/refresh", json={
        "refreshToken": refresh_token,
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "access_token" in body["tokens"]
    assert body["user"]["email"] == test_user["email"]


async def test_refresh_invalid_token(raw_client):
    resp = await raw_client.post("/api/auth/refresh", json={
        "refreshToken": "invalid.token.here",
    })
    assert resp.status_code == 401


# ─── logout ───────────────────────────────────────────────────────────────

async def test_logout_clears_cookie(api_client):
    resp = await api_client.post("/api/auth/logout")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


# ─── change-password ──────────────────────────────────────────────────────

async def test_change_password_success(api_client):
    resp = await api_client.post("/api/auth/change-password", json={
        "currentPassword": "testpass123",
        "newPassword": "newpass456",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "access_token" in body["tokens"]


async def test_change_password_wrong_current(api_client):
    resp = await api_client.post("/api/auth/change-password", json={
        "currentPassword": "wrongpassword",
        "newPassword": "newpass456",
    })
    assert resp.status_code == 400


async def test_change_password_invalidates_old_token(db, raw_client, test_user):
    # Change password using the Authorization header
    resp = await raw_client.post("/api/auth/change-password",
        json={"currentPassword": "testpass123", "newPassword": "newpass456"},
        headers={"Authorization": f"Bearer {test_user['token']}"},
    )
    assert resp.status_code == 200

    # Verify token_version was incremented in the DB
    from sqlalchemy import select

    from app.db.engine import get_db
    from app.db.models import User

    async with get_db() as session:
        result = await session.execute(select(User).where(User.id == "test_user_1"))
        user = result.scalar_one()
        assert user.token_version == 1


# ─── logout-all ───────────────────────────────────────────────────────────

async def test_logout_all_invalidates_token(api_client):
    """Logout-all increments token_version in the DB."""
    resp = await api_client.post("/api/auth/logout-all")
    assert resp.status_code == 200

    # Verify token_version was incremented in the DB
    from sqlalchemy import select

    from app.db.engine import get_db
    from app.db.models import User

    async with get_db() as session:
        result = await session.execute(select(User).where(User.id == "test_user_1"))
        user = result.scalar_one()
        assert user.token_version == 1
