"""Cloud sync endpoints for desktop engine durable writes.

These endpoints UPSERT authoritative data without starting Agent runs.
Desktop online path: local engine executes; cloud PG remains the authority.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from app.auth.dependencies import get_current_user
from app.auth.ownership import verify_conversation_ownership
from app.db.engine import get_db
from app.db.models import Conversation, Message, User
from app.utils.clock import now_ms

router = APIRouter()


class SyncMessageItem(BaseModel):
    id: str = Field(min_length=1)
    conversation_id: str = Field(alias="conversationId", min_length=1)
    role: str
    agent_id: str | None = Field(default=None, alias="agentId")
    parts: list[dict[str, Any]] = Field(default_factory=list)
    status: str = "complete"
    parent_message_id: str | None = Field(default=None, alias="parentMessageId")
    mentioned_agent_ids: list[str] = Field(
        default_factory=list, alias="mentionedAgentIds"
    )
    run_id: str | None = Field(default=None, alias="runId")
    usage: dict[str, Any] | None = None
    hidden: bool = False
    created_at: int | None = Field(default=None, alias="createdAt")

    model_config = {"populate_by_name": True}


class SyncMessagesBody(BaseModel):
    messages: list[SyncMessageItem] = Field(min_length=1)

    model_config = {"populate_by_name": True}


@router.post("/sync/messages")
async def upsert_sync_messages(
    req: Request,
    user: User = Depends(get_current_user),
) -> JSONResponse:
    """UPSERT message rows for conversations owned by the caller. No Agent runs."""
    try:
        raw = await req.json()
    except Exception:
        raw = None
    try:
        body = SyncMessagesBody.model_validate(raw)
    except ValidationError as err:
        return JSONResponse(
            status_code=400,
            content={"error": "Invalid body", "issues": err.errors()},
        )

    for cid in {m.conversation_id for m in body.messages}:
        await verify_conversation_ownership(cid, user.id)

    upserted = 0
    async with get_db() as db:
        for item in body.messages:
            result = await db.execute(select(Message).where(Message.id == item.id))
            row = result.scalar_one_or_none()
            created = item.created_at if item.created_at is not None else now_ms()
            if row is None:
                msg = Message(
                    id=item.id,
                    conversation_id=item.conversation_id,
                    role=item.role,
                    agent_id=item.agent_id,
                    status=item.status,
                    parent_message_id=item.parent_message_id,
                    run_id=item.run_id,
                    usage=item.usage,
                    hidden=item.hidden,
                    created_at=created,
                )
                msg.parts_list = item.parts
                msg.mentioned_agent_ids_list = item.mentioned_agent_ids
                db.add(msg)
            else:
                # Complete/error/abort always wins over streaming; never drop
                # complete content for incomplete offline payloads.
                if item.status in ("complete", "error", "aborted") or row.status == "streaming":
                    row.parts_list = item.parts
                    row.status = item.status
                    row.usage = item.usage
                    row.agent_id = item.agent_id or row.agent_id
                    row.run_id = item.run_id or row.run_id
                    row.hidden = item.hidden
            conv = (
                await db.execute(
                    select(Conversation).where(Conversation.id == item.conversation_id)
                )
            ).scalar_one_or_none()
            if conv is not None:
                conv.updated_at = now_ms()
            upserted += 1

    return JSONResponse(content={"ok": True, "upserted": upserted})
