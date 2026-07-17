"""Authenticated REST control plane for local source intelligence."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.auth.dependencies import get_current_user
from app.auth.ownership import verify_conversation_ownership
from app.code_intelligence import service as code_service
from app.code_intelligence.metadata import MetadataStore
from app.db.models import User, Workspace
from app.services.fs_service import get_workspace_for_conversation

router = APIRouter()
CurrentUser = Annotated[User, Depends(get_current_user)]


async def _workspace_context(conversation_id: str, user_id: str) -> Workspace:
    await verify_conversation_ownership(conversation_id, user_id)
    workspace = await get_workspace_for_conversation(conversation_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    return workspace


def _paths(workspace: Workspace) -> tuple[Path, Path]:
    if workspace.mode != "local" or not workspace.bound_path:
        raise HTTPException(
            status_code=400,
            detail="Source intelligence is available only for local workspaces",
        )
    return Path(workspace.root_path), Path(workspace.bound_path).resolve()


def _service():
    try:
        return code_service.get_code_intelligence_service()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _require_state(
    workspace_root: Path,
    operation: str,
    allowed: set[str],
) -> None:
    status = MetadataStore(workspace_root).read().status
    if status not in allowed:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot {operation} source intelligence from state {status}",
        )


@router.get("/conversations/{conversation_id}/code-intelligence")
async def get_status(
    conversation_id: str,
    user: CurrentUser,
) -> JSONResponse:
    workspace = await _workspace_context(conversation_id, user.id)
    workspace_root, project_path = _paths(workspace)
    metadata = MetadataStore(workspace_root).read().model_dump(by_alias=True)
    metadata["projectPath"] = str(project_path)
    return JSONResponse(content={"status": metadata})


@router.post("/conversations/{conversation_id}/code-intelligence/enable")
async def enable(
    conversation_id: str,
    user: CurrentUser,
) -> JSONResponse:
    workspace = await _workspace_context(conversation_id, user.id)
    workspace_root, project_path = _paths(workspace)
    _require_state(workspace_root, "enable", {"disabled"})
    _service().schedule_enable(
        workspace_root=workspace_root,
        project_path=project_path,
        download_approved=True,
    )
    return JSONResponse(status_code=202, content={"accepted": True})


@router.post("/conversations/{conversation_id}/code-intelligence/cancel")
async def cancel(
    conversation_id: str,
    user: CurrentUser,
) -> JSONResponse:
    workspace = await _workspace_context(conversation_id, user.id)
    workspace_root, project_path = _paths(workspace)
    _require_state(workspace_root, "cancel", {"preparing_runtime", "queued", "indexing", "syncing", "rebuilding", "cancelling"})
    accepted = await _service().cancel(
        workspace_root=workspace_root,
        project_path=project_path,
    )
    return JSONResponse(content={"accepted": accepted})


@router.post("/conversations/{conversation_id}/code-intelligence/sync")
async def sync(
    conversation_id: str,
    user: CurrentUser,
) -> JSONResponse:
    workspace = await _workspace_context(conversation_id, user.id)
    workspace_root, project_path = _paths(workspace)
    _require_state(workspace_root, "sync", {"ready"})
    _service().schedule_operation(
        workspace_root=workspace_root,
        project_path=project_path,
        operation="sync",
    )
    return JSONResponse(status_code=202, content={"accepted": True})


@router.post("/conversations/{conversation_id}/code-intelligence/rebuild")
async def rebuild(
    conversation_id: str,
    user: CurrentUser,
) -> JSONResponse:
    workspace = await _workspace_context(conversation_id, user.id)
    workspace_root, project_path = _paths(workspace)
    _require_state(workspace_root, "rebuild", {"ready"})
    _service().schedule_operation(
        workspace_root=workspace_root,
        project_path=project_path,
        operation="rebuild",
    )
    return JSONResponse(status_code=202, content={"accepted": True})


@router.post("/conversations/{conversation_id}/code-intelligence/retry")
async def retry(
    conversation_id: str,
    user: CurrentUser,
) -> JSONResponse:
    workspace = await _workspace_context(conversation_id, user.id)
    workspace_root, project_path = _paths(workspace)
    _require_state(workspace_root, "retry", {"failed", "interrupted"})
    _service().schedule_enable(
        workspace_root=workspace_root,
        project_path=project_path,
        download_approved=True,
    )
    return JSONResponse(status_code=202, content={"accepted": True})


@router.post("/conversations/{conversation_id}/code-intelligence/disable")
async def disable(
    conversation_id: str,
    user: CurrentUser,
) -> JSONResponse:
    workspace = await _workspace_context(conversation_id, user.id)
    workspace_root, project_path = _paths(workspace)
    _require_state(workspace_root, "disable", {"preparing_runtime", "queued", "indexing", "ready", "syncing", "rebuilding", "cancelling", "failed", "interrupted"})
    await _service().disable(
        workspace_root=workspace_root,
        project_path=project_path,
    )
    return JSONResponse(content={"ok": True})
