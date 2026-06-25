"""Deployment asset serving — serves the inlined preview site at /deployments/{id}/...

Port of the old Next.js route src/app/deployments/[id]/[[...path]]/route.ts (which
read assets via the TS deployment-service). Mounted at root (no /api prefix) so the
previewPath `/deployments/{id}` the agent emits resolves directly. The Next.js
frontend proxies `/deployments/*` here via a rewrite (see next.config.ts).
"""

from fastapi import APIRouter, Response

from app.services.deployment_service import read_deployment_asset

router = APIRouter()


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
async def serve_deployment_root(deployment_id: str) -> Response:
    """Serve the deployment's runtime entry (index.html)."""
    return _serve(deployment_id, None)


@router.get("/deployments/{deployment_id}/{asset_path:path}")
async def serve_deployment_asset(deployment_id: str, asset_path: str) -> Response:
    """Serve a specific asset within the deployment."""
    parts = [p for p in asset_path.split("/") if p]
    return _serve(deployment_id, parts or None)
