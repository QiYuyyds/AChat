"""Workspace env API routes.

Endpoints for the workspace environment isolation feature
(specs/workspace-env-isolation):

  - POST   /api/workspaces/{conversation_id}/create-venv
  - PATCH  /api/workspaces/{conversation_id}/env-preference
  - GET    /api/workspaces/{conversation_id}/env-status

All mutation endpoints are protected by the global CSRF Origin middleware
(see main.py) and the per-conversation ownership check.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.auth.dependencies import get_current_user
from app.auth.ownership import verify_conversation_ownership
from app.db.models import User
from app.infra.cache_helpers import get_workspace_cached
from app.schemas import UpdateEnvPreferenceRequest, WorkspaceEnvStatusResponse
from app.services import workspace_env_service
from app.utils.workspace_utils import get_effective_cwd

router = APIRouter()


def _err(message: str, status: int) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message})


async def _read_json(req: Request) -> object | None:
    try:
        return await req.json()
    except Exception:  # noqa: BLE001 - any parse failure maps to None
        return None


# ─── POST /workspaces/{id}/create-venv ───────────────────────────────────────
@router.post("/workspaces/{conversation_id}/create-venv")
async def create_venv(
    conversation_id: str, user: User = Depends(get_current_user)
) -> JSONResponse:
    """Trigger asynchronous ``python -m venv .venv`` in the workspace's bound path.

    Returns 202 immediately; progress / result is reported via
    ``WorkspaceEnvStatusEvent`` SSE events. The frontend listens for
    ``status='creating'`` → ``'ready'`` / ``'failed'``.
    """
    await verify_conversation_ownership(conversation_id, user.id)

    workspace = await get_workspace_cached(conversation_id)
    if workspace is None:
        return _err("Workspace not found", 404)
    if workspace.mode != "local" or not workspace.bound_path:
        return _err(
            "Venv creation is only available for local-mode workspaces", 400
        )

    # Fire-and-forget: the SSE events carry the result. The task itself
    # catches all exceptions and emits a 'failed' status, so it won't leak.
    import asyncio

    asyncio.create_task(
        workspace_env_service.create_project_venv(conversation_id, user.id)
    )
    return JSONResponse(status_code=202, content={"accepted": True})


# ─── PATCH /workspaces/{id}/env-preference ───────────────────────────────────
@router.patch("/workspaces/{conversation_id}/env-preference")
async def update_env_preference(
    conversation_id: str,
    req: Request,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Persist the user's env choice (skip / system_python / venv_created).

    This dismisses the hint card on the frontend and suppresses future
    ``WorkspaceEnvHintEvent`` for this conversation.
    """
    await verify_conversation_ownership(conversation_id, user.id)

    raw = await _read_json(req)
    try:
        body = UpdateEnvPreferenceRequest.model_validate(raw)
    except ValidationError as exc:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid body", "issues": exc.errors()},
        )

    try:
        await workspace_env_service.set_env_preference(
            conversation_id, body.preference, user.id
        )
    except ValueError as err:
        return _err(str(err), 400)
    return JSONResponse(content={"ok": True})


# ─── GET /workspaces/{id}/env-status ─────────────────────────────────────────
@router.get("/workspaces/{conversation_id}/env-status")
async def get_env_status(
    conversation_id: str, user: User = Depends(get_current_user)
) -> JSONResponse:
    """Return the current project env detection result and persisted preference.

    The frontend calls this on page load to decide whether to show the env
    hint card (e.g. after a refresh, when the SSE hint event was missed).
    """
    await verify_conversation_ownership(conversation_id, user.id)

    workspace = await get_workspace_cached(conversation_id)
    if workspace is None:
        return _err("Workspace not found", 404)

    if workspace.mode != "local" or not workspace.bound_path:
        # Sandbox workspaces have no user project to inspect.
        response = WorkspaceEnvStatusResponse(
            workspace_mode=workspace.mode,
            language="unknown",
            venv_present=False,
            env_preference=workspace.env_preference,
        )
        return JSONResponse(content=response.model_dump(by_alias=True))

    info = workspace_env_service.detect_project_env(
        get_effective_cwd(workspace)
    )
    response = WorkspaceEnvStatusResponse(
        workspace_mode=workspace.mode,
        language=info.language,
        venv_present=info.venv_present,
        env_preference=workspace.env_preference,
    )
    return JSONResponse(content=response.model_dump(by_alias=True))
