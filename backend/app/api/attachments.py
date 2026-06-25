"""Attachments API routes.

Ports:
- src/app/api/attachments/[id]/route.ts                (GET serve file, DELETE)
- src/app/api/conversations/[id]/attachments/route.ts  (GET list, POST upload)
"""

from urllib.parse import quote

from fastapi import APIRouter, UploadFile
from fastapi.responses import JSONResponse, Response

from app.schemas import (
    AttachmentListResponse,
    AttachmentResponse,
    UploadAttachmentResponse,
)
from app.services import attachment_service

router = APIRouter()


def _to_response(row) -> AttachmentResponse:
    return AttachmentResponse(
        id=row.id,
        conversation_id=row.conversation_id,
        kind=row.kind,
        file_name=row.file_name,
        file_path=row.file_path,
        size=row.size,
        mime_type=row.mime_type,
        created_at=row.created_at,
    )


@router.get("/attachments/{attachment_id}")
async def serve_attachment(attachment_id: str) -> Response:
    """Serve the raw attachment bytes (inline for images, else download)."""
    row = await attachment_service.get_attachment(attachment_id)
    if row is None:
        return JSONResponse({"error": "Not found"}, status_code=404)

    abs_path = await attachment_service.get_attachment_absolute_path(attachment_id)
    if not abs_path:
        return JSONResponse({"error": "File missing on disk"}, status_code=410)

    with open(abs_path, "rb") as f:
        buf = f.read()

    encoded_name = quote(row.file_name)
    prefix = "inline" if row.kind == "image" else "attachment"
    disposition = f"{prefix}; filename*=UTF-8''{encoded_name}"

    return Response(
        content=buf,
        media_type=row.mime_type,
        headers={
            "Content-Length": str(len(buf)),
            "Content-Disposition": disposition,
            "Cache-Control": "private, max-age=3600",
        },
    )


@router.delete("/attachments/{attachment_id}")
async def delete_attachment(attachment_id: str) -> JSONResponse:
    """Remove an attachment from the file library."""
    try:
        await attachment_service.delete_attachment(attachment_id)
    except ValueError as err:
        return JSONResponse({"error": str(err)}, status_code=404)
    return JSONResponse({"ok": True})


@router.get("/conversations/{conversation_id}/attachments")
async def list_attachments(conversation_id: str) -> AttachmentListResponse:
    """List a conversation's attachments (newest first)."""
    rows = await attachment_service.list_attachments(conversation_id)
    return AttachmentListResponse(attachments=[_to_response(r) for r in rows])


@router.post("/conversations/{conversation_id}/attachments", status_code=201)
async def upload_attachment(
    conversation_id: str, file: UploadFile | None = None
) -> Response:
    """Upload a file (multipart/form-data, field name ``file``)."""
    if file is None:
        return JSONResponse({"error": "Missing file"}, status_code=400)

    data = await file.read()
    try:
        row = await attachment_service.upload_attachment(
            conversation_id=conversation_id,
            file_name=file.filename or "file",
            data=data,
            content_type=file.content_type,
        )
    except ValueError as err:
        return JSONResponse({"error": str(err)}, status_code=400)

    payload = UploadAttachmentResponse(attachment=_to_response(row))
    return JSONResponse(payload.model_dump(by_alias=True), status_code=201)
