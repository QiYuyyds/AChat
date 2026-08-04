"""Shared auth helpers for routers — ownership verification.

Local tables (conversations, artifacts, attachments, agents) are single-user
in dual-DB mode: ownership checks are existence-only (404 if not found, no 403).
Remote tables (documents) retain full user_id comparison.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select

from app.db.engine import get_local_db, get_remote_db
from app.db.models import Agent, Artifact, Attachment, Conversation, Document


async def verify_conversation_ownership(conversation_id: str, user_id: str) -> None:
    """Raise 404 if conversation doesn't exist. user_id is ignored (single-user mode)."""
    async with get_local_db() as db:
        result = await db.execute(
            select(Conversation.id).where(Conversation.id == conversation_id)
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Conversation not found: {conversation_id}",
            )


async def verify_artifact_ownership(artifact_id: str, user_id: str) -> str:
    """Raise 404 if artifact doesn't exist. user_id is ignored (single-user mode).

    Returns the conversation_id on success.
    """
    async with get_local_db() as db:
        result = await db.execute(
            select(Artifact.conversation_id).where(Artifact.id == artifact_id)
        )
        conv_id = result.scalar_one_or_none()
        if conv_id is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Artifact not found: {artifact_id}",
            )
        return conv_id


async def verify_attachment_ownership(attachment_id: str, user_id: str) -> str:
    """Raise 404 if attachment doesn't exist. user_id is ignored (single-user mode).

    Returns the conversation_id on success.
    """
    async with get_local_db() as db:
        result = await db.execute(
            select(Attachment.conversation_id).where(Attachment.id == attachment_id)
        )
        conv_id = result.scalar_one_or_none()
        if conv_id is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Attachment not found: {attachment_id}",
            )
        return conv_id


async def verify_agent_ownership(agent_id: str, user_id: str, allow_builtin: bool = True) -> None:
    """Raise 404 if agent doesn't exist. user_id is ignored (single-user mode)."""
    async with get_local_db() as db:
        result = await db.execute(
            select(Agent.id).where(Agent.id == agent_id)
        )
        if result.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Agent not found: {agent_id}",
            )


async def verify_document_ownership(document_id: str, user_id: str) -> None:
    """Raise 404 if document doesn't exist, 403 if it belongs to another user."""
    async with get_remote_db() as db:
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
