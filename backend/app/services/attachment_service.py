"""Conversation file library.

Port of src/server/attachment-service.ts.

Metadata lives in the ``attachments`` table; binary blobs live at
``workspace.root_path/uploads/{id}{ext}``. All paths stay inside the workspace
sandbox (path-traversal is rejected).
"""

from __future__ import annotations

import contextlib
import os
import re

from sqlalchemy import desc, select

from app.db.engine import get_db
from app.db.models import Attachment, Workspace
from app.utils.clock import now_ms
from app.utils.ids import new_attachment_id
from app.utils.workspace_utils import is_path_within

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB

_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".json": "application/json",
    ".pdf": "application/pdf",
    ".csv": "text/csv",
}


async def upload_attachment(
    conversation_id: str,
    file_name: str,
    data: bytes,
    content_type: str | None = None,
) -> Attachment:
    """Store an uploaded file and create its ``attachments`` row.

    Mirrors TS ``uploadAttachment``: rejects empty / oversized files, writes the
    blob under ``<rootPath>/uploads/{id}{ext}`` (sandbox-checked), and infers
    ``kind`` / ``mime_type``.
    """
    size = len(data)
    if size == 0:
        raise ValueError("Empty file")
    if size > MAX_FILE_SIZE:
        raise ValueError(f"File too large (max {MAX_FILE_SIZE // 1024 // 1024}MB)")

    async with get_db() as db:
        ws_result = await db.execute(
            select(Workspace).where(Workspace.conversation_id == conversation_id)
        )
        workspace = ws_result.scalar_one_or_none()
        if workspace is None:
            raise ValueError(f"Workspace not found for conversation: {conversation_id}")
        root_path = workspace.root_path

        upload_dir = os.path.join(root_path, "uploads")
        os.makedirs(upload_dir, exist_ok=True)

        attachment_id = new_attachment_id()
        ext = _sanitize_ext(file_name)
        stored_name = f"{attachment_id}{ext}"
        abs_path = os.path.join(upload_dir, stored_name)

        # Sandbox check: resolved path must still be inside the workspace.
        resolved = os.path.abspath(abs_path)
        if not is_path_within(resolved, root_path):
            raise ValueError("Path traversal detected")

        with open(abs_path, "wb") as f:
            f.write(data)

        mime_type = content_type or _guess_mime(ext)
        kind = "image" if mime_type.startswith("image/") else "file"

        row = Attachment(
            id=attachment_id,
            conversation_id=conversation_id,
            kind=kind,
            file_name=file_name,
            # Relative path, posix-style separators for cross-platform storage.
            file_path=f"uploads/{stored_name}",
            size=size,
            mime_type=mime_type,
            created_at=now_ms(),
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        db.expunge(row)
        return row


async def list_attachments(conversation_id: str) -> list[Attachment]:
    async with get_db() as db:
        result = await db.execute(
            select(Attachment)
            .where(Attachment.conversation_id == conversation_id)
            .order_by(desc(Attachment.created_at))
        )
        rows = list(result.scalars().all())
        for row in rows:
            db.expunge(row)
        return rows


async def delete_attachment(attachment_id: str) -> None:
    """Delete the attachments row and its backing file.

    Raises if the attachment does not exist. File removal failures are swallowed
    (the row is the source of truth), matching TS.
    """
    abs_path = await get_attachment_absolute_path(attachment_id)

    async with get_db() as db:
        result = await db.execute(
            select(Attachment).where(Attachment.id == attachment_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            raise ValueError(f"Attachment not found: {attachment_id}")
        await db.delete(row)

    if abs_path:
        with contextlib.suppress(OSError):
            os.remove(abs_path)


def _sanitize_ext(file_name: str) -> str:
    """Lowercased extension limited to ``.[a-z0-9]{1,8}``; else empty."""
    ext = os.path.splitext(file_name)[1].lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,8}", ext):
        return ""
    return ext


def _guess_mime(ext: str) -> str:
    return _MIME_BY_EXT.get(ext, "application/octet-stream")


async def get_attachment(attachment_id: str) -> Attachment | None:
    async with get_db() as db:
        result = await db.execute(
            select(Attachment).where(Attachment.id == attachment_id)
        )
        return result.scalar_one_or_none()


async def get_attachment_absolute_path(attachment_id: str) -> str | None:
    async with get_db() as db:
        result = await db.execute(
            select(Attachment).where(Attachment.id == attachment_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return None
        ws_result = await db.execute(
            select(Workspace).where(Workspace.conversation_id == row.conversation_id)
        )
        workspace = ws_result.scalar_one_or_none()
    if workspace is None:
        return None
    abs_path = os.path.join(workspace.root_path, row.file_path)
    resolved = os.path.realpath(abs_path)
    if not is_path_within(resolved, workspace.root_path):
        return None
    return resolved if os.path.exists(resolved) else None
