"""Artifacts API routes.

Ports the TS routes under ``src/app/api/artifacts/``:
  - GET    /api/artifacts                     → list all artifacts
  - GET    /api/artifacts/{id}                → one artifact
  - DELETE /api/artifacts/{id}                → delete
  - GET    /api/artifacts/{id}/versions       → version chain
  - POST   /api/artifacts/{id}/versions       → submit edited content as new version
  - GET    /api/artifacts/{id}/export         → one-click download
  - GET    /api/artifacts/{id}/preview        → rendered web_app HTML

Routes are thin: they call ``artifact_service`` (the source of truth, owning its
own DB sessions) and translate results into HTTP responses matching the TS
contract byte-for-byte (the unchanged React frontend depends on it).
"""

from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from app.schemas import CreateArtifactVersionRequest
from app.schemas.artifacts import ProjectFile
from app.services import artifact_service
from app.services.fs_service import get_workspace_for_conversation
from app.services.project_artifact import zip_project_from_workspace
from app.utils.artifact_preview import build_web_app_html
from app.utils.workspace_utils import get_effective_cwd

router = APIRouter()

_PREVIEW_CSP = "; ".join(
    [
        "sandbox allow-scripts",
        "default-src 'none'",
        "script-src 'unsafe-inline'",
        "style-src 'unsafe-inline'",
        "img-src data: blob: http: https:",
        "font-src data:",
        "connect-src 'none'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'self'",
    ]
)


def _iso_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _content_disposition(filename: str) -> str:
    """Mirror the TS ``attachment; filename="${encodeURIComponent(base)}.ext"``.

    The TS code URL-encodes only the base name and appends the literal extension,
    so the dot before the extension stays un-encoded.
    """
    if "." in filename:
        base, _, ext = filename.rpartition(".")
        encoded = f"{quote(base, safe='')}.{ext}"
    else:
        encoded = quote(filename, safe="")
    return f'attachment; filename="{encoded}"'


@router.get("/artifacts")
async def list_artifacts() -> dict:
    """List all artifacts, newest first."""
    artifacts = await artifact_service.list_artifacts()
    return {"artifacts": [a.to_camel() for a in artifacts]}


@router.get("/artifacts/{artifact_id}")
async def get_artifact(artifact_id: str):
    """Get an artifact by ID."""
    artifact = await artifact_service.get_artifact(artifact_id)
    if artifact is None:
        return JSONResponse({"error": "Not found"}, status_code=404)
    return {"artifact": artifact}


@router.delete("/artifacts/{artifact_id}")
async def delete_artifact(artifact_id: str):
    """Delete an artifact."""
    try:
        await artifact_service.delete_artifact(artifact_id)
    except ValueError as err:
        return JSONResponse({"error": str(err)}, status_code=404)
    return {"ok": True}


@router.get("/artifacts/{artifact_id}/versions")
async def list_artifact_versions(artifact_id: str):
    """Return the full version chain (root → all descendants, ascending)."""
    versions = await artifact_service.list_artifact_versions(artifact_id)
    if versions is None:
        return JSONResponse({"error": "Artifact not found"}, status_code=404)
    return {"versions": versions}


@router.post("/artifacts/{artifact_id}/versions")
async def create_artifact_version(artifact_id: str, body: CreateArtifactVersionRequest):
    """Submit edited content as a new version (version+1 off the given parent)."""
    result = await artifact_service.create_artifact_version(
        artifact_id, body.content, body.title
    )
    if not result.ok:
        return JSONResponse({"error": result.error}, status_code=result.status or 400)
    return {"artifact": result.artifact}


@router.get("/artifacts/{artifact_id}/export")
async def export_artifact(artifact_id: str, request: Request):
    """One-click export; dispatches on artifact type inside the service."""
    export_mode = request.query_params.get("mode") or "editable"
    export = await artifact_service.serialize_artifact_export(artifact_id, export_mode)

    if export.kind == "error":
        return JSONResponse({"error": export.error}, status_code=export.status or 400)

    if export.kind == "redirect":
        return RedirectResponse(export.redirect_url or "", status_code=302)

    if export.kind == "file":
        return Response(
            content=export.body or b"",
            media_type=export.content_type,
            headers={
                "Content-Disposition": _content_disposition(export.filename or "artifact")
            },
        )

    # deferred — router finishes work that needs extra machinery
    if export.deferred_kind == "ppt":
        # PPTX generation has no Python port yet (TS used @/server/ppt-export).
        return JSONResponse(
            {"error": "PPTX export is not available in this build."},
            status_code=501,
        )

    if export.deferred_kind == "project":
        artifact = await artifact_service.get_artifact(artifact_id)
        if artifact is None:
            return JSONResponse({"error": "Artifact not found"}, status_code=404)
        workspace = await get_workspace_for_conversation(artifact["conversationId"])
        if workspace is None:
            return JSONResponse({"error": "Workspace not found"}, status_code=404)
        content = artifact["content"]
        raw_files = content.get("files", []) if isinstance(content, dict) else []
        files = [ProjectFile(**f) for f in raw_files]
        buf = zip_project_from_workspace(
            get_effective_cwd(workspace),
            files,
            artifact["title"],
            _iso_now(),
        )
        return Response(
            content=buf,
            media_type="application/zip",
            headers={
                "Content-Disposition": _content_disposition(
                    f"{export.base_name or 'artifact'}.zip"
                )
            },
        )

    return JSONResponse({"error": "Unsupported export"}, status_code=400)


@router.get("/artifacts/{artifact_id}/preview")
async def preview_artifact(artifact_id: str):
    """Render a web_app artifact as a sandboxed, self-contained HTML document."""
    artifact = await artifact_service.get_artifact(artifact_id)
    if artifact is None:
        return JSONResponse({"error": "Artifact not found"}, status_code=404)

    content = artifact["content"]
    if not isinstance(content, dict) or content.get("type") != "web_app":
        return JSONResponse({"error": "Artifact is not a web_app"}, status_code=400)

    html = build_web_app_html(
        content.get("files") or {}, content.get("entry", "index.html")
    )
    return Response(
        content=html,
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Security-Policy": _PREVIEW_CSP,
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store",
        },
    )
