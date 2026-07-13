"""Shared auth helpers for routers — ownership verification."""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select

from app.db.engine import get_db
from app.db.models import Agent, Artifact, Attachment, Conversation, Document


async def verify_conversation_ownership(conversation_id: str, user_id: str) -> None:
    """Raise 404 if conversation doesn't exist, 403 if it belongs to another user."""
    async with get_db() as db:
        result = await db.execute(
            select(Conversation.user_id).where(Conversation.id == conversation_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Conversation not found: {conversation_id}",
            )
        if row != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied to this conversation",
            )


async def verify_artifact_ownership(artifact_id: str, user_id: str) -> str:
    """Raise 404 if artifact doesn't exist, 403 if its conversation belongs to another user.

    Returns the conversation_id on success.
    """
    async with get_db() as db:
        result = await db.execute(
            select(Artifact.conversation_id, Conversation.user_id)
            .join(Conversation, Artifact.conversation_id == Conversation.id)
            .where(Artifact.id == artifact_id)
        )
        row = result.one_or_none()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Artifact not found: {artifact_id}",
            )
        conv_id, conv_user_id = row
        if conv_user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied to this artifact",
            )
        return conv_id


async def verify_attachment_ownership(attachment_id: str, user_id: str) -> str:
    """Raise 404 if attachment doesn't exist, 403 if its conversation belongs to another user.

    Returns the conversation_id on success.
    """
    async with get_db() as db:
        result = await db.execute(
            select(Attachment.conversation_id, Conversation.user_id)
            .join(Conversation, Attachment.conversation_id == Conversation.id)
            .where(Attachment.id == attachment_id)
        )
        row = result.one_or_none()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Attachment not found: {attachment_id}",
            )
        conv_id, conv_user_id = row
        if conv_user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied to this attachment",
            )
        return conv_id


async def verify_agent_ownership(agent_id: str, user_id: str, allow_builtin: bool = True) -> None:
    """Raise 404 if agent doesn't exist, 403 if it belongs to another user.

    Builtin agents (user_id IS NULL) are accessible to all users when
    ``allow_builtin`` is True.
    """
    async with get_db() as db:
        result = await db.execute(
            select(Agent.user_id, Agent.is_builtin).where(Agent.id == agent_id)
        )
        row = result.one_or_none()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Agent not found: {agent_id}",
            )
        agent_user_id, is_builtin = row
        if agent_user_id is None and is_builtin and allow_builtin:
            return
        if agent_user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied to this agent",
            )


async def verify_document_ownership(document_id: str, user_id: str) -> None:
    """Raise 404 if document doesn't exist, 403 if it belongs to another user."""
    async with get_db() as db:
        result = await db.execute(
            select(Document.user_id).where(Document.id == document_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document not found: {document_id}",
            )
        if row != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied to this document",
            )
