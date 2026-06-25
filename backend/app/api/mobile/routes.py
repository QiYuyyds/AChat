"""Mobile companion API routes (spec 14).

Thin wrappers over the same services the desktop routes use, plus:

- a bearer-token auth gate (``AGENTHUB_MOBILE_TOKEN`` / ``AGENTHUB_MOBILE_DEV_TOKEN``),
- permissive CORS for the Capacitor/localhost companion app,
- trimmed (mobile-shaped) payloads for the snapshot / conversation-detail / artifact views.

The aggregation + projection helpers below port ``src/server/mobile-service.ts``
(no dedicated Python ``mobile_service`` exists); the routes themselves stay thin.
"""

from __future__ import annotations

import hmac
import os
import re
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.engine import get_db
from app.db.models import Agent, AgentRun, Artifact
from app.schemas.dispatch import AskUserAnswer, PendingQuestion, PendingWrite
from app.services import conversation_service
from app.services.pending_questions import pending_questions
from app.services.pending_writes import pending_writes

router = APIRouter()

APP_VERSION = "0.1.0"

# ─── CORS ───────────────────────────────────────────────────────────────────
_BUILTIN_ALLOWED_ORIGINS = frozenset(
    {
        "capacitor://localhost",
        "ionic://localhost",
        "https://localhost",
        "http://localhost",
    }
)
_LOCALHOST_RE = re.compile(r"^http://(localhost|127\.0\.0\.1|\[::1\]):\d+$")


def _is_allowed_origin(origin: str) -> bool:
    if origin in _BUILTIN_ALLOWED_ORIGINS:
        return True
    if _LOCALHOST_RE.match(origin):
        return True
    configured = os.environ.get("AGENTHUB_MOBILE_ALLOWED_ORIGINS")
    if not configured:
        return False
    return origin in {item.strip() for item in configured.split(",") if item.strip()}


def _append_vary_origin(value: str | None) -> str:
    if not value:
        return "Origin"
    parts = [item.strip().lower() for item in value.split(",")]
    return value if "origin" in parts else f"{value}, Origin"


def _apply_cors(req: Request, res: Response) -> Response:
    origin = req.headers.get("origin")
    if origin and _is_allowed_origin(origin):
        res.headers["Access-Control-Allow-Origin"] = origin
    res.headers["Vary"] = _append_vary_origin(res.headers.get("Vary"))
    res.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    res.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, Accept"
    res.headers["Access-Control-Max-Age"] = "600"
    return res


def _mobile_json(req: Request, body: Any, status: int = 200) -> JSONResponse:
    return _apply_cors(req, JSONResponse(content=body, status_code=status))


# ─── Auth ───────────────────────────────────────────────────────────────────
def _expected_token() -> str | None:
    primary = os.environ.get("AGENTHUB_MOBILE_TOKEN")
    if primary and primary.strip():
        return primary.strip()
    dev = os.environ.get("AGENTHUB_MOBILE_DEV_TOKEN")
    if dev and dev.strip():
        return dev.strip()
    return None


def _read_bearer(header: str | None) -> str | None:
    if not header:
        return None
    parts = header.strip().split()
    if len(parts) < 2 or parts[0] != "Bearer" or not parts[1]:
        return None
    return parts[1]


def _require_mobile_auth(req: Request) -> JSONResponse | None:
    expected = _expected_token()
    if not expected:
        return _mobile_json(
            req,
            {"error": "Mobile companion is not configured on the desktop host"},
            status=503,
        )
    actual = _read_bearer(req.headers.get("authorization"))
    if not actual or not hmac.compare_digest(actual, expected):
        return _mobile_json(req, {"error": "Unauthorized"}, status=401)
    return None


# ─── Request bodies ─────────────────────────────────────────────────────────
class _ContentBody(BaseModel):
    content: str = Field(min_length=1, max_length=12000)


class _PendingWriteActionBody(BaseModel):
    action: str  # "approve" | "reject"


# ─── Projection helpers (port of mobile-service.ts) ─────────────────────────
def _to_mobile_run(run: AgentRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "conversationId": run.conversation_id,
        "agentId": run.agent_id,
        "status": run.status,
        "startedAt": run.started_at,
    }


