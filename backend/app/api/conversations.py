"""Conversations API routes.

Thin HTTP layer over conversation_service / deploy_command_service /
context_compaction_service. Wire format is camelCase; service results are
Pydantic models or dataclasses of Pydantic models, serialized via by_alias.
"""

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from app.auth.dependencies import get_current_user
from app.auth.ownership import verify_conversation_ownership
from app.db.models import User
from app.schemas import (
    CreateConversationRequest,
    SendMessageRequest,
    SetRagModeRequest,
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
async def list_conversations(user: User = Depends(get_current_user)) -> JSONResponse:
    conversations = await conversation_service.list_conversations(user_id=user.id)
    return JSONResponse(content={"conversations": _model(conversations)})


@router.post("/conversations")
async def create_conversation(req: Request, user: User = Depends(get_current_user)) -> JSONResponse:
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
            code_intelligence_enabled=body.code_intelligence_enabled,
            dispatch_mode=body.dispatch_mode,
            user_id=user.id,
        )
    except ValueError as err:
        return _err(str(err), 400)
    return JSONResponse(status_code=201, content={"conversation": _model(conversation)})


# ─── /conversations/{id} ─────────────────────────────────────────────────────
@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, user: User = Depends(get_current_user)) -> JSONResponse:
    await verify_conversation_ownership(conversation_id, user.id)
    try:
        await conversation_service.delete_conversation(conversation_id)
    except ValueError as err:
        return _err(str(err), 404)
    return JSONResponse(content={"ok": True})


