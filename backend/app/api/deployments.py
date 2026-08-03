"""Deployment asset serving — serves the inlined preview site at /deployments/{id}/...

Auth: uses ``get_current_user_optional`` — when the user is authenticated (normal
API calls), the deployment's artifact → conversation → user ownership chain is
verified. When no auth is present (iframe preview with sandbox), the asset is
still served because the deployment_id is an unguessable nanoid.
"""

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select

from app.auth.dependencies import get_current_user_optional
from app.db.engine import get_local_db
from app.db.models import Artifact, Conversation, User
from app.services.deployment_service import read_deployment_asset, read_deployment_manifest

router = APIRouter()


async def _verify_deployment_ownership(deployment_id: str, user: User) -> bool:
    """Return True if the deployment's artifact belongs to a conversation owned by the user."""
    manifest = read_deployment_manifest(deployment_id)
    if manifest is None:
        return False
    artifact_id = manifest.get("artifactId", "")
    # Workspace deployments use "workspace:<path>" — no artifact to verify.
    if not artifact_id or not artifact_id.startswith("art_"):
        return True
    async with get_local_db() as db:
        result = await db.execute(
            select(Conversation.user_id)
            .join(Artifact, Artifact.conversation_id == Conversation.id)
            .where(Artifact.id == artifact_id)
        )
        row = result.scalar_one_or_none()
    return row is not None and row == user.id


def _serve(deployment_id: str, path_parts: list[str] | None) -> Response:
    result = read_deployment_asset(deployment_id, path_parts)
    if not result.ok:
        return Response(
            content=result.error or "Not found",
            status_code=result.status or 404,
            media_type="text/plain; charset=utf-8",
        )
    return Response(
        content=result.body or b"",
        media_type=result.content_type or "application/octet-stream",
        headers=result.headers or {},
    )


@router.get("/deployments/{deployment_id}")
async def serve_deployment_root(
    deployment_id: str,
    user: User | None = Depends(get_current_user_optional),
) -> Response:
    """Serve the deployment's runtime entry (index.html)."""
    if user is not None:
        if not await _verify_deployment_ownership(deployment_id, user):
            return Response(content="Not found", status_code=404)
    return _serve(deployment_id, None)


@router.get("/deployments/{deployment_id}/{asset_path:path}")
async def serve_deployment_asset(
    deployment_id: str,
    asset_path: str,
    user: User | None = Depends(get_current_user_optional),
) -> Response:
    """Serve a specific asset within the deployment."""
    if user is not None:
        if not await _verify_deployment_ownership(deployment_id, user):
            return Response(content="Not found", status_code=404)
    parts = [p for p in asset_path.split("/") if p]
    return _serve(deployment_id, parts or None)
