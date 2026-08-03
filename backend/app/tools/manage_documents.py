"""manage_documents tool — list / upload / delete / refresh knowledge-base documents.

Reuses DocumentService for all operations. All operations are scoped by
ToolContext.user_id.
"""

from __future__ import annotations

from typing import Any

from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok
from app.tools.manage_base import emit_guide_side_effect

# ── Handler + sub-handlers ───────────────────────────────────────────────────


async def _manage_documents_handler(args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    action = args.get("action", "")
    user_id = ctx.user_id
    if user_id is None:
        return err("manage_documents requires a user context")

    if action == "list":
        return await _list_documents(user_id)
    elif action == "upload":
        return await _upload_document(args, user_id, ctx)
    elif action == "delete":
        return await _delete_document(args, user_id, ctx)
    elif action == "refresh":
        return await _refresh_document(args, user_id, ctx)
    else:
        return err(f"Unknown action: {action}")


async def _list_documents(user_id: str) -> ToolResult:
    from app.services.document_service import DocumentService

    ds = DocumentService(db=None, rag=None)
    docs = await ds.list_flat(user_id)
    return ok({"documents": docs})


async def _upload_document(args: dict[str, Any], user_id: str, ctx: ToolContext) -> ToolResult:
    from app.services.document_service import DocumentService

    title = args.get("title", "").strip()
    content = args.get("content", "")

    if not title:
        return err("title is required for upload action")
    if not content:
        return err("content is required for upload action")

    ds = DocumentService(db=None, rag=None)
    try:
        doc = await ds.upload_file(
            filename=title,
            content_type="text/markdown",
            file_content=content.encode("utf-8"),
            user_id=user_id,
            title=title,
        )
    except Exception as e:
        return err(f"Failed to upload document: {e}")

    emit_guide_side_effect(ctx=ctx, target="documents", action="create")
    return ok({"document": doc, "message": f"已上传文档「{title}」"})


async def _delete_document(args: dict[str, Any], user_id: str, ctx: ToolContext) -> ToolResult:
    if not args.get("confirm", False):
        return err("删除操作需要先通过 ask_user 向用户确认，并传 confirm=true")

    document_id = args.get("document_id")
    if not document_id:
        return err("document_id is required for delete action")

    from app.db.engine import get_remote_db
    from app.db.models import Document
    from app.services.document_service import DocumentService

    async with get_remote_db() as db:
        doc = await db.get(Document, document_id)
        if doc is None or doc.user_id != user_id:
            return err(f"Document not found: {document_id}")
        doc_title = doc.title

    ds = DocumentService(db=None, rag=None)
    try:
        await ds.delete_document(document_id)
    except Exception as e:
        return err(f"Failed to delete document: {e}")

    emit_guide_side_effect(ctx=ctx, target="documents", action="delete")
    return ok({"message": f"已删除文档「{doc_title}」"})


async def _refresh_document(args: dict[str, Any], user_id: str, ctx: ToolContext) -> ToolResult:
    document_id = args.get("document_id")
    if not document_id:
        return err("document_id is required for refresh action")

    from app.db.engine import get_remote_db
    from app.db.models import Document

    async with get_remote_db() as db:
        doc = await db.get(Document, document_id)
        if doc is None or doc.user_id != user_id:
            return err(f"Document not found: {document_id}")

    emit_guide_side_effect(ctx=ctx, target="documents", action="refresh")
    return ok({"message": f"已触发文档「{doc.title}」的版本刷新"})


# ── Tool definition ──────────────────────────────────────────────────────────

manage_documents_tool = ToolDef(
    name="manage_documents",
    description=(
        "管理知识库文档：列表 / 上传 / 删除 / 刷新版本。"
        "action: list | upload | delete | refresh。"
        "delete 操作需要先通过 ask_user 向用户确认，并传 confirm=true。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "upload", "delete", "refresh"],
            },
            "search": {"type": "string"},
            "file_path": {"type": "string"},
            "title": {"type": "string"},
            "content": {"type": "string"},
            "document_id": {"type": "string"},
            "confirm": {"type": "boolean", "default": False},
        },
        "required": ["action"],
    },
    handler=_manage_documents_handler,  # type: ignore[assignment]
)
