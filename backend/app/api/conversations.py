"""Conversations API routes.

Thin HTTP layer over conversation_service / deploy_command_service /
context_compaction_service. Wire format is camelCase; service results are
Pydantic models or dataclasses of Pydantic models, serialized via by_alias.
"""

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from app.schemas import (
    CreateConversationRequest,
    SendMessageRequest,
)
from app.services import conversation_service, deploy_command_service

router = APIRouter()


def _model(value: Any) -> Any:
    """Serialize a Pydantic model (or list / scalar) to a camelCase wire value."""
    if isinstance(value, BaseModel):
        return value.model_dump(by_alias=True)
    if isinstance(value, list):
        return [_model(v) for v in value]
    return value


def _invalid_body(exc: ValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"error": "Invalid body", "issues": exc.errors()},
    )


def _err(message: str, status: int) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message})


async def _read_json(req: Request) -> Any:
    """Mirror TS ``req.json().catch(() => null)`` — malformed body becomes None."""
    try:
        return await req.json()
    except Exception:  # noqa: BLE001 - any parse failure maps to a None body
        return None


# ─── /conversations ──────────────────────────────────────────────────────────
@router.get("/conversations")
async def list_conversations() -> JSONResponse:
    conversations = await conversation_service.list_conversations()
    return JSONResponse(content={"conversations": _model(conversations)})


@router.post("/conversations")
async def create_conversation(req: Request) -> JSONResponse:
    raw = await _read_json(req)
    try:
        body = CreateConversationRequest.model_validate(raw)
    except ValidationError as exc:
        return _invalid_body(exc)

    try:
        conversation = await conversation_service.create_conversation(
            mode=body.mode,
            agent_ids=body.agent_ids,
            title=body.title,
            bound_path=body.bound_path,
        )
    except ValueError as err:
        return _err(str(err), 400)
    return JSONResponse(status_code=201, content={"conversation": _model(conversation)})


# ─── /conversations/{id} ─────────────────────────────────────────────────────
@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str) -> JSONResponse:
    try:
        await conversation_service.delete_conversation(conversation_id)
    except ValueError as err:
        return _err(str(err), 404)
    return JSONResponse(content={"ok": True})


@router.patch("/conversations/{conversation_id}")
async def update_conversation(conversation_id: str, req: Request) -> JSONResponse:
    raw = await _read_json(req)
    title = raw.get("title") if isinstance(raw, dict) else None
    add_agent_ids = raw.get("addAgentIds") if isinstance(raw, dict) else None
    fs_mode = raw.get("fsWriteApprovalMode") if isinstance(raw, dict) else None
    toggle_pin = raw.get("togglePin") if isinstance(raw, dict) else None
    toggle_archive = raw.get("toggleArchive") if isinstance(raw, dict) else None

    # Mirror the zod refine: at least one recognized field is required.
    if (
        not isinstance(raw, dict)
        or (
            title is None
            and add_agent_ids is None
            and fs_mode is None
            and toggle_pin is None
            and toggle_archive is None
        )
    ):
        return JSONResponse(
            status_code=400,
            content={
                "error": "Invalid body",
                "issues": [
                    {
                        "message": (
                            "At least one of addAgentIds / title / "
                            "fsWriteApprovalMode / togglePin / toggleArchive "
                            "is required"
                        )
                    }
                ],
            },
        )

    # Validate field shapes (mirror zod constraints).
    if title is not None and (
        not isinstance(title, str) or not (1 <= len(title) <= 100)
    ):
        return _err("Invalid body", 400)
    if add_agent_ids is not None and (
        not isinstance(add_agent_ids, list) or len(add_agent_ids) < 1
    ):
        return _err("Invalid body", 400)
    if fs_mode is not None and fs_mode not in ("auto", "review"):
        return _err("Invalid body", 400)
    if toggle_pin is not None and toggle_pin is not True:
        return _err("Invalid body", 400)
    if toggle_archive is not None and toggle_archive is not True:
        return _err("Invalid body", 400)

    try:
        conversation = None
        if title is not None:
            conversation = await conversation_service.rename_conversation(
                conversation_id, title
            )
        if add_agent_ids is not None:
            conversation = await conversation_service.add_agents_to_conversation(
                conversation_id, add_agent_ids
            )
        if fs_mode is not None:
            conversation = await conversation_service.set_conversation_approval_mode(
                conversation_id, fs_mode
            )
        if toggle_pin:
            conversation = await conversation_service.toggle_pin_conversation(
                conversation_id
            )
        if toggle_archive:
            conversation = await conversation_service.toggle_archive_conversation(
                conversation_id
            )
    except ValueError as err:
        return _err(str(err), 400)
    return JSONResponse(content={"conversation": _model(conversation)})


