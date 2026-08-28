"""Documents API routes — 8 endpoints for document lifecycle management.

Routes:
  GET    /documents                  — list all active documents
  POST   /documents                  — create or update document (optional ingest)
  GET    /documents/{id}             — get document + latest version
  GET    /documents/{id}/versions    — list all versions
  GET    /documents/{id}/versions/{ver_id} — get specific version
  DELETE /documents/{id}             — soft-delete + clean RAG chunks
  POST   /documents/{id}/ingest      — ingest a version to RAG
  POST   /documents/upload           — upload file → parse → create → ingest
"""

from fastapi import APIRouter, Depends, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from app.auth.dependencies import get_current_user
from app.auth.ownership import verify_document_ownership
from app.config import get_settings
from app.db.models import User
from app.schemas import (
    CreateFolderRequest,
    DeleteDocumentResponse,
    DocumentDetailResponse,
    DocumentFlatListResponse,
    DocumentListItem,
    DocumentListResponse,
    DocumentResponse,
    DocumentTreeResponse,
    FileNode,
    FolderNode,
    IngestResultResponse,
    IngestVersionRequest,
    MoveDocumentRequest,
    PreviewResponse,
    UploadDocumentResponse,
    VersionListResponse,
    VersionResponse,
    WriteDocumentRequest,
    WriteDocumentResponse,
)

router = APIRouter()


def _get_service():
    """Lazy import to avoid circular dependency; returns the global DocumentService."""
    from app.main import _document_service  # type: ignore[attr-defined]
    if _document_service is None:
        raise RuntimeError("DocumentService not initialized")
    return _document_service


def _doc_response(d: dict) -> DocumentResponse:
    return DocumentResponse(
        id=d["id"],
        title=d["title"],
        doc_type=d["doc_type"],
        source=d["source"],
        status=d["status"],
        created_by=d["created_by"],
        created_at=d["created_at"],
        updated_at=d["updated_at"],
        latest_version=d["latest_version"],
        latest_version_id=d["latest_version_id"],
        source_path=d.get("source_path", ""),
        content_hash=d.get("content_hash"),
        chunk_preset=d.get("chunk_preset", "general"),
        parent_id=d.get("parent_id"),
        is_folder=d.get("is_folder", False),
    )


def _ver_response(v: dict) -> VersionResponse:
    return VersionResponse(
        id=v["id"],
        document_id=v["document_id"],
        version=v["version"],
        content_md=v["content_md"],
        summary=v.get("summary"),
        metadata=v.get("metadata", {}),
        created_at=v["created_at"],
    )


# ─── List ──────────────────────────────────────────────────────────────────


@router.get("/documents")
async def list_documents(user: User = Depends(get_current_user)) -> DocumentListResponse:
    """List all active documents with latest-version metadata."""
    svc = _get_service()
    items = await svc.list_documents()
    docs = []
    for item in items:
        docs.append(DocumentListItem(
            id=item["id"],
            title=item["title"],
            doc_type=item["doc_type"],
            source=item["source"],
            status=item["status"],
            created_by=item["created_by"],
            created_at=item["created_at"],
            updated_at=item["updated_at"],
            latest_version=item["latest_version"],
            latest_version_id=item["latest_version_id"],
            latest_metadata=item.get("latest_metadata"),
            latest_content_chars=item.get("latest_content_chars"),
            latest_parser=item.get("latest_parser"),
        ))
    return DocumentListResponse(documents=docs)


# ─── Create / Update ──────────────────────────────────────────────────────


@router.post("/documents")
async def write_document(req: WriteDocumentRequest, user: User = Depends(get_current_user)) -> WriteDocumentResponse:
    """Create a new document or update an existing one (creates a new version)."""
    svc = _get_service()
    try:
        result = await svc.write_document(
            document_id=req.document_id,
            title=req.title,
            doc_type=req.doc_type,
            source=req.source,
            created_by=req.created_by,
            content_md=req.content_md,
            summary=req.summary,
            metadata=req.metadata,
            ingest_to_rag=req.ingest_to_rag,
            user_id=user.id,
            preset_id=req.preset_id,
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)  # type: ignore

    return WriteDocumentResponse(
        document=_doc_response(result["document"]),
        version=_ver_response(result["version"]),
        created=result["created"],
        already_imported=result.get("already_imported", False),
        ingest=result.get("ingest"),
    )


# ─── Get document ─────────────────────────────────────────────────────────