def _to_mobile_pending_write(write: PendingWrite) -> dict[str, Any]:
    return {
        "id": write.id,
        "conversationId": write.conversation_id,
        "agentId": write.agent_id,
        "runId": write.run_id,
        "path": write.path,
        "oldContent": write.old_content,
        "newContent": write.new_content,
        "createdAt": write.created_at,
    }


def _to_mobile_pending_question(question: PendingQuestion) -> dict[str, Any]:
    return {
        "id": question.id,
        "conversationId": question.conversation_id,
        "agentId": question.agent_id,
        "runId": question.run_id,
        "questions": [
            {
                "question": item.question,
                "header": item.header,
                "options": [
                    {"label": opt.label, "description": opt.description}
                    for opt in item.options
                ],
                "multiSelect": item.multi_select,
            }
            for item in question.questions
        ],
        "createdAt": question.created_at,
    }


def _to_mobile_message_part(part: dict[str, Any]) -> dict[str, Any]:
    ptype = part.get("type")
    if ptype == "text":
        return {"type": "text", "content": part.get("content", "")}
    if ptype == "code":
        return {
            "type": "code",
            "language": part.get("language", ""),
            "content": part.get("content", ""),
        }
    if ptype == "thinking":
        return {"type": "thinking", "content": part.get("content", "")}
    if ptype == "tool_use":
        return {
            "type": "tool_use",
            "callId": part.get("callId"),
            "toolName": part.get("toolName"),
        }
    if ptype == "tool_result":
        return {
            "type": "tool_result",
            "callId": part.get("callId"),
            "isError": part.get("isError"),
        }
    if ptype == "artifact_ref":
        return {"type": "artifact_ref", "artifactId": part.get("artifactId")}
    if ptype == "deploy_status":
        dep = part.get("deployment", {})
        # TS spreads optional deployment fields; JSON.stringify drops undefined ones.
        # Mirror that: omit absent keys rather than emitting null (frontend wire match).
        out = {
            "type": "deploy_status",
            "title": dep.get("title"),
            "version": dep.get("version"),
            "sourceType": dep.get("sourceType"),
            "workspacePath": dep.get("workspacePath"),
            "previewPath": dep.get("previewPath"),
            "status": dep.get("status"),
            "error": dep.get("error"),
        }
        return {k: v for k, v in out.items() if v is not None}
    if ptype == "deploy_candidates":
        return {"type": "deploy_candidates", "candidates": part.get("candidates", [])}
    if ptype == "image_attachment":
        return {"type": "attachment", "fileName": part.get("fileName"), "kind": "image"}
    if ptype == "file_attachment":
        return {"type": "attachment", "fileName": part.get("fileName"), "kind": "file"}
    # Unknown part types are dropped (matches the exhaustive TS switch having no default).
    return {"type": ptype}


def _companion_mode() -> str:
    return "tailnet" if os.environ.get("AGENTHUB_COMPANION_MODE") == "tailnet" else "lan"


async def _list_active_runs(conversation_ids: list[str]) -> list[AgentRun]:
    if not conversation_ids:
        return []
    async with get_db() as db:
        result = await db.execute(
            select(AgentRun)
            .where(
                AgentRun.conversation_id.in_(conversation_ids),
                AgentRun.status.in_(["queued", "running"]),
            )
            .order_by(AgentRun.started_at.desc())
        )
        return list(result.scalars().all())


async def _list_agents_ordered() -> list[Agent]:
    async with get_db() as db:
        result = await db.execute(select(Agent).order_by(Agent.created_at.asc()))
        return list(result.scalars().all())


