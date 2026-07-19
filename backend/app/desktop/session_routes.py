"""Local-engine routes for session handoff and sync status (desktop only)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.desktop.cloud_client import clear_cloud_session, set_cloud_access_token
from app.desktop.offline_store import OfflineStore
from app.desktop.runtime import get_desktop_runtime
from app.desktop.sync import conflict_status_payload, flush_outbox

router = APIRouter(prefix="/api/desktop", tags=["desktop"])


class SessionHandoffBody(BaseModel):
    access_token: str = Field(..., min_length=1)
    user_id: str | None = None


@router.post("/session")
async def handoff_session(body: SessionHandoffBody) -> dict[str, str]:
    """Frontend posts user JWT after cloud login so engine can call official APIs."""
    set_cloud_access_token(body.access_token, body.user_id)
    return {"status": "ok"}


@router.delete("/session")
async def clear_session() -> dict[str, str]:
    clear_cloud_session()
    return {"status": "ok"}


@router.get("/sync/status")
async def sync_status() -> dict:
    rt = get_desktop_runtime()
    if not rt:
        raise HTTPException(status_code=503, detail="not in desktop runtime")
    store = OfflineStore(rt.sqlite_path())
    pending = store.list_pending()
    return {
        "pending": len(pending),
        "conflicts": conflict_status_payload(store),
    }


@router.post("/sync/flush")
async def sync_flush() -> dict:
    rt = get_desktop_runtime()
    if not rt:
        raise HTTPException(status_code=503, detail="not in desktop runtime")
    store = OfflineStore(rt.sqlite_path())
    report = await flush_outbox(store)
    return {
        "uploaded": report.uploaded,
        "conflicts": report.conflicts,
        "failed": report.failed,
        "errors": report.errors,
    }