@router.get("/documents/tree")
async def list_document_tree(
    path: str = "",
    parent_id: str | None = None,
    user: User = Depends(get_current_user),
) -> DocumentTreeResponse:
    """Virtual directory tree for all document sources."""
    svc = _get_service()
    result = await svc.list_tree(path, user.id, parent_id=parent_id)
    return DocumentTreeResponse(
        current_path=result["current_path"],
        folders=[
            FolderNode(
                name=f["name"],
                path=f["path"],
                doc_count=f["doc_count"],
            )
            for f in result["folders"]
        ],
        files=[
            FileNode(
                id=f["id"],
                title=f["title"],
                source_path=f["source_path"],
                doc_type=f["doc_type"],
                source=f["source"],
                updated_at=f["updated_at"],
                is_folder=f.get("is_folder", False),
                parent_id=f.get("parent_id"),
            )
            for f in result["files"]
        ],
    )


@router.get("/documents/flat")
async def list_documents_flat(
    sources: str | None = None,
    user: User = Depends(get_current_user),
) -> DocumentFlatListResponse:
    """Flat document list for non-obsidian sources."""
    svc = _get_service()
    source_list = sources.split(",") if sources else None
    items = await svc.list_flat(user.id, source_list)
    docs = []
    for item in items:
        docs.append(DocumentListItem(
            id=item["id"],
            title=item["title"],
            doc_type=item["doc_type"],
            source=item["source"],
            status=item["status"],
            created_by=item["created_by"],
            created_at=item["created_at"],
            updated_at=item["updated_at"],
            latest_version=item["latest_version"],
            latest_version_id=item["latest_version_id"],
            source_path=item.get("source_path", ""),
            content_hash=item.get("content_hash"),
            latest_metadata=item.get("latest_metadata"),
            latest_content_chars=item.get("latest_content_chars"),
            latest_parser=item.get("latest_parser"),
        ))
    return DocumentFlatListResponse(documents=docs)


@router.get("/documents/{document_id}")
async def get_document(document_id: str, user: User = Depends(get_current_user)) -> DocumentDetailResponse:
    await verify_document_ownership(document_id, user.id)
    """Get document + latest version."""
    svc = _get_service()
    result = await svc.get_document(document_id)
    if result is None:
        return JSONResponse({"error": "Document not found"}, status_code=404)  # type: ignore
    return DocumentDetailResponse(
        document=_doc_response(result["document"]),
        version=_ver_response(result["version"]),
    )


# ─── List versions ────────────────────────────────────────────────────────


@router.get("/documents/{document_id}/versions")
async def list_versions(document_id: str, user: User = Depends(get_current_user)) -> VersionListResponse:
    await verify_document_ownership(document_id, user.id)
    """List all versions of a document."""
    svc = _get_service()
    versions = await svc.list_versions(document_id)
    return VersionListResponse(versions=[_ver_response(v) for v in versions])


# ─── Get specific version ─────────────────────────────────────────────────


@router.get("/documents/{document_id}/versions/{version_id}")
async def get_version(document_id: str, version_id: str, user: User = Depends(get_current_user)) -> VersionResponse:
    await verify_document_ownership(document_id, user.id)
    """Get a specific version by ID."""
    svc = _get_service()
    ver = await svc.get_version(version_id)
    if ver is None:
        return JSONResponse({"error": "Version not found"}, status_code=404)  # type: ignore
    return _ver_response(ver)


# ─── Delete ───────────────────────────────────────────────────────────────


@router.delete("/documents/{document_id}")
async def delete_document(document_id: str, user: User = Depends(get_current_user)) -> DeleteDocumentResponse:
    await verify_document_ownership(document_id, user.id)
    """Soft-delete document + clean up RAG chunks."""
    svc = _get_service()
    try:
        deleted = await svc.delete_document(document_id, user_id=user.id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)  # type: ignore
    return DeleteDocumentResponse(ok=True, deleted_chunks=deleted)


# ─── Ingest version to RAG ────────────────────────────────────────────────


@router.post("/documents/{document_id}/ingest")
async def ingest_document(
    document_id: str,
    req: IngestVersionRequest,
    user: User = Depends(get_current_user),
) -> IngestResultResponse:
    """Ingest a specific version into RAG."""
    svc = _get_service()
    try:
        result = await svc.ingest_version(
        document_id, req.version_id, user_id=user.id, preset_id=req.preset_id
    )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)  # type: ignore
    return IngestResultResponse(
        version_id=result.get("version_id", req.version_id),
        chunk_count=result["chunk_count"],
        doc_hash=result["doc_hash"],
    )


# ─── Upload file (one-stop) ───────────────────────────────────────────────