async def _build_snapshot() -> dict[str, Any]:
    conversations = await conversation_service.list_conversations()
    agents = await _list_agents_ordered()
    conv_ids = [c.id for c in conversations]
    running_runs = await _list_active_runs(conv_ids)

    writes_by_conv = {c.id: pending_writes.list_by_conversation(c.id) for c in conversations}
    questions_by_conv = {
        c.id: pending_questions.list_by_conversation(c.id) for c in conversations
    }

    def conv_summary(conv: Any) -> dict[str, Any]:
        return {
            "id": conv.id,
            "title": conv.title,
            "mode": conv.mode,
            "updatedAt": conv.updated_at,
            "runningRunCount": sum(1 for r in running_runs if r.conversation_id == conv.id),
            "pendingWriteCount": len(writes_by_conv.get(conv.id, [])),
            "pendingQuestionCount": len(questions_by_conv.get(conv.id, [])),
        }

    all_writes = [w for ws in writes_by_conv.values() for w in ws]
    all_questions = [q for qs in questions_by_conv.values() for q in qs]

    return {
        "conversations": [conv_summary(c) for c in conversations],
        "agents": [
            {
                "id": a.id,
                "name": a.name,
                "avatar": a.avatar,
                "description": a.description,
                "isOrchestrator": a.is_orchestrator,
            }
            for a in agents
        ],
        "runningRuns": [_to_mobile_run(r) for r in running_runs],
        "pendingWrites": [_to_mobile_pending_write(w) for w in all_writes],
        "pendingQuestions": [_to_mobile_pending_question(q) for q in all_questions],
        "server": {"version": APP_VERSION, "companionMode": _companion_mode()},
    }


def _extract_artifact_ids(messages: list[Any]) -> list[str]:
    seen: dict[str, None] = {}
    for msg in messages:
        for part in msg.parts:
            if part.get("type") == "artifact_ref" and part.get("artifactId"):
                seen.setdefault(part["artifactId"], None)
    return list(seen.keys())


async def _list_artifact_summaries(artifact_ids: list[str]) -> list[dict[str, Any]]:
    if not artifact_ids:
        return []
    async with get_db() as db:
        result = await db.execute(
            select(Artifact).where(Artifact.id.in_(artifact_ids))
        )
        rows = {row.id: row for row in result.scalars().all()}
    summaries = []
    for aid in artifact_ids:
        art = rows.get(aid)
        if art is None:
            continue
        summaries.append(
            {
                "id": art.id,
                "type": art.type,
                "title": art.title,
                "version": art.version,
                "createdAt": art.created_at,
            }
        )
    return summaries


async def _build_conversation_detail(conversation_id: str) -> dict[str, Any]:
    conversations = await conversation_service.list_conversations()
    conversation = next((c for c in conversations if c.id == conversation_id), None)
    if conversation is None:
        raise ValueError(f"Conversation not found: {conversation_id}")

    messages = await conversation_service.list_messages(conversation_id)
    running_runs = await _list_active_runs([conversation_id])
    artifacts = await _list_artifact_summaries(_extract_artifact_ids(messages))
    writes = pending_writes.list_by_conversation(conversation_id)
    questions = pending_questions.list_by_conversation(conversation_id)

    return {
        "conversation": {
            "id": conversation.id,
            "title": conversation.title,
            "mode": conversation.mode,
            "agentIds": conversation.agent_ids,
            "updatedAt": conversation.updated_at,
        },
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "agentId": m.agent_id,
                "parts": [_to_mobile_message_part(p) for p in m.parts],
                "status": m.status,
                "createdAt": m.created_at,
            }
            for m in messages
        ],
        "artifacts": artifacts,
        "runningRuns": [_to_mobile_run(r) for r in running_runs],
        "pendingWrites": [_to_mobile_pending_write(w) for w in writes],
        "pendingQuestions": [_to_mobile_pending_question(q) for q in questions],
    }


async def _get_mobile_artifact(artifact_id: str) -> dict[str, Any]:
    async with get_db() as db:
        result = await db.execute(select(Artifact).where(Artifact.id == artifact_id))
        art = result.scalar_one_or_none()
    if art is None:
        raise ValueError(f"Artifact not found: {artifact_id}")
    return {
        "id": art.id,
        "conversationId": art.conversation_id,
        "type": art.type,
        "title": art.title,
        "content": art.content_dict,
        "version": art.version,
        "parentArtifactId": art.parent_artifact_id or None,
        "createdByAgentId": art.created_by_agent_id,
        "createdAt": art.created_at,
    }


