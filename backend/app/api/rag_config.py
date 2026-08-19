"""RAG configuration query API — presets + OCR engine status.

Read-only endpoints that expose backend RAG capabilities to the frontend:
- GET /api/rag/presets — chunking strategy list
- GET /api/ocr-engines — OCR engine availability and health
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.auth.dependencies import get_current_user
from app.config import get_settings
from app.db.models import User
from app.rag.chunking.presets import CHUNK_PRESETS
from app.rag.parser_registry import get_ocr_engine_status
from app.services.settings_service import get_user_settings

router = APIRouter()


@router.get("/rag/presets")
async def get_rag_presets(
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Return all chunking presets with labels, descriptions, and the user's default."""
    us = await get_user_settings(user.id)
    settings = get_settings()

    default_preset = us.rag_chunk_preset or settings.rag_chunk_preset

    presets = [
        {"id": pid, "label": p["label"], "description": p["description"]}
        for pid, p in CHUNK_PRESETS.items()
    ]

    return JSONResponse({"presets": presets, "default": default_preset})


@router.get("/ocr-engines")
async def get_ocr_engines(
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Return OCR engine availability and health status for all registered engines."""
    us = await get_user_settings(user.id)
    settings = get_settings()

    current = us.ocr_engine or settings.ocr_engine
    engines = get_ocr_engine_status()

    return JSONResponse({"engines": engines, "current": current})
