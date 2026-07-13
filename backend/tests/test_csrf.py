"""Tests for CSRF Origin check middleware.

Verifies that POST/PATCH/DELETE requests with a non-matching Origin header
are rejected with 403, and that requests without an Origin header (or with
a matching origin) are allowed.
"""

from __future__ import annotations


async def test_post_with_matching_origin_allowed(api_client):
    """POST with an allowed Origin header succeeds."""
    resp = await api_client.post(
        "/api/auth/logout",
        headers={"Origin": "http://localhost:3000"},
    )
    assert resp.status_code == 200


async def test_post_without_origin_allowed(api_client):
    """POST without any Origin header passes (non-browser client)."""
    resp = await api_client.post("/api/auth/logout")
    assert resp.status_code == 200


async def test_post_with_foreign_origin_rejected(api_client):
    """POST with a foreign Origin header gets 403."""
    resp = await api_client.post(
        "/api/auth/logout",
        headers={"Origin": "http://evil.example.com"},
    )
    assert resp.status_code == 403


async def test_patch_with_foreign_origin_rejected(api_client):
    """PATCH with a foreign Origin header gets 403."""
    resp = await api_client.patch(
        "/api/settings",
        json={"anthropicApiKey": "sk-test"},
        headers={"Origin": "http://evil.example.com"},
    )
    assert resp.status_code == 403


async def test_delete_with_foreign_origin_rejected(api_client):
    """DELETE with a foreign Origin header gets 403."""
    resp = await api_client.delete(
        "/api/conversations/fake_id",
        headers={"Origin": "http://evil.example.com"},
    )
    assert resp.status_code == 403


async def test_get_with_foreign_origin_allowed(api_client):
    """GET requests are not subject to CSRF checks."""
    resp = await api_client.get(
        "/api/conversations",
        headers={"Origin": "http://evil.example.com"},
    )
    # Should not be 403 (may be 200 or other status, but not CSRF-blocked)
    assert resp.status_code != 403
