"""Local-engine routes for infra config, sync status (desktop only)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.desktop.cloud_client import clear_cloud_session, set_cloud_access_token
from app.desktop.config import (
    clear_user_override,
    load_desktop_config,
    redact_config,
    save_user_override,
)
from app.desktop.offline_store import OfflineStore
from app.desktop.runtime import get_desktop_runtime
from app.desktop.sync import conflict_status_payload, flush_outbox

router = APIRouter(prefix="/api/desktop", tags=["desktop"])


class SessionHandoffBody(BaseModel):
    """Legacy v0 handoff — kept for optional cloud_api_client feature flag."""

    access_token: str = Field(..., min_length=1)
    user_id: str | None = None


class InfraConfigBody(BaseModel):
    """User override payload (subset of DesktopConfig JSON)."""

    flavor: str | None = None
    allowedOrigins: list[str] | None = None
    updateFeedUrl: str | None = None
    infra: dict[str, Any] | None = None
    featureFlags: dict[str, Any] | None = None


@router.post("/session")
async def handoff_session(body: SessionHandoffBody) -> dict[str, str]:
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


@router.get("/infra-config")
async def get_infra_config() -> dict[str, Any]:
    """Return effective config with secrets redacted (for settings UI)."""
    rt = get_desktop_runtime()
    if not rt:
        raise HTTPException(status_code=503, detail="not in desktop runtime")
    cfg = rt.desktop_config or load_desktop_config(
        data_dir=rt.data_dir,
        packaged_path=rt.infra_config_path,
    )
    public = cfg.to_dict(include_secrets=False)
    public["redactedPreview"] = redact_config(cfg)
    public["restartRequired"] = True
    return public


@router.put("/infra-config")
async def put_infra_config(body: InfraConfigBody) -> dict[str, Any]:
    """Save user override under %APPDATA%/AChat/config. Requires engine restart to apply."""
    rt = get_desktop_runtime()
    if not rt:
        raise HTTPException(status_code=503, detail="not in desktop runtime")
    payload: dict[str, Any] = {}
    if body.flavor is not None:
        payload["flavor"] = body.flavor
    if body.allowedOrigins is not None:
        payload["allowedOrigins"] = body.allowedOrigins
    if body.updateFeedUrl is not None:
        payload["updateFeedUrl"] = body.updateFeedUrl
    if body.infra is not None:
        payload["infra"] = body.infra
    if body.featureFlags is not None:
        payload["featureFlags"] = body.featureFlags
    if not payload:
        raise HTTPException(status_code=400, detail="empty config body")
    path = save_user_override(rt.data_dir, payload)
    return {
        "status": "ok",
        "path": str(path),
        "restartRequired": True,
        "message": "Saved. Restart the local engine (or app) to apply infrastructure settings.",
    }


@router.delete("/infra-config")
async def delete_infra_config() -> dict[str, Any]:
    """Revert to packaged defaults (delete user override)."""
    rt = get_desktop_runtime()
    if not rt:
        raise HTTPException(status_code=503, detail="not in desktop runtime")
    removed = clear_user_override(rt.data_dir)
    return {
        "status": "ok",
        "removed": removed,
        "restartRequired": True,
        "message": "User override cleared. Restart the local engine to reload packaged defaults.",
    }