@router.post("/documents/upload")
async def upload_document(
    user: User = Depends(get_current_user),
    file: UploadFile | None = None,
    document_id: str = Form(default=""),
    title: str | None = Form(default=None),
    doc_type: str | None = Form(default=None),
    preset_id: str = Form(default=""),
) -> UploadDocumentResponse:
    """Upload file → parse → create document → ingest to RAG (one-stop).

    Optional form fields:
    - document_id: if provided, creates a new version for an existing document
    - title: overrides the default title derived from filename
    - doc_type: overrides the default document type
    """
    if file is None:
        return JSONResponse({"error": "Missing file"}, status_code=400)  # type: ignore

    data = await file.read()
    svc = _get_service()
    try:
        result = await svc.upload_file(
            filename=file.filename or "file",
            content_type=file.content_type or "",
            data=data,
            document_id=document_id,
            title=title,
            doc_type=doc_type or "upload",
            user_id=user.id,
            preset_id=preset_id,
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)  # type: ignore

    return UploadDocumentResponse(
        filename=result["filename"],
        content_type=result.get("content_type"),
        parser=result.get("parser"),
        pages=result.get("pages"),
        text_chars=result.get("text_chars"),
        needs_ocr=result.get("needs_ocr"),
        chunk_count=result.get("chunk_count"),
        doc_hash=result.get("doc_hash"),
        document=_doc_response(result["document"]) if result.get("document") else None,
        version=_ver_response(result["version"]) if result.get("version") else None,
        success=result["success"],
        message=result.get("message"),
    )


# ─── File preview ─────────────────────────────────────────────────────────


@router.get("/documents/{document_id}/preview")
async def preview_document(
    document_id: str, user: User = Depends(get_current_user)
) -> PreviewResponse:
    """Return parsed Markdown + image metadata for document preview."""
    await verify_document_ownership(document_id, user.id)
    svc = _get_service()
    result = await svc.get_document(document_id)
    if result is None:
        return JSONResponse({"error": "Document not found"}, status_code=404)  # type: ignore
    ver = result["version"]
    meta = ver.get("metadata", {})
    return PreviewResponse(
        document_id=result["document"]["id"],
        version_id=ver["id"],
        content_md=ver["content_md"],
        images=meta.get("images", []),
        parser=meta.get("parser"),
        pages=meta.get("pages"),
    )


# ─── Image file serving ─────────────────────────────────────────────────



_CONTENT_TYPE_MAP = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


@router.get("/documents/{document_id}/images/{filename:path}")
async def serve_document_image(
    document_id: str,
    filename: str,
    user: User = Depends(get_current_user),
):
    """Serve a persisted document image file."""
    await verify_document_ownership(document_id, user.id)

    # Path safety: reject path traversal
    if ".." in filename or filename.startswith("/") or filename.startswith("\\"):
        return JSONResponse(
            {"error": "Invalid filename"}, status_code=404,
        )

    # Resolve image path
    settings = get_settings()
    image_dir = settings.data_path / "documents" / document_id / "images"
    image_path = (image_dir / filename).resolve()

    # Ensure resolved path is within the image directory
    try:
        image_path.relative_to(image_dir.resolve())
    except ValueError:
        return JSONResponse(
            {"error": "Invalid file path"}, status_code=404,
        )

    if not image_path.exists() or not image_path.is_file():
        return JSONResponse(
            {"error": f"Image not found: {filename}"}, status_code=404,
        )

    # Determine content type from extension
    ext = image_path.suffix.lower()
    media_type = _CONTENT_TYPE_MAP.get(ext, "application/octet-stream")

    return FileResponse(
        path=str(image_path),
        media_type=media_type,
        filename=filename,
    )


# ─── Folder management ────────────────────────────────────────────────────


@router.post("/documents/folder")
async def create_folder(
    req: CreateFolderRequest, user: User = Depends(get_current_user)
) -> DocumentResponse:
    """Create a virtual folder in the document tree."""
    svc = _get_service()
    folder = await svc.create_folder(user_id=user.id, parent_id=req.parent_id, name=req.name)
    return _doc_response(folder)


@router.patch("/documents/{document_id}/move")
async def move_document(
    document_id: str,
    req: MoveDocumentRequest,
    user: User = Depends(get_current_user),
) -> DocumentResponse:
    """Move a document/folder to a new parent."""
    await verify_document_ownership(document_id, user.id)
    svc = _get_service()
    try:
        doc = await svc.move_document(document_id, req.target_parent_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)  # type: ignore
    return _doc_response(doc)