async def _parse_json_body(req: Request) -> Any:
    try:
        return await req.json()
    except Exception:
        return None


# ─── OPTIONS (CORS preflight) ───────────────────────────────────────────────
@router.options("/mobile/{rest:path}")
async def mobile_options(req: Request, rest: str) -> Response:
    return _apply_cors(req, Response(status_code=204))


# ─── GET /api/mobile/snapshot ───────────────────────────────────────────────
@router.get("/mobile/snapshot")
async def mobile_snapshot(req: Request) -> Response:
    auth_error = _require_mobile_auth(req)
    if auth_error:
        return auth_error
    snapshot = await _build_snapshot()
    return _mobile_json(req, snapshot)


# ─── GET /api/mobile/conversations/{id} ─────────────────────────────────────
@router.get("/mobile/conversations/{conversation_id}")
async def mobile_conversation_detail(req: Request, conversation_id: str) -> Response:
    auth_error = _require_mobile_auth(req)
    if auth_error:
        return auth_error
    try:
        detail = await _build_conversation_detail(conversation_id)
    except Exception as err:  # noqa: BLE001
        return _mobile_json(req, {"error": str(err)}, status=404)
    return _mobile_json(req, detail)


# ─── POST /api/mobile/conversations/{id}/messages ───────────────────────────
@router.post("/mobile/conversations/{conversation_id}/messages")
async def mobile_send_message(req: Request, conversation_id: str) -> Response:
    auth_error = _require_mobile_auth(req)
    if auth_error:
        return auth_error
    raw = await _parse_json_body(req)
    try:
        body = _ContentBody.model_validate(raw)
    except Exception as err:  # noqa: BLE001
        return _mobile_json(
            req, {"error": "Invalid body", "issues": _issues(err)}, status=400
        )
    try:
        result = await conversation_service.send_message(
            conversation_id=conversation_id, content=body.content
        )
    except Exception as err:  # noqa: BLE001
        return _mobile_json(req, {"error": str(err)}, status=400)
    return _mobile_json(
        req, {"messageId": result.message_id, "runIds": result.run_ids}, status=202
    )


# ─── POST /api/mobile/conversations/{id}/messages/{messageId}/edit ──────────
@router.post("/mobile/conversations/{conversation_id}/messages/{message_id}/edit")
async def mobile_edit_message(
    req: Request, conversation_id: str, message_id: str
) -> Response:
    auth_error = _require_mobile_auth(req)
    if auth_error:
        return auth_error
    raw = await _parse_json_body(req)
    try:
        body = _ContentBody.model_validate(raw)
    except Exception as err:  # noqa: BLE001
        return _mobile_json(
            req, {"error": "Invalid body", "issues": _issues(err)}, status=400
        )
    try:
        result = await conversation_service.edit_and_resend_latest_user_message(
            conversation_id, message_id, body.content
        )
    except Exception as err:  # noqa: BLE001
        return _mobile_json(req, {"error": str(err)}, status=400)
    return _mobile_json(
        req,
        {
            "deletedMessageIds": result.deleted_message_ids,
            "deletedArtifactIds": result.deleted_artifact_ids,
            "newMessage": result.new_message.model_dump(by_alias=True),
            "runIds": result.run_ids,
        },
        status=200,
    )


# ─── POST /api/mobile/conversations/{id}/messages/{messageId}/withdraw ──────
@router.post("/mobile/conversations/{conversation_id}/messages/{message_id}/withdraw")
async def mobile_withdraw_message(
    req: Request, conversation_id: str, message_id: str
) -> Response:
    auth_error = _require_mobile_auth(req)
    if auth_error:
        return auth_error
    try:
        result = await conversation_service.withdraw_latest_user_message(
            conversation_id, message_id
        )
    except Exception as err:  # noqa: BLE001
        return _mobile_json(req, {"error": str(err)}, status=400)
    return _mobile_json(
        req,
        {
            "deletedMessageIds": result.deleted_message_ids,
            "deletedArtifactIds": result.deleted_artifact_ids,
        },
        status=200,
    )


