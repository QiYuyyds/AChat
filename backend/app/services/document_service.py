"""DocumentService — global knowledge-base document lifecycle management.

CRUD + version management + RAG bridging (ingest backfill, delete cleanup).
Documents are independent of conversations; all agents share the same knowledge base.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

from sqlalchemy import desc, func, select, update

from app.db.engine import get_remote_db
from app.db.models import Document, DocumentVersion, RagChunk
from app.rag.parser import parse_bytes
from app.utils.ids import new_document_id, new_document_version_id

logger = logging.getLogger(__name__)


def _now() -> float:
    """Current epoch time in seconds (float, matching AGI-memory pattern)."""
    return time.time()


def _doc_hash(content: str) -> str:
    """Compute the same doc_hash that RAGEngine.ingest() uses."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


class DocumentService:
    """Document library service: CRUD + version management + RAG bridging."""

    def __init__(self, db=None, rag=None):
        # db is the get_db context manager; rag is the RAGService instance
        self._get_db = db or get_remote_db
        self._rag = rag

    # ─── List ──────────────────────────────────────────────────────────────

    async def list_documents(self) -> list[dict]:
        """List all active documents with latest-version metadata."""
        async with self._get_db() as session:
            # Query active documents ordered by updated_at DESC
            result = await session.execute(
                select(Document)
                .where(Document.status != "deleted")
                .order_by(desc(Document.updated_at))
            )
            docs = result.scalars().all()
            if not docs:
                return []

            items: list[dict] = []
            for doc in docs:
                item = _doc_to_dict(doc)
                # Join latest version
                if doc.latest_version_id:
                    ver_result = await session.execute(
                        select(DocumentVersion).where(
                            DocumentVersion.id == doc.latest_version_id
                        )
                    )
                    ver = ver_result.scalar_one_or_none()
                    if ver:
                        meta = ver.meta or {}
                        item["latest_metadata"] = meta
                        item["latest_content_chars"] = len(ver.content_md or "")
                        item["latest_parser"] = meta.get("parser")
                items.append(item)
            return items

    # ─── Virtual directory tree (obsidian_sync only) ──────────────────────

    async def list_tree(self, path: str, user_id: str) -> dict:
        """Return virtual directory tree for obsidian_sync documents.

        Only documents with source='obsidian_sync' are included. Folders and
        files are derived from the source_path column.
        """
        async with self._get_db() as session:
            # Normalize path: strip leading/trailing slashes
            prefix = path.strip("/")

            result = await session.execute(
                select(Document).where(
                    Document.source == "obsidian_sync",
                    Document.status != "deleted",
                    Document.user_id == user_id if user_id else True,
                )
            )
            docs = result.scalars().all()

        # Build folder and file lists from source_path
        folders: dict[str, dict[str, Any]] = {}
        files: list[dict[str, Any]] = []

        for doc in docs:
            sp = doc.source_path
            if not sp:
                continue

            # Filter by requested path prefix
            if prefix:
                if not sp.startswith(prefix + "/"):
                    continue
                remaining = sp[len(prefix) + 1:]
            else:
                remaining = sp

            if not remaining:
                continue

            parts = remaining.split("/")
            if len(parts) == 1:
                # It's a file at the current level
                files.append({
                    "id": doc.id,
                    "title": doc.title,
                    "source_path": doc.source_path,
                    "doc_type": doc.doc_type,
                    "source": doc.source,
                    "updated_at": doc.updated_at,
                })
            else:
                # It's in a subdirectory — collect folder names at current level
                folder_name = parts[0]
                if folder_name not in folders:
                    folders[folder_name] = {
                        "name": folder_name,
                        "path": f"{prefix}/{folder_name}" if prefix else folder_name,
                        "doc_count": 0,
                    }
                folders[folder_name]["doc_count"] += 1

        return {
            "current_path": path,
            "folders": sorted(folders.values(), key=lambda f: f["name"]),
            "files": sorted(files, key=lambda f: f["title"]),
        }

    # ─── Flat list (non-obsidian sources) ──────────────────────────────────

    async def list_flat(self, user_id: str, sources: list[str] | None = None) -> list[dict]:
        """Return flat document list for specified sources.

        Default sources: user_upload, agent_generated, artifact_import.
        Does NOT include obsidian_sync documents.
        """
        if sources is None:
            sources = ["user_upload", "agent_generated", "artifact_import"]

        async with self._get_db() as session:
            result = await session.execute(
                select(Document)
                .where(
                    Document.status != "deleted",
                    Document.source.in_(sources),
                    Document.user_id == user_id if user_id else True,
                )
                .order_by(desc(Document.updated_at))
            )
            docs = result.scalars().all()
            if not docs:
                return []

            items: list[dict] = []
            for doc in docs:
                item = _doc_to_dict(doc)
                if doc.latest_version_id:
                    ver_result = await session.execute(
                        select(DocumentVersion).where(
                            DocumentVersion.id == doc.latest_version_id
                        )
                    )
                    ver = ver_result.scalar_one_or_none()
                    if ver:
                        meta = ver.meta or {}
                        item["latest_metadata"] = meta
                        item["latest_content_chars"] = len(ver.content_md or "")
                        item["latest_parser"] = meta.get("parser")
                items.append(item)
            return items

    # ─── Write (create or update) ──────────────────────────────────────────

    async def write_document(
        self,
        *,
        document_id: str = "",
        title: str,
        doc_type: str = "note",
        source: str = "agent_generated",
        created_by: str = "agent",
        content_md: str,
        summary: str | None = None,
        metadata: dict | None = None,
        ingest_to_rag: bool = False,
        user_id: str | None = None,
        source_path: str = "",
        content_hash: str | None = None,
    ) -> dict:
        """Create a new document or update an existing one (creates a new version).

        Returns dict with: document, version, created, ingest (optional).
        """
        now = _now()
        meta = metadata or {}

        # Artifact import is idempotent: if this artifact was already imported,
        # return the existing document instead of creating a duplicate.
        artifact_id = meta.get("artifactId") if source == "artifact_import" else None
        if artifact_id:
            existing = await self._find_imported_artifact(artifact_id)
            if existing is not None:
                return {
                    "document": existing["document"],
                    "version": existing["version"],
                    "created": False,
                    "already_imported": True,
                    "ingest": None,
                }

        async with self._get_db() as session:
            if document_id:
                # Update existing document — create new version
                result = await session.execute(
                    select(Document).where(Document.id == document_id)
                )
                doc = result.scalar_one_or_none()
                if doc is None:
                    raise ValueError(f"Document not found: {document_id}")

                # Determine next version number
                ver_result = await session.execute(
                    select(func.max(DocumentVersion.version)).where(
                        DocumentVersion.document_id == document_id
                    )
                )
                max_ver = ver_result.scalar() or 0
                next_ver = max_ver + 1

                version = DocumentVersion(
                    id=new_document_version_id(),
                    document_id=document_id,
                    version=next_ver,
                    content_md=content_md,
                    summary=summary,
                    meta=meta,
                    created_at=now,
                )
                session.add(version)

                doc.title = title
                doc.doc_type = doc_type
                doc.latest_version = next_ver
                doc.latest_version_id = version.id
                doc.updated_at = now
                if source_path is not None:
                    doc.source_path = source_path
                if content_hash is not None:
                    doc.content_hash = content_hash
                created = False
            else:
                # Create new document
                document_id = new_document_id()
                doc = Document(
                    id=document_id,
                    user_id=user_id,
                    title=title,
                    doc_type=doc_type,
                    source=source,
                    status="active",
                    created_by=created_by,
                    created_at=now,
                    updated_at=now,
                    latest_version=1,
                    latest_version_id="",
                    source_path=source_path,
                    content_hash=content_hash,
                )
                version = DocumentVersion(
                    id=new_document_version_id(),
                    document_id=document_id,
                    version=1,
                    content_md=content_md,
                    summary=summary,
                    meta=meta,
                    created_at=now,
                )
                doc.latest_version_id = version.id
                session.add(doc)
                session.add(version)
                created = True

            await session.flush()

            doc_dict = _doc_to_dict(doc)
            ver_dict = _ver_to_dict(version)

        # Optional RAG ingest
        ingest_info: dict | None = None
        if ingest_to_rag and self._rag:
            ingest_info = await self._ingest_content(
                content_md, document_id, version.id, user_id=user_id
            )

        return {
            "document": doc_dict,
            "version": ver_dict,
            "created": created,
            "already_imported": False,
            "ingest": ingest_info,
        }

    async def _find_imported_artifact(self, artifact_id: str) -> dict | None:
        """Return the active document previously imported from this artifactId, or None."""
        async with self._get_db() as session:
            result = await session.execute(
                select(Document)
                .join(DocumentVersion, DocumentVersion.document_id == Document.id)
                .where(
                    Document.status != "deleted",
                    DocumentVersion.meta["artifactId"].as_string() == artifact_id,
                )
                .order_by(desc(Document.updated_at))
                .limit(1)
            )
            doc = result.scalars().first()
            if doc is None:
                return None

            ver_result = await session.execute(
                select(DocumentVersion).where(
                    DocumentVersion.id == doc.latest_version_id
                )
            )
            ver = ver_result.scalar_one_or_none()
            return {
                "document": _doc_to_dict(doc),
                "version": _ver_to_dict(ver) if ver else None,
            }

    # ─── Read ──────────────────────────────────────────────────────────────

    async def get_document(self, document_id: str) -> dict | None:
        """Get document + latest version."""
        async with self._get_db() as session:
            result = await session.execute(
                select(Document).where(Document.id == document_id)
            )
            doc = result.scalar_one_or_none()
            if doc is None:
                return None

            ver_result = await session.execute(
                select(DocumentVersion).where(
                    DocumentVersion.id == doc.latest_version_id
                )
            )
            ver = ver_result.scalar_one_or_none()
            if ver is None:
                return None

            return {
                "document": _doc_to_dict(doc),
                "version": _ver_to_dict(ver),
            }

    async def list_versions(self, document_id: str) -> list[dict]:
        """List all versions of a document, ordered by version DESC."""
        async with self._get_db() as session:
            result = await session.execute(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == document_id)
                .order_by(desc(DocumentVersion.version))
            )
            versions = result.scalars().all()
            return [_ver_to_dict(v) for v in versions]

    async def get_version(self, version_id: str) -> dict | None:
        """Get a specific version by ID."""
        async with self._get_db() as session:
            result = await session.execute(
                select(DocumentVersion).where(DocumentVersion.id == version_id)
            )
            ver = result.scalar_one_or_none()
            if ver is None:
                return None
            return _ver_to_dict(ver)

    # ─── Delete ────────────────────────────────────────────────────────────

    async def delete_versions_by_document(self, document_id: str) -> int:
        """Delete all RAG chunks for a document (all versions) from PG + ES + Milvus + KG.

        Delegates to RAGService.delete_by_document_id for the four-way cleanup.
        Returns the number of PG rows deleted.
        """
        if not self._rag:
            return 0
        try:
            return await self._rag.delete_by_document_id(document_id)
        except Exception as e:
            logger.warning(
                "delete_versions_by_document failed for doc %s: %s",
                document_id,
                e,
            )
            return 0

    async def delete_document(self, document_id: str) -> int:
        """Soft-delete document + clean up RAG chunks. Returns deleted chunk count."""
        async with self._get_db() as session:
            result = await session.execute(
                select(Document).where(Document.id == document_id)
            )
            doc = result.scalar_one_or_none()
            if doc is None:
                raise ValueError(f"Document not found: {document_id}")

            # Soft delete
            doc.status = "deleted"
            doc.updated_at = _now()

            # Get all versions to compute doc_hashes
            ver_result = await session.execute(
                select(DocumentVersion).where(
                    DocumentVersion.document_id == document_id
                )
            )
            versions = ver_result.scalars().all()

        # Clean up RAG chunks for each version's doc_hash
        total_deleted = 0
        if self._rag:
            for ver in versions:
                dh = _doc_hash(ver.content_md)
                deleted = await self._rag.delete_by_doc_hash(dh)
                total_deleted += deleted

        return total_deleted

    # ─── Ingest version to RAG ─────────────────────────────────────────────

    async def ingest_version(self, document_id: str, version_id: str, *, user_id: str | None = None) -> dict:
        """Ingest a specific version's content into RAG, backfilling traceability fields."""
        async with self._get_db() as session:
            result = await session.execute(
                select(DocumentVersion).where(DocumentVersion.id == version_id)
            )
            ver = result.scalar_one_or_none()
            if ver is None:
                raise ValueError(f"Version not found: {version_id}")

            content_md = ver.content_md

        # Clean old RAG data for this document before re-ingesting
        await self.delete_versions_by_document(document_id)
        return await self._ingest_content(content_md, document_id, version_id, user_id=user_id)

    # ─── Upload file (one-stop) ────────────────────────────────────────────

    async def upload_file(
        self,
        filename: str,
        content_type: str,
        data: bytes,
        *,
        document_id: str = "",
        title: str | None = None,
        doc_type: str = "upload",
        user_id: str | None = None,
    ) -> dict:
        """Parse file → create document → ingest to RAG (one-stop).

        Returns UploadResult dict. If needs_ocr, returns early without creating a document.
        If document_id is provided, creates a new version for the existing document
        instead of creating a new document.
        """
        result = parse_bytes(filename, content_type, data)

        if result.needs_ocr:
            return {
                "filename": result.filename,
                "content_type": result.content_type,
                "parser": result.parser,
                "pages": result.pages,
                "text_chars": result.text_chars,
                "needs_ocr": True,
                "chunk_count": 0,
                "success": False,
                "message": "PDF 文本抽取结果过少，可能是扫描件，需要 OCR 后再入库",
            }

        # Build metadata from parse result
        meta: dict[str, Any] = {
            "filename": result.filename,
            "content_type": result.content_type,
            "parser": result.parser,
            "pages": result.pages,
            "text_chars": result.text_chars,
            "needs_ocr": result.needs_ocr,
        }

        doc_title = title or result.filename or "Untitled"

        write_result = await self.write_document(
            document_id=document_id,
            title=doc_title,
            doc_type=doc_type,
            source="user_upload",
            created_by="user",
            content_md=result.content,
            metadata=meta,
            ingest_to_rag=True,
            user_id=user_id,
        )

        ingest = write_result.get("ingest") or {}
        return {
            "filename": result.filename,
            "content_type": result.content_type,
            "parser": result.parser,
            "pages": result.pages,
            "text_chars": result.text_chars,
            "needs_ocr": False,
            "chunk_count": ingest.get("chunk_count", 0),
            "doc_hash": ingest.get("doc_hash", ""),
            "document": write_result["document"],
            "version": write_result["version"],
            "success": True,
        }

    # ─── Internal: ingest content to RAG + backfill ────────────────────────

    async def _ingest_content(
        self, content_md: str, document_id: str, version_id: str,
        *, user_id: str | None = None,
    ) -> dict:
        """Ingest content to RAG and backfill document_id/version_id on chunks."""
        dh = _doc_hash(content_md)

        # Clean old RAG data for this document before ingesting new content
        await self.delete_versions_by_document(document_id)

        # Call RAGService.ingest() to split + embed + index
        chunk_count = 0
        if self._rag:
            try:
                chunk_count = await self._rag.ingest(content_md, user_id=user_id)
            except Exception as e:
                logger.warning("RAG ingest failed for doc %s: %s", document_id, e)

        # Backfill document_id / version_id on rag_chunks with this doc_hash
        if chunk_count > 0:
            try:
                async with self._get_db() as session:
                    await session.execute(
                        update(RagChunk)
                        .where(RagChunk.doc_hash == dh)
                        .values(document_id=document_id, version_id=version_id, user_id=user_id)
                    )
            except Exception as e:
                logger.warning(
                    "Backfill document_id failed for doc %s: %s", document_id, e
                )

        return {
            "chunk_count": chunk_count,
            "doc_hash": dh,
            "indexed_count": chunk_count,
        }


# ─── Helpers ───────────────────────────────────────────────────────────────


def _doc_to_dict(doc: Document) -> dict:
    """Convert Document ORM row to API dict."""
    return {
        "id": doc.id,
        "user_id": doc.user_id,
        "title": doc.title,
        "doc_type": doc.doc_type,
        "source": doc.source,
        "status": doc.status,
        "created_by": doc.created_by,
        "created_at": doc.created_at,
        "updated_at": doc.updated_at,
        "latest_version": doc.latest_version,
        "latest_version_id": doc.latest_version_id,
        "source_path": doc.source_path,
        "content_hash": doc.content_hash,
    }


def _ver_to_dict(ver: DocumentVersion) -> dict:
    """Convert DocumentVersion ORM row to API dict."""
    return {
        "id": ver.id,
        "document_id": ver.document_id,
        "version": ver.version,
        "content_md": ver.content_md,
        "summary": ver.summary,
        "metadata": ver.meta or {},
        "created_at": ver.created_at,
    }