# ─── /conversations/{id}/messages ────────────────────────────────────────────
@router.get("/conversations/{conversation_id}/messages")
async def list_messages(conversation_id: str) -> JSONResponse:
    messages = await conversation_service.list_messages(conversation_id)
    return JSONResponse(content={"messages": _model(messages)})


@router.post("/conversations/{conversation_id}/messages")
async def send_message(conversation_id: str, req: Request) -> JSONResponse:
    raw = await _read_json(req)
    try:
        body = SendMessageRequest.model_validate(raw)
    except ValidationError as exc:
        return _invalid_body(exc)

    content = body.content or ""
    attachment_ids = body.attachment_ids or []
    # Mirror zod refine: content (trimmed) or at least one attachment required.
    if not content.strip() and len(attachment_ids) == 0:
        return JSONResponse(
            status_code=400,
            content={
                "error": "Invalid body",
                "issues": [{"message": "必须提供 content 或 attachmentIds 之一"}],
            },
        )

    try:
        result = await conversation_service.send_message(
            conversation_id=conversation_id,
            content=content,
            mentioned_agent_ids=body.mentioned_agent_ids,
            parent_message_id=body.parent_message_id,
            attachment_ids=body.attachment_ids,
        )
    except ValueError as err:
        return _err(str(err), 400)
    return JSONResponse(status_code=202, content=_send_message_result(result))


@router.delete("/conversations/{conversation_id}/messages")
async def clear_conversation_history(conversation_id: str) -> JSONResponse:
    try:
        result = await conversation_service.clear_conversation_history(conversation_id)
    except ValueError as err:
        message = str(err)
        if message.startswith("Conversation not found"):
            status = 404
        elif "agent runs are active" in message:
            status = 409
        else:
            status = 400
        return _err(message, status)
    return JSONResponse(
        content={
            "conversation": _model(result.conversation),
            "deletedMessageCount": result.deleted_message_count,
            "deletedRunCount": result.deleted_run_count,
            "deletedSummaryCount": result.deleted_summary_count,
        }
    )


# ─── /conversations/{id}/regenerate ──────────────────────────────────────────
@router.post("/conversations/{conversation_id}/regenerate")
async def regenerate(conversation_id: str) -> JSONResponse:
    try:
        result = await conversation_service.regenerate_latest_response(conversation_id)
    except ValueError as err:
        return _err(str(err), 400)
    return JSONResponse(
        content={
            "deletedMessageIds": result.deleted_message_ids,
            "deletedArtifactIds": result.deleted_artifact_ids,
            "triggerMessageId": result.trigger_message_id,
            "runIds": result.run_ids,
        }
    )


# ─── /conversations/{id}/compact ─────────────────────────────────────────────
@router.post("/conversations/{conversation_id}/compact")
async def compact(conversation_id: str) -> JSONResponse:
    # The full compactConversation flow (LLM summary) is DEFERRED in the Python
    # context_compaction_service port; no callable exists yet. Fail with the same
    # 400 error shape the TS route uses for service errors.
    return _err("Context compaction is not yet implemented", 400)


# ─── /conversations/{id}/deploy ──────────────────────────────────────────────
@router.get("/conversations/{conversation_id}/deploy")
async def list_deploy(conversation_id: str) -> JSONResponse:
    try:
        candidates = await deploy_command_service.list_deploy_candidates(
            conversation_id
        )
    except ValueError as err:
        return _err(str(err), 400)
    return JSONResponse(content={"candidates": _model(candidates)})


@router.post("/conversations/{conversation_id}/deploy")
async def deploy(conversation_id: str, req: Request) -> JSONResponse:
    raw = await _read_json(req)
    if not isinstance(raw, dict):
        raw = {}
    artifact_id = raw.get("artifactId")
    if artifact_id is not None and (
        not isinstance(artifact_id, str) or len(artifact_id) < 1
    ):
        return _err("Invalid body", 400)

    try:
        result = await deploy_command_service.handle_deploy_command(
            conversation_id=conversation_id,
            artifact_id=artifact_id,
        )
    except ValueError as err:
        return _err(str(err), 400)
    return JSONResponse(content=_deploy_result(result))


# ─── Result serializers ──────────────────────────────────────────────────────
def _send_message_result(result: conversation_service.SendMessageResult) -> dict:
    out: dict[str, Any] = {
        "messageId": result.message_id,
        "runIds": result.run_ids,
    }
    if result.messages is not None:
        out["messages"] = _model(result.messages)
    if result.deploy is not None:
        out["deploy"] = _deploy_result(result.deploy)
    return out


def _deploy_result(result: deploy_command_service.DeployCommandResult) -> dict:
    out: dict[str, Any] = {
        "kind": result.kind,
        "message": _model(result.message),
    }
    if result.candidates is not None:
        out["candidates"] = _model(result.candidates)
    if result.deployment is not None:
        out["deployment"] = _model(result.deployment)
    return out
