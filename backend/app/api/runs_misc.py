"""Misc API routes.

Ports the standalone Next.js routes that don't belong to a larger resource
group: run abort, message search, usage summary, host platform, companion
connection hints, and deployment zip downloads.

The internal Codex tool-bridge route (POST /api/internal/agenthub-tools) lives
in ``app/api/internal.py`` — it was ported alongside the CodexCLIAdapter.
"""

import os
from urllib.parse import quote

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response

from app.schemas import (
    ConnectionHintsResponse,
    PlatformResponse,
    SearchHit,
    SearchResponse,
    UsageSummaryResponse,
)
from app.services import conversation_service, deployment_service
from app.services.network_hints import get_connection_hints
from app.services.search_service import search_messages
from app.services.settings_service import DEFAULT_COMPANION_PORT, get_app_settings
from app.services.usage_summary_service import get_usage_summary
from app.utils.platform import IS_WINDOWS

router = APIRouter()


# ─── POST /api/runs/{id}/abort ───────────────────────────────────────────────
@router.post("/runs/{run_id}/abort")
async def abort_run(run_id: str) -> JSONResponse:
    """Abort a running agent run."""
    ok = await conversation_service.abort_run(run_id)
    if not ok:
        return JSONResponse(
            {"error": "Run not found or already finished"}, status_code=404
        )
    return JSONResponse({"ok": True})


# ─── GET /api/search ─────────────────────────────────────────────────────────
@router.get("/search")
async def search(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    conversation_id: str | None = Query(None, alias="conversationId"),
    role: str | None = Query(None),
    fallback: str | None = Query(None),
) -> JSONResponse:
    """Search messages. Mirrors the TS envelope { ok, data } / { ok, error }."""
    # Validate the enum-constrained params the way the TS zod schema does.
    if role is not None and role not in ("user", "agent"):
        return JSONResponse(
            {"ok": False, "error": {"code": "INVALID_QUERY", "message": "Invalid role"}},
            status_code=400,
        )
    if fallback is not None and fallback != "like":
        return JSONResponse(
            {
                "ok": False,
                "error": {"code": "INVALID_QUERY", "message": "Invalid fallback"},
            },
            status_code=400,
        )

    result = await search_messages(
        query=q,
        limit=limit,
        offset=offset,
        conversation_id=conversation_id,
        role=role,
        fallback=fallback,
    )
    if result.error == "INVALID_QUERY":
        return JSONResponse(
            {
                "ok": False,
                "error": {"code": "INVALID_QUERY", "message": "Invalid search syntax"},
            },
            status_code=400,
        )

    payload = SearchResponse(
        hits=[
            SearchHit(
                message_id=h.message_id,
                conversation_id=h.conversation_id,
                conversation_title=h.conversation_title,
                role=h.role,
                agent_id=h.agent_id,
                agent_name=h.agent_name,
                agent_avatar=h.agent_avatar,
                created_at=h.created_at,
                snippet_html=h.snippet_html,
            )
            for h in result.hits
        ],
        total=result.total,
        took_ms=result.took_ms,
    )
    return JSONResponse({"ok": True, "data": payload.model_dump(by_alias=True)})


# ─── GET /api/usage/summary ──────────────────────────────────────────────────
@router.get("/usage/summary")
async def usage_summary() -> JSONResponse:
    """Global token usage summary."""
    summary = await get_usage_summary()
    # Round-trip through the schema to validate shape, then emit camelCase.
    return JSONResponse(
        UsageSummaryResponse.model_validate(summary).model_dump(by_alias=True)
    )


# ─── GET /api/platform ───────────────────────────────────────────────────────
@router.get("/platform")
async def platform() -> JSONResponse:
    """Server host platform (UI hint only)."""
    return JSONResponse(
        PlatformResponse(
            platform="windows" if IS_WINDOWS else "posix"
        ).model_dump(by_alias=True)
    )


# ─── GET /api/connection-hints ───────────────────────────────────────────────
@router.get("/connection-hints")
async def connection_hints(request: Request) -> JSONResponse:
    """LAN / tailscale / local connection hints for the companion app."""
    url = request.url
    settings = await get_app_settings()
    local_port = str(url.port or os.environ.get("PORT") or "3000")
    remote_port = (
        local_port
        if settings.companion_mode == "off"
        else str(DEFAULT_COMPANION_PORT)
    )
    protocol = f"{url.scheme}:"
    payload = ConnectionHintsResponse(
        hints=get_connection_hints(
            protocol=protocol, remote_port=remote_port, local_port=local_port
        ),
        companion_mode=settings.companion_mode,
        mobile_device_token_configured=bool(settings.mobile_device_token),
    )
    return JSONResponse(payload.model_dump(by_alias=True))


# ─── GET /api/deployments/{id}/download/{kind} ───────────────────────────────
@router.get("/deployments/{deployment_id}/download/{kind}")
async def download_deployment(deployment_id: str, kind: str) -> Response:
    """Download a deployment as a source or container zip."""
    if kind == "source":
        download = deployment_service.build_deployment_source_zip(deployment_id)
    elif kind == "container":
        download = deployment_service.build_deployment_container_zip(deployment_id)
    else:
        download = None

    if download is None:
        status = 404 if kind in ("source", "container") else 400
        error = "Deployment not found" if status == 404 else "Invalid download kind"
        return JSONResponse({"error": error}, status_code=status)

    filename = quote(download.file_name)
    return Response(
        content=download.body,
        media_type=download.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )
