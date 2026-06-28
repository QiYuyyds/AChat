"""read_attachment tool — read a user-uploaded conversation attachment.

Port of src/server/tools/read-attachment.ts (don't confuse with read_artifact):
  - ids starting ``att_`` are attachments → read_attachment
  - ids starting ``art_`` are artifacts → read_artifact

Text-like attachments are returned inline; PDFs are lazily parsed to text; image
attachments point to the multimodal channel; other binaries return metadata only.

The TS version used the ``pdf-parse`` package; here PDF text extraction uses
``pypdf`` (an optional dependency). If it is not installed the tool degrades to a
metadata-only note rather than failing.
"""

from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from app.db.engine import get_db
from app.db.models import Attachment
from app.services.attachment_service import get_attachment_absolute_path
from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok

MAX_TEXT_CHARS = 50_000
_TEXT_MIME_PREFIXES = ("text/",)
_TEXT_MIME_FULL = {
    "application/json",
    "application/xml",
    "application/javascript",
    "application/x-yaml",
}


class _Args(BaseModel):
    attachment_id: str = Field(alias="attachmentId", min_length=1)

    model_config = {"populate_by_name": True}


_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["attachmentId"],
    "properties": {
        "attachmentId": {
            "type": "string",
            "description": "Attachment id, format att_xxx (NOT an artifact id)",
        },
    },
}


def _is_text_like(mime: str) -> bool:
    if mime in _TEXT_MIME_FULL:
        return True
    return any(mime.startswith(p) for p in _TEXT_MIME_PREFIXES)


def _is_pdf_like(mime_type: str, file_name: str, abs_path: str) -> bool:
    if mime_type == "application/pdf":
        return True
    if os.path.splitext(file_name)[1].lower() == ".pdf":
        return True
    try:
        with open(abs_path, "rb") as f:
            return f.read(5) == b"%PDF-"
    except OSError:
        return False


def _truncate_text(raw: str) -> tuple[str, bool]:
    truncated = len(raw) > MAX_TEXT_CHARS
    content = (
        raw[:MAX_TEXT_CHARS] + f"\n\n[TRUNCATED at {MAX_TEXT_CHARS} chars]"
        if truncated
        else raw
    )
    return content, truncated


def _extract_pdf_text(abs_path: str) -> dict[str, Any]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - depends on optional dep
        raise RuntimeError("pypdf is not installed; cannot extract PDF text") from exc

    reader = PdfReader(abs_path)
    text = "\n".join((page.extract_text() or "") for page in reader.pages).strip()
    content, truncated = _truncate_text(text)
    result: dict[str, Any] = {
        "content": content,
        "truncated": truncated,
        "pageCount": len(reader.pages),
    }
    if not text:
        result["note"] = (
            "No extractable text was found in this PDF. It may be scanned or "
            "image-only; OCR is required to inspect its content."
        )
    return result


async def _handler(args: Any, ctx: ToolContext) -> ToolResult:
    try:
        parsed = _Args.model_validate(args)
    except ValidationError as e:
        return err(f"Invalid args: {e}")

    attachment_id = parsed.attachment_id
    if attachment_id.startswith("art_"):
        return err(
            f"'{attachment_id}' is an artifact id (art_*), not an attachment. Use "
            "read_artifact instead."
        )

    async with get_db() as db:
        result = await db.execute(
            select(Attachment).where(
                Attachment.id == attachment_id,
                Attachment.conversation_id == ctx.conversation_id,
            )
        )
        row = result.scalar_one_or_none()
    if row is None:
        return err(f"Attachment not found in this conversation: {attachment_id}")

    abs_path = await get_attachment_absolute_path(attachment_id)
    if not abs_path:
        return err("Attachment file missing on disk")

    meta = {
        "id": row.id,
        "fileName": row.file_name,
        "size": row.size,
        "mimeType": row.mime_type,
        "kind": row.kind,
    }

    if _is_pdf_like(row.mime_type, row.file_name, abs_path):
        if ctx.cancel_event.is_set():
            return err("PDF extraction aborted")
        try:
            extracted = _extract_pdf_text(abs_path)
        except Exception as e:  # noqa: BLE001 - surface extraction failure to the LLM
            return err(f"Failed to extract PDF text: {e}")
        if ctx.cancel_event.is_set():
            return err("PDF extraction aborted")
        return ok({**meta, **extracted})

    if _is_text_like(row.mime_type):
        try:
            with open(abs_path, encoding="utf-8") as f:
                raw = f.read()
        except OSError as e:
            return err(f"Failed to read text file: {e}")
        content, truncated = _truncate_text(raw)
        return ok({**meta, "content": content, "truncated": truncated})

    if row.kind == "image":
        return ok(
            {
                **meta,
                "note": (
                    "Image bytes are delivered through the multimodal user message (if "
                    "the agent supports vision). You should already see this image in "
                    "the conversation content blocks."
                ),
            }
        )

    return ok(
        {
            **meta,
            "note": (
                f"This is a {row.mime_type} binary file. AChat does not yet extract "
                "text from this format; only metadata is available. Ask the user for a "
                "text version if you need to inspect content."
            ),
        }
    )


read_attachment_tool = ToolDef(
    name="read_attachment",
    description=(
        "Read the contents of a user-uploaded attachment (id starts with 'att_'). Use "
        "this when the user prompt mentions [图片附件: ...] or [文件附件: ...]. Returns "
        "plain text for text-like files (txt/md/json/csv/etc) and extractable PDF "
        "text. For images and unsupported binary formats only metadata is returned. "
        "Do NOT use this for ids starting with 'art_' — that's for read_artifact."
    ),
    parameters=_PARAMETERS,
    handler=_handler,
)
