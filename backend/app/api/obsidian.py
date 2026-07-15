"""Obsidian sync API routes — sync trigger and status.

Routes:
  POST /api/obsidian/sync    — trigger vault sync
  GET  /api/obsidian/status  — get vault status and last sync info
"""

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.auth.dependencies import get_current_user
from app.db.models import User
from app.schemas.obsidian import SyncRequest
from app.services.settings_service import get_user_settings

router = APIRouter()


def _get_sync_service():
    """Lazy import to avoid circular dependency; returns the global ObsidianSyncService."""
    from app.main import _obsidian_sync_service  # type: ignore[attr-defined]
    if _obsidian_sync_service is None:
        raise RuntimeError("ObsidianSyncService not initialized")
    return _obsidian_sync_service


def _get_document_service():
    """Lazy import to avoid circular dependency; returns the global DocumentService."""
    from app.main import _document_service  # type: ignore[attr-defined]
    if _document_service is None:
        raise RuntimeError("DocumentService not initialized")
    return _document_service


@router.post("/obsidian/sync")
async def sync_obsidian(
    req: SyncRequest | None = None,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Trigger an Obsidian vault sync for the current user."""
    svc = _get_sync_service()

    # Read vault path from user settings if not provided in request
    vault_path = None
    if req and req.vault_path:
        vault_path = req.vault_path
    else:
        settings = await get_user_settings(user.id)
        vault_path = settings.obsidian_vault_path

    if not vault_path:
        return JSONResponse(
            {"error": "Obsidian vault path not configured. Set it in Settings."},
            status_code=400,
        )

    report = await svc.sync_vault(vault_path, user.id)

    return JSONResponse({
        "scanned": report["scanned"],
        "added": report["added"],
        "updated": report["updated"],
        "deleted": report["deleted"],
        "skipped": report["skipped"],
        "errors": report["errors"],
    })


@router.get("/obsidian/status")
async def get_obsidian_status(
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Get current Obsidian vault status and last sync info."""
    svc = _get_sync_service()

    settings = await get_user_settings(user.id)
    vault_path = settings.obsidian_vault_path

    status = await svc.get_status(user.id, vault_path)

    return JSONResponse({
        "vaultPath": status["vault_path"],
        "vaultExists": status["vault_exists"],
        "totalMdFiles": status["total_md_files"],
        "lastSyncAt": status["last_sync_at"],
        "lastSyncSummary": status["last_sync_summary"],
    })
