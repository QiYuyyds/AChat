"""Messages API routes.

Ports the four TS message routes under ``src/app/api/messages/[id]/``:
edit / withdraw / pin / bookmark. Each is a thin wrapper over
``conversation_service``; the service is the source of truth and manages its
own DB session, so these handlers take no ``db`` dependency.

Wire format is camelCase. Bodies are parsed manually (not via a FastAPI body
model) so that an invalid body produces the TS-compatible
``400 {"error": "Invalid body", "issues": [...]}`` shape rather than FastAPI's
default 422.
"""

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError

from app.services import conversation_service

router = APIRouter()


class _ConversationIdBody(BaseModel):
    """Body for withdraw / pin / bookmark: ``{ conversationId }``."""

    conversation_id: str = Field(alias="conversationId", min_length=1)

    model_config = {"populate_by_name": True}


class _EditBody(BaseModel):
    """Body for edit: ``{ conversationId, content }``."""

    conversation_id: str = Field(alias="conversationId", min_length=1)
    content: str = Field(min_length=1)

    model_config = {"populate_by_name": True}


async def _parse_body(req: Request, model: type[BaseModel]) -> Any:
    """Parse + validate the JSON body, or return a 400 JSONResponse on failure.

    Mirrors the TS ``Body.safeParse`` flow: malformed JSON or a body that fails
    validation both yield ``400 {"error": "Invalid body", "issues": [...]}``.
    """
    try:
        raw = await req.json()
    except Exception:
        raw = None
    try:
        return model.model_validate(raw)
    except ValidationError as err:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid body", "issues": err.errors()},
        )


def _error_status(message: str) -> int:
    return 404 if "not found" in message else 400


@router.post("/messages/{message_id}/edit")
async def edit_message(message_id: str, req: Request) -> JSONResponse:
    """Edit the latest user message and resend it with new content."""
    parsed = await _parse_body(req, _EditBody)
    if isinstance(parsed, JSONResponse):
        return parsed
    try:
        result = await conversation_service.edit_and_resend_latest_user_message(
            parsed.conversation_id, message_id, parsed.content
        )
    except ValueError as err:
        message = str(err)
        return JSONResponse(status_code=_error_status(message), content={"error": message})
    return JSONResponse(
        content={
            "deletedMessageIds": result.deleted_message_ids,
            "deletedArtifactIds": result.deleted_artifact_ids,
            "newMessage": result.new_message.model_dump(by_alias=True),
            "runIds": result.run_ids,
        }
    )


@router.post("/messages/{message_id}/withdraw")
async def withdraw_message(message_id: str, req: Request) -> JSONResponse:
    """Withdraw the latest user message and everything it triggered."""
    parsed = await _parse_body(req, _ConversationIdBody)
    if isinstance(parsed, JSONResponse):
        return parsed
    try:
        result = await conversation_service.withdraw_latest_user_message(
            parsed.conversation_id, message_id
        )
    except ValueError as err:
        message = str(err)
        return JSONResponse(status_code=_error_status(message), content={"error": message})
    return JSONResponse(
        content={
            "deletedMessageIds": result.deleted_message_ids,
            "deletedArtifactIds": result.deleted_artifact_ids,
        }
    )


@router.post("/messages/{message_id}/pin")
async def toggle_pin(message_id: str, req: Request) -> JSONResponse:
    """Toggle whether a message is pinned into the LLM long-term context."""
    parsed = await _parse_body(req, _ConversationIdBody)
    if isinstance(parsed, JSONResponse):
        return parsed
    try:
        result = await conversation_service.toggle_pinned_message(
            parsed.conversation_id, message_id
        )
    except ValueError as err:
        return JSONResponse(status_code=400, content={"error": str(err)})
    return JSONResponse(content=result)


@router.post("/messages/{message_id}/bookmark")
async def toggle_bookmark(message_id: str, req: Request) -> JSONResponse:
    """Toggle a UI bookmark on a message (does not affect LLM context)."""
    parsed = await _parse_body(req, _ConversationIdBody)
    if isinstance(parsed, JSONResponse):
        return parsed
    try:
        result = await conversation_service.toggle_bookmarked_message(
            parsed.conversation_id, message_id
        )
    except ValueError as err:
        return JSONResponse(status_code=400, content={"error": str(err)})
    return JSONResponse(content=result)