@router.patch("/conversations/{conversation_id}")
async def update_conversation(conversation_id: str, req: Request, user: User = Depends(get_current_user)) -> JSONResponse:
    await verify_conversation_ownership(conversation_id, user.id)
    raw = await _read_json(req)
    title = raw.get("title") if isinstance(raw, dict) else None
    summary = raw.get("summary") if isinstance(raw, dict) else None
    add_agent_ids = raw.get("addAgentIds") if isinstance(raw, dict) else None
    fs_mode = raw.get("fsWriteApprovalMode") if isinstance(raw, dict) else None
    toggle_pin = raw.get("togglePin") if isinstance(raw, dict) else None
    toggle_archive = raw.get("toggleArchive") if isinstance(raw, dict) else None
    dispatch_mode = raw.get("dispatchMode") if isinstance(raw, dict) else None

    # Mirror the zod refine: at least one recognized field is required.
    if (
        not isinstance(raw, dict)
        or (
            title is None
            and summary is None
            and add_agent_ids is None
            and fs_mode is None
            and toggle_pin is None
            and toggle_archive is None
            and dispatch_mode is None
        )
    ):
        return JSONResponse(
            status_code=400,
            content={
                "error": "Invalid body",
                "issues": [
                    {
                        "message": (
                            "At least one of addAgentIds / title / summary / "
                            "fsWriteApprovalMode / togglePin / toggleArchive / "
                            "dispatchMode is required"
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
    if summary is not None and (
        not isinstance(summary, str) or len(summary) > 100
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
    if dispatch_mode is not None and dispatch_mode not in ("solo", "orchestrated"):
        return _err("Invalid body", 400)

    try:
        conversation = None
        if title is not None:
            conversation = await conversation_service.rename_conversation(
                conversation_id, title
            )
        if summary is not None:
            conversation = await conversation_service.update_conversation_summary(
                conversation_id, summary
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
        if dispatch_mode is not None:
            conversation = await conversation_service.set_dispatch_mode(
                conversation_id, dispatch_mode
            )
    except ValueError as err:
        return _err(str(err), 400)
    return JSONResponse(content={"conversation": _model(conversation)})


# ─── /conversations/{id}/rag-mode ────────────────────────────────────────────
@router.patch("/conversations/{conversation_id}/rag-mode")
async def set_rag_mode(conversation_id: str, req: Request, user: User = Depends(get_current_user)) -> JSONResponse:
    await verify_conversation_ownership(conversation_id, user.id)
    raw = await _read_json(req)
    try:
        body = SetRagModeRequest.model_validate(raw)
    except ValidationError as exc:
        return _invalid_body(exc)

    try:
        conversation = await conversation_service.set_rag_mode(
            conversation_id, body.rag_enabled
        )
    except ValueError as err:
        return _err(str(err), 404)
    return JSONResponse(content={"conversation": _model(conversation)})


# ─── /conversations/{id}/messages ────────────────────────────────────────────
@router.get("/conversations/{conversation_id}/messages")
async def list_messages(conversation_id: str, user: User = Depends(get_current_user)) -> JSONResponse:
    # Desktop: same as POST — mirror cloud conversation before local ownership.
    try:
        from app.desktop.runtime import is_desktop_mode

        if is_desktop_mode():
            from app.desktop.mirror import ensure_conversation_context

            await ensure_conversation_context(conversation_id, user.id)
    except ValueError as err:
        msg = str(err)
        status = 404 if "not found" in msg.lower() else 400
        return _err(msg, status)
    except Exception:
        pass

    await verify_conversation_ownership(conversation_id, user.id)
    messages = await conversation_service.list_messages(conversation_id)
    return JSONResponse(content={"messages": _model(messages)})


@router.post("/conversations/{conversation_id}/messages")
async def send_message(conversation_id: str, req: Request, user: User = Depends(get_current_user)) -> JSONResponse:
    # Desktop: cloud conversations live on official API; mirror into local engine DB
    # before ownership checks (local SQLite starts empty each engine process).
    try:
        from app.desktop.runtime import is_desktop_mode

        if is_desktop_mode():
            from app.desktop.mirror import ensure_conversation_context

            await ensure_conversation_context(conversation_id, user.id)
    except ValueError as err:
        msg = str(err)
        status = 404 if "not found" in msg.lower() else 400
        return _err(msg, status)
    except Exception:
        # Non-desktop import paths / transient cloud errors: fall through to ownership.
        pass

    await verify_conversation_ownership(conversation_id, user.id)
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
            user_id=user.id,
        )
    except ValueError as err:
        msg = str(err)
        status = 404 if msg.startswith("Conversation not found") else 400
        return _err(msg, status)
    return JSONResponse(status_code=202, content=_send_message_result(result))


@router.delete("/conversations/{conversation_id}/messages")
async def clear_conversation_history(conversation_id: str, user: User = Depends(get_current_user)) -> JSONResponse:
    await verify_conversation_ownership(conversation_id, user.id)
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
async def regenerate(conversation_id: str, user: User = Depends(get_current_user)) -> JSONResponse:
    await verify_conversation_ownership(conversation_id, user.id)
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
async def compact(conversation_id: str, user: User = Depends(get_current_user)) -> JSONResponse:
    await verify_conversation_ownership(conversation_id, user.id)
    import logging

    from app.services import context_compaction_service

    _log = logging.getLogger(__name__)
    try:
        result = await context_compaction_service.compact_conversation(conversation_id)
    except context_compaction_service.CompactionSkipped as skip:
        return JSONResponse(
            content={
                "skipped": True,
                "reason": skip.reason,
                "message": skip.message.model_dump(by_alias=True) if skip.message else None,
            }
        )
    except ValueError as err:
        _log.warning("[compact] 400 for conv=%s: %s", conversation_id, err)
        return _err(str(err), 400)
    except Exception as err:  # noqa: BLE001 - surface unexpected failures clearly
        _log.exception("[compact] unexpected error for conv=%s", conversation_id)
        return _err(f"压缩失败：{err}", 500)
    return JSONResponse(
        content={
            "summary": result.summary.model_dump(by_alias=True),
            "message": result.message.model_dump(by_alias=True) if result.message else None,
            "ctxBefore": result.ctx_before,
            "ctxAfter": result.ctx_after,
        }
    )


# ─── /conversations/{id}/deploy ──────────────────────────────────────────────
@router.get("/conversations/{conversation_id}/deploy")
async def list_deploy(conversation_id: str, user: User = Depends(get_current_user)) -> JSONResponse:
    await verify_conversation_ownership(conversation_id, user.id)
    try:
        candidates = await deploy_command_service.list_deploy_candidates(
            conversation_id
        )
    except ValueError as err:
        return _err(str(err), 400)
    return JSONResponse(content={"candidates": _model(candidates)})


@router.post("/conversations/{conversation_id}/deploy")
async def deploy(conversation_id: str, req: Request, user: User = Depends(get_current_user)) -> JSONResponse:
    await verify_conversation_ownership(conversation_id, user.id)
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
