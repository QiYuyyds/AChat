"""Misc API routes.

Ports the standalone Next.js routes that don't belong to a larger resource
group: run abort, message search, usage summary, host platform, companion
connection hints, and deployment zip downloads.

The internal Codex tool-bridge route (POST /api/internal/agenthub-tools) is
intentionally NOT ported here — see the agent's deferral report (no Codex
adapter / internal-tool-auth module exists in the Python backend yet).
"""

import asyncio
import os
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select

from app.auth.dependencies import get_current_user
from app.auth.ownership import verify_conversation_ownership
from app.db.engine import get_local_db
from app.db.models import AgentRun, User
from app.schemas import (
    ConnectionHintsResponse,
    PlatformResponse,
    SearchHit,
    SearchResponse,
    UsageSummaryResponse,
    UsageTimeseriesResponse,
)
from app.services import conversation_service, deployment_service
from app.services.network_hints import get_connection_hints
from app.services.search_service import search_messages
from app.services.settings_service import DEFAULT_COMPANION_PORT, get_user_settings
from app.services.usage_summary_service import get_usage_summary, get_usage_timeseries
from app.utils.clock import now_ms
from app.utils.platform import IS_WINDOWS

router = APIRouter()

# How far back the runs list reaches for terminal runs without dispatch data
# (non-terminal runs and dispatch-carrying runs are always included).
RECENT_RUN_WINDOW_MS = 24 * 60 * 60 * 1000


# ─── GET /api/conversations/{id}/runs ────────────────────────────────────────
@router.get("/conversations/{conversation_id}/runs")
async def list_conversation_runs(
    conversation_id: str, user: User = Depends(get_current_user)
) -> JSONResponse:
    """List a conversation's active and recent runs for frontend run-state rebuild.

    In-memory active runs are the live truth (merged over DB rows by run id);
    DB rows back non-terminal runs left by a previous process and recent /
    dispatch-carrying terminal runs. Dispatch plan/results come from the
    persisted JSONB columns (progressively written during the run).
    """
    from app.services.agent_runner import get_active_runs_for_conversation

    await verify_conversation_ownership(conversation_id, user.id)

    cutoff = now_ms() - RECENT_RUN_WINDOW_MS
    async with get_local_db() as db:
        rows = (
            await db.execute(
                select(AgentRun).where(AgentRun.conversation_id == conversation_id)
            )
        ).scalars().all()

    # Dispatch-carrying inclusion is checked in Python: SQLite stores JSON
    # NULL as the string 'null', so a SQL IS NOT NULL predicate is not
    # portable across the dual-DB dialects.
    rows = [
        row
        for row in rows
        if row.status in ("queued", "running")
        or row.started_at >= cutoff
        or row.dispatch_plan is not None
    ]

    rows_by_id = {row.id: row for row in rows}
    merged: dict[str, dict] = {}

    def _row_dict(row: AgentRun, status: str | None = None) -> dict:
        return {
            "id": row.id,
            "conversationId": row.conversation_id,
            "agentId": row.agent_id,
            "triggerMessageId": row.trigger_message_id,
            "status": status or row.status,
            "error": row.error,
            "parentRunId": row.parent_run_id,
            "usage": row.usage_dict,
            "startedAt": row.started_at,
            "finishedAt": row.finished_at,
            "stopReason": None,
            "stopReasonLabel": None,
            "dispatchPlan": row.dispatch_plan,
            "dispatchResults": row.dispatch_results,
        }

    # Memory first: live runs keep their in-memory status, dispatch state
    # always comes from the DB row (progressively persisted JSONB).
    for mem in get_active_runs_for_conversation(conversation_id):
        row = rows_by_id.get(mem["id"])
        entry = dict(mem)
        if row is not None:
            entry["dispatchPlan"] = row.dispatch_plan
            entry["dispatchResults"] = row.dispatch_results
        merged[mem["id"]] = entry

    for run_id, row in rows_by_id.items():
        if run_id not in merged:
            merged[run_id] = _row_dict(row)

    runs = sorted(merged.values(), key=lambda r: r["startedAt"])
    return JSONResponse({"runs": runs})


# ─── POST /api/runs/{id}/abort ───────────────────────────────────────────────
@router.post("/runs/{run_id}/abort")
async def abort_run(run_id: str, user: User = Depends(get_current_user)) -> JSONResponse:
    """Abort a running agent run."""
    ok = await conversation_service.abort_run(run_id)
    if not ok:
        return JSONResponse(
            {"error": "Run not found or already finished"}, status_code=404
        )
    return JSONResponse({"ok": True})


# ─── POST /api/runs/{id}/resume ──────────────────────────────────────────────
@router.post("/runs/{run_id}/resume")
async def resume_run(run_id: str, user: User = Depends(get_current_user)) -> JSONResponse:
    """Resume an SDK agent run from its latest checkpoint."""
    from app.services.agent_runner import RunArgs, execute_run
    from app.services.checkpoint_service import load_latest_checkpoint

    async with get_local_db() as db:
        run = (
            await db.execute(select(AgentRun).where(AgentRun.id == run_id))
        ).scalar_one_or_none()
        if run is None:
            return JSONResponse(
                {"error": "Run not found"}, status_code=404
            )
        if run.status == "complete":
            return JSONResponse(
                {"error": "Cannot resume a completed run"}, status_code=409
            )
        agent_id = run.agent_id
        conversation_id = run.conversation_id
        trigger_message_id = run.trigger_message_id or ""

    checkpoint = await load_latest_checkpoint(run_id)
    if checkpoint is None:
        return JSONResponse(
            {"error": "No checkpoint available for resume"}, status_code=404
        )

    cancel_event = asyncio.Event()
    args = RunArgs(
        agent_id=agent_id,
        conversation_id=conversation_id,
        trigger_message_id=trigger_message_id,
        resume_from_checkpoint=True,
        user_id=user.id,
    )
    asyncio.create_task(execute_run(run_id, cancel_event, args))
    return JSONResponse({"ok": True, "runId": run_id, "resumedFromTurn": checkpoint.turn_number})


# ─── GET /api/runs/{id}/checkpoints ──────────────────────────────────────────
@router.get("/runs/{run_id}/checkpoints")
async def list_run_checkpoints(run_id: str, user: User = Depends(get_current_user)) -> JSONResponse:
    """List available checkpoints for a run."""
    from app.services.checkpoint_service import list_checkpoints

    checkpoints = await list_checkpoints(run_id)
    return JSONResponse({
        "checkpoints": [
            {
                "id": c.id,
                "turnNumber": c.turn_number,
                "createdAt": c.created_at,
            }
            for c in checkpoints
        ],
    })


# ─── GET /api/search ─────────────────────────────────────────────────────────
@router.get("/search")
async def search(
    user: User = Depends(get_current_user),
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
async def usage_summary(user: User = Depends(get_current_user)) -> JSONResponse:
    """Global token usage summary."""
    summary = await get_usage_summary()
    # Round-trip through the schema to validate shape, then emit camelCase.
    return JSONResponse(
        UsageSummaryResponse.model_validate(summary).model_dump(by_alias=True)
    )


# ─── GET /api/usage/timeseries ───────────────────────────────────────────────
@router.get("/usage/timeseries")
async def usage_timeseries(
    days: int = Query(default=14),
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """Daily token usage timeseries for the last ``days`` days."""
    clamped = max(1, min(90, days))
    points = await get_usage_timeseries(clamped)
    return JSONResponse(
        UsageTimeseriesResponse(
            points=points
        ).model_dump(by_alias=True)["points"]
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
async def connection_hints(request: Request, user: User = Depends(get_current_user)) -> JSONResponse:
    """LAN / tailscale / local connection hints for the companion app."""
    url = request.url
    settings = await get_user_settings(user.id)
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
async def download_deployment(deployment_id: str, kind: str, user: User = Depends(get_current_user)) -> Response:
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
