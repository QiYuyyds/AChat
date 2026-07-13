"""Tests for the profile API and preference source priority."""

from __future__ import annotations

import io

# ─── GET /api/profile ──────────────────────────────────────────────────────

async def test_get_profile_empty(api_client):
    """GET /api/profile returns all-null fields when no preferences exist."""
    resp = await api_client.get("/api/profile")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] is None
    assert body["location"] is None
    assert body["hometown"] is None
    assert body["preferences"] is None
    assert body["bio"] is None
    assert body["avatarUrl"] is None


# ─── PUT /api/profile ──────────────────────────────────────────────────────

async def test_put_profile_writes_manual_source(api_client):
    """PUT /api/profile stores fields with source='manual' in UserPreference."""
    resp = await api_client.put("/api/profile", json={
        "name": "张三",
        "location": "北京",
        "hometown": "成都",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "张三"
    assert body["location"] == "北京"
    assert body["hometown"] == "成都"

    # Verify source='manual' in DB
    from sqlalchemy import select

    from app.db.engine import get_db
    from app.db.models import UserPreference

    async with get_db() as session:
        stmt = select(UserPreference).where(UserPreference.user_id == "test_user_1")
        result = await session.execute(stmt)
        rows = {r.key: r for r in result.scalars().all()}

    assert rows["姓名"].source == "manual"
    assert rows["姓名"].value == "张三"
    assert rows["所在地"].source == "manual"
    assert rows["所在地"].value == "北京"
    assert rows["家乡"].source == "manual"
    assert rows["家乡"].value == "成都"


async def test_put_profile_clear_field(api_client):
    """PUT /api/profile with null deletes the field."""
    # Set a field first
    await api_client.put("/api/profile", json={"name": "李四"})
    # Clear it
    resp = await api_client.put("/api/profile", json={"name": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] is None


# ─── Preference source priority ────────────────────────────────────────────

async def test_extracted_does_not_overwrite_manual(api_client):
    """Preference.set(source='extracted') cannot overwrite a manual row."""
    from app.memory.preference import Preference

    # Set a manual value
    pref = Preference(user_id="test_user_1")
    await pref.set("所在地", "北京", source="manual")

    # Try to overwrite with extracted
    await pref.set("所在地", "重庆", source="extracted")

    # Manual value should be preserved
    assert pref.get("所在地") == "北京"


async def test_manual_overwrites_extracted():
    """Preference.set(source='manual') overwrites an extracted row."""
    from app.memory.preference import Preference

    pref = Preference(user_id="test_user_1")
    await pref.set("喜好", "Python", source="extracted")
    await pref.set("喜好", "Rust", source="manual")
    assert pref.get("喜好") == "Rust"


async def test_manual_value_not_truncated():
    """Manual values exceeding 200 chars are not truncated."""
    from app.memory.preference import Preference

    long_value = "A" * 300
    pref = Preference(user_id="test_user_1")
    await pref.set("简介", long_value, source="manual")
    assert len(pref.get("简介")) == 300


async def test_extracted_value_truncated():
    """Extracted values exceeding 200 chars are truncated."""
    from app.memory.preference import Preference

    long_value = "A" * 300
    pref = Preference(user_id="test_user_1")
    await pref.set("喜好", long_value, source="extracted")
    assert len(pref.get("喜好")) <= 200


# ─── Hometown extraction rules ─────────────────────────────────────────────

def test_hometown_extraction_laojia():
    """Rule-based extraction produces {'家乡': '成都'} from '我老家在成都'."""
    from app.memory.memory_writer import _extract_rule_based

    result = _extract_rule_based("我老家在成都")
    assert result == {"家乡": "成都"}


def test_hometown_extraction_jiaxiang():
    """Rule-based extraction produces {'家乡': '重庆'} from '我家乡在重庆'."""
    from app.memory.memory_writer import _extract_rule_based

    result = _extract_rule_based("我家乡在重庆")
    assert result == {"家乡": "重庆"}


def test_hometown_extraction_laojia_shi():
    """Rule-based extraction from '老家是杭州'."""
    from app.memory.memory_writer import _extract_rule_based

    result = _extract_rule_based("老家是杭州")
    assert result == {"家乡": "杭州"}


def test_hometown_classification():
    """classify_memory_content classifies '家乡'/'老家' as identity/hometown."""
    from app.memory.memory_writer import classify_memory_content

    category, tags, slot_hint = classify_memory_content("家乡", "成都")
    assert category == "identity"
    assert "hometown" in tags
    assert slot_hint == "profile"


# ─── Avatar upload ─────────────────────────────────────────────────────────

async def test_upload_avatar_valid(api_client, tmp_path, monkeypatch):
    """POST /api/profile/avatar accepts a valid PNG image."""
    monkeypatch.setenv("AGENTHUB_DATA_DIR", str(tmp_path / "data"))
    from app.config import get_settings
    get_settings.cache_clear()

    # Minimal valid PNG (1x1 pixel)
    png_bytes = (
        b'\x89PNG\r\n\x1a\n'
        b'\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde'
        b'\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N'
        b'\x00\x00\x00\x00IEND\xaeB`\x82'
    )
    resp = await api_client.post(
        "/api/profile/avatar",
        files={"file": ("avatar.png", io.BytesIO(png_bytes), "image/png")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["avatarUrl"] == "/api/profile/avatar"


async def test_upload_avatar_rejects_non_image(api_client):
    """POST /api/profile/avatar rejects a .txt file with 400."""
    resp = await api_client.post(
        "/api/profile/avatar",
        files={"file": ("file.txt", io.BytesIO(b"not an image"), "text/plain")},
    )
    assert resp.status_code == 400


async def test_upload_avatar_rejects_oversized(api_client, tmp_path, monkeypatch):
    """POST /api/profile/avatar rejects files larger than 2MB with 413."""
    monkeypatch.setenv("AGENTHUB_DATA_DIR", str(tmp_path / "data"))
    from app.config import get_settings
    get_settings.cache_clear()

    # Create a 3MB fake image
    big_data = b'\x00' * (3 * 1024 * 1024)
    resp = await api_client.post(
        "/api/profile/avatar",
        files={"file": ("big.png", io.BytesIO(big_data), "image/png")},
    )
    assert resp.status_code == 413


# ─── Avatar serving ────────────────────────────────────────────────────────

async def test_get_avatar_no_avatar(api_client):
    """GET /api/profile/avatar returns 404 when user has no avatar."""
    resp = await api_client.get("/api/profile/avatar")
    assert resp.status_code == 404


async def test_get_avatar_after_upload(api_client, tmp_path, monkeypatch):
    """GET /api/profile/avatar returns image bytes after upload."""
    monkeypatch.setenv("AGENTHUB_DATA_DIR", str(tmp_path / "data"))
    from app.config import get_settings
    get_settings.cache_clear()

    png_bytes = (
        b'\x89PNG\r\n\x1a\n'
        b'\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde'
        b'\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N'
        b'\x00\x00\x00\x00IEND\xaeB`\x82'
    )
    upload_resp = await api_client.post(
        "/api/profile/avatar",
        files={"file": ("avatar.png", io.BytesIO(png_bytes), "image/png")},
    )
    assert upload_resp.status_code == 200

    resp = await api_client.get("/api/profile/avatar")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert len(resp.content) > 0
