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

from fastapi import APIRouter, UploadFile
from fastapi.responses import JSONResponse

from app.schemas import (
    DeleteDocumentResponse,
    DocumentDetailResponse,
    DocumentListItem,
    DocumentListResponse,
    DocumentResponse,
    IngestResultResponse,
    IngestVersionRequest,
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
async def list_documents() -> DocumentListResponse:
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
async def write_document(req: WriteDocumentRequest) -> WriteDocumentResponse:
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
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)  # type: ignore

    return WriteDocumentResponse(
        document=_doc_response(result["document"]),
        version=_ver_response(result["version"]),
        created=result["created"],
        ingest=result.get("ingest"),
    )


# ─── Get document ─────────────────────────────────────────────────────────


@router.get("/documents/{document_id}")
async def get_document(document_id: str) -> DocumentDetailResponse:
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
async def list_versions(document_id: str) -> VersionListResponse:
    """List all versions of a document."""
    svc = _get_service()
    versions = await svc.list_versions(document_id)
    return VersionListResponse(versions=[_ver_response(v) for v in versions])


# ─── Get specific version ─────────────────────────────────────────────────


@router.get("/documents/{document_id}/versions/{version_id}")
async def get_version(document_id: str, version_id: str) -> VersionResponse:
    """Get a specific version by ID."""
    svc = _get_service()
    ver = await svc.get_version(version_id)
    if ver is None:
        return JSONResponse({"error": "Version not found"}, status_code=404)  # type: ignore
    return _ver_response(ver)


# ─── Delete ───────────────────────────────────────────────────────────────


@router.delete("/documents/{document_id}")
async def delete_document(document_id: str) -> DeleteDocumentResponse:
    """Soft-delete document + clean up RAG chunks."""
    svc = _get_service()
    try:
        deleted = await svc.delete_document(document_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)  # type: ignore
    return DeleteDocumentResponse(ok=True, deleted_chunks=deleted)


# ─── Ingest version to RAG ────────────────────────────────────────────────


@router.post("/documents/{document_id}/ingest")
async def ingest_document(
    document_id: str, req: IngestVersionRequest
) -> IngestResultResponse:
    """Ingest a specific version into RAG."""
    svc = _get_service()
    try:
        result = await svc.ingest_version(document_id, req.version_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)  # type: ignore
    return IngestResultResponse(
        version_id=result["version_id"] if "version_id" in result else req.version_id,
        chunk_count=result["chunk_count"],
        doc_hash=result["doc_hash"],
    )


# ─── Upload file (one-stop) ───────────────────────────────────────────────


@router.post("/documents/upload")
async def upload_document(file: UploadFile | None = None) -> UploadDocumentResponse:
    """Upload file → parse → create document → ingest to RAG (one-stop)."""
    if file is None:
        return JSONResponse({"error": "Missing file"}, status_code=400)  # type: ignore

    data = await file.read()
    svc = _get_service()
    try:
        result = await svc.upload_file(
            filename=file.filename or "file",
            content_type=file.content_type or "",
            data=data,
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
