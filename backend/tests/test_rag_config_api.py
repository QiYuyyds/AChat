"""Tests for RAG config API endpoints (presets + ocr-engines).

Sync tests via TestClient; auth is bypassed with a dependency override on
get_current_user, and the shared ``db`` fixture backs the DB dependency.
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def mock_user():
    from app.db.models import User

    return User(
        id="test_user",
        email="test@test.com",
        name="Test",
        password_hash="x",
        token_version=0,
        created_at=0,
        updated_at=0,
    )


@pytest.fixture
def client(db, mock_user, monkeypatch):
    """Sync TestClient over create_app with auth overridden away."""
    from app.auth.dependencies import get_current_user
    from app.main import create_app

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: mock_user

    # Route-level user-settings reads are stubbed so no DB rows are needed.
    import app.api.rag_config as rag_config_module

    async def mock_get_settings(user_id):
        from app.services.settings_service import _empty_user_settings

        return _empty_user_settings(user_id)

    monkeypatch.setattr(rag_config_module, "get_user_settings", mock_get_settings)

    with TestClient(app) as test_client:
        yield test_client


class TestRagPresetsEndpoint:
    """GET /api/rag/presets"""

    def test_returns_preset_list(self, client):
        """Endpoint should return all presets with id/label/description."""
        from app.rag.chunking.presets import CHUNK_PRESETS

        response = client.get("/api/rag/presets")
        assert response.status_code == 200
        data = response.json()

        assert "presets" in data
        assert "default" in data
        assert len(data["presets"]) == len(CHUNK_PRESETS)

        for preset in data["presets"]:
            assert "id" in preset
            assert "label" in preset
            assert "description" in preset

        assert data["default"] == "general"


class TestOcrEnginesEndpoint:
    """GET /api/ocr-engines"""

    def test_returns_engine_list(self, client, monkeypatch):
        """Endpoint should return engine list with status fields."""
        # Mock get_ocr_engine_status to avoid real imports
        def mock_status():
            return [
                {"id": "rapidocr", "label": "RapidOCR", "available": False, "status": "not_installed"},
                {"id": "deepseek_ocr", "label": "DeepSeek OCR", "available": False, "status": "not_configured"},
            ]

        import app.api.rag_config as rag_config_module

        monkeypatch.setattr(rag_config_module, "get_ocr_engine_status", mock_status)

        response = client.get("/api/ocr-engines")
        assert response.status_code == 200
        data = response.json()

        assert "engines" in data
        assert "current" in data
        assert len(data["engines"]) == 2

        for engine in data["engines"]:
            assert "id" in engine
            assert "label" in engine
            assert "available" in engine
            assert "status" in engine

        assert data["current"] == "auto"
