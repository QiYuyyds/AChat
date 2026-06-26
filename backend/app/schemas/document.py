"""Pydantic schemas for Document + Version API."""

from typing import Any

from pydantic import BaseModel, Field


# ─── Request models ────────────────────────────────────────────────────────


class WriteDocumentRequest(BaseModel):
    """Request to create or update a document (update = new version)."""

    document_id: str = Field(default="", alias="documentId")
    title: str
    doc_type: str = Field(default="note", alias="docType")
    source: str = Field(default="agent_generated", alias="source")
    created_by: str = Field(default="agent", alias="createdBy")
    content_md: str = Field(alias="contentMd")
    summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    ingest_to_rag: bool = Field(default=False, alias="ingestToRag")

    model_config = {"populate_by_name": True}


class IngestVersionRequest(BaseModel):
    """Request to ingest a specific version into RAG."""

    version_id: str = Field(alias="versionId")

    model_config = {"populate_by_name": True}


# ─── Response models ───────────────────────────────────────────────────────


class DocumentResponse(BaseModel):
    """Document metadata in API responses."""

    id: str
    title: str
    doc_type: str = Field(alias="docType")
    source: str
    status: str
    created_by: str = Field(alias="createdBy")
    created_at: float = Field(alias="createdAt")
    updated_at: float = Field(alias="updatedAt")
    latest_version: int = Field(alias="latestVersion")
    latest_version_id: str = Field(alias="latestVersionId")

    model_config = {"populate_by_name": True}


class VersionResponse(BaseModel):
    """Document version in API responses."""

    id: str
    document_id: str = Field(alias="documentId")
    version: int
    content_md: str = Field(alias="contentMd")
    summary: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(alias="createdAt")

    model_config = {"populate_by_name": True}


class LatestVersionMeta(BaseModel):
    """Metadata of the latest version (joined into list responses)."""

    filename: str | None = None
    parser: str | None = None
    pages: int | None = None
    text_chars: int | None = Field(default=None, alias="textChars")
    needs_ocr: bool | None = Field(default=None, alias="needsOcr")

    model_config = {"populate_by_name": True}


class DocumentListItem(DocumentResponse):
    """Document row in list responses, enriched with latest-version info."""

    latest_metadata: dict[str, Any] | None = Field(default=None, alias="latestMetadata")
    latest_content_chars: int | None = Field(default=None, alias="latestContentChars")
    latest_parser: str | None = Field(default=None, alias="latestParser")

    model_config = {"populate_by_name": True}


class DocumentListResponse(BaseModel):
    """Response for GET /api/documents."""

    documents: list[DocumentListItem]

    model_config = {"populate_by_name": True}


class DocumentDetailResponse(BaseModel):
    """Response for GET /api/documents/{id}."""

    document: DocumentResponse
    version: VersionResponse

    model_config = {"populate_by_name": True}


class VersionListResponse(BaseModel):
    """Response for GET /api/documents/{id}/versions."""

    versions: list[VersionResponse]

    model_config = {"populate_by_name": True}


class IngestResultResponse(BaseModel):
    """Response for POST /api/documents/{id}/ingest."""

    version_id: str = Field(alias="versionId")
    chunk_count: int = Field(alias="chunkCount")
    doc_hash: str = Field(alias="docHash")

    model_config = {"populate_by_name": True}


class WriteDocumentResponse(BaseModel):
    """Response for POST /api/documents."""

    document: DocumentResponse
    version: VersionResponse
    created: bool
    ingest: dict[str, Any] | None = None

    model_config = {"populate_by_name": True}


class DeleteDocumentResponse(BaseModel):
    """Response for DELETE /api/documents/{id}."""

    ok: bool
    deleted_chunks: int = Field(alias="deletedChunks")

    model_config = {"populate_by_name": True}


class UploadDocumentResponse(BaseModel):
    """Response for POST /api/documents/upload."""

    filename: str
    content_type: str | None = Field(default=None, alias="contentType")
    parser: str | None = None
    pages: int | None = None
    text_chars: int | None = Field(default=None, alias="textChars")
    needs_ocr: bool | None = Field(default=None, alias="needsOcr")
    chunk_count: int | None = Field(default=None, alias="chunkCount")
    doc_hash: str | None = Field(default=None, alias="docHash")
    document: DocumentResponse | None = None
    version: VersionResponse | None = None
    success: bool
    message: str | None = None

    model_config = {"populate_by_name": True}