# ─── POST /api/mobile/conversations/{id}/regenerate ─────────────────────────
@router.post("/mobile/conversations/{conversation_id}/regenerate")
async def mobile_regenerate(req: Request, conversation_id: str) -> Response:
    auth_error = _require_mobile_auth(req)
    if auth_error:
        return auth_error
    try:
        result = await conversation_service.regenerate_latest_response(conversation_id)
    except Exception as err:  # noqa: BLE001
        return _mobile_json(req, {"error": str(err)}, status=400)
    return _mobile_json(
        req,
        {
            "deletedMessageIds": result.deleted_message_ids,
            "deletedArtifactIds": result.deleted_artifact_ids,
            "triggerMessageId": result.trigger_message_id,
            "runIds": result.run_ids,
        },
        status=202,
    )


# ─── GET /api/mobile/artifacts/{id} ─────────────────────────────────────────
@router.get("/mobile/artifacts/{artifact_id}")
async def mobile_artifact(req: Request, artifact_id: str) -> Response:
    auth_error = _require_mobile_auth(req)
    if auth_error:
        return auth_error
    try:
        artifact = await _get_mobile_artifact(artifact_id)
    except Exception as err:  # noqa: BLE001
        return _mobile_json(req, {"error": str(err)}, status=404)
    return _mobile_json(req, {"artifact": artifact})


# ─── POST /api/mobile/pending-questions/{id} ────────────────────────────────
@router.post("/mobile/pending-questions/{pending_id}")
async def mobile_answer_pending_question(req: Request, pending_id: str) -> Response:
    auth_error = _require_mobile_auth(req)
    if auth_error:
        return auth_error
    raw = await _parse_json_body(req)
    if not isinstance(raw, dict) or not isinstance(raw.get("answers"), dict):
        return _mobile_json(
            req, {"error": "Invalid body", "issues": ["answers is required"]}, status=400
        )
    try:
        answers = {
            key: AskUserAnswer.model_validate(value)
            for key, value in raw["answers"].items()
        }
    except Exception as err:  # noqa: BLE001
        return _mobile_json(
            req, {"error": "Invalid body", "issues": _issues(err)}, status=400
        )

    existing = pending_questions.get(pending_id)
    if not existing:
        return _mobile_json(req, {"error": "Pending question not found"}, status=404)

    ok = pending_questions.answer(pending_id, answers)
    if not ok:
        return _mobile_json(req, {"error": "Failed to record answer"}, status=500)
    return _mobile_json(req, {"ok": True})


# ─── POST /api/mobile/pending-writes/{id} ───────────────────────────────────
@router.post("/mobile/pending-writes/{pending_id}")
async def mobile_resolve_pending_write(req: Request, pending_id: str) -> Response:
    auth_error = _require_mobile_auth(req)
    if auth_error:
        return auth_error
    raw = await _parse_json_body(req)
    try:
        body = _PendingWriteActionBody.model_validate(raw)
    except Exception as err:  # noqa: BLE001
        return _mobile_json(
            req, {"error": "Invalid body", "issues": _issues(err)}, status=400
        )
    if body.action not in ("approve", "reject"):
        return _mobile_json(
            req,
            {"error": "Invalid body", "issues": ["action must be approve|reject"]},
            status=400,
        )

    existing = pending_writes.get(pending_id)
    if not existing:
        return _mobile_json(req, {"error": "Pending write not found"}, status=404)

    ok = (
        pending_writes.approve(pending_id)
        if body.action == "approve"
        else pending_writes.reject(pending_id)
    )
    if not ok:
        return _mobile_json(req, {"error": "Failed to process pending write"}, status=500)
    return _mobile_json(req, {"ok": True})


def _issues(err: Exception) -> list[Any]:
    """Best-effort extraction of pydantic validation issues (mirrors zod issues array)."""
    errors = getattr(err, "errors", None)
    if callable(errors):
        try:
            return [
                {"path": list(e.get("loc", [])), "message": e.get("msg", "")}
                for e in errors()
            ]
        except Exception:  # noqa: BLE001
            return [str(err)]
    return [str(err)]
