"""DocumentService — global knowledge-base document lifecycle management.

CRUD + version management + RAG bridging (ingest backfill, delete cleanup).
Documents are independent of conversations; all agents share the same knowledge base.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from typing import Any

from sqlalchemy import desc, func, select, update

from app.config import get_settings
from app.db.engine import get_remote_db
from app.db.models import Document, DocumentVersion, RagChunk
from app.graph.types import ChunkRef
from app.rag.parsers.base import ParseResult
from app.rag.parsers.unified import parse_document
from app.rag.parsers.zip_utils import ZipParseResult
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

    # ─── Virtual directory tree (all sources, parent_id based) ──────

    async def list_tree(self, path: str, user_id: str, *, parent_id: str | None = None) -> dict:
        """Return virtual directory tree for all document sources.

        When ``parent_id`` is provided, returns children of that folder.
        When ``parent_id`` is None, returns root-level documents/folders.
        Falls back to source_path-derived tree when no documents have parent_id set.
        """
        async with self._get_db() as session:
            # Check if any documents have parent_id set (new tree mode)
            has_parent_result = await session.execute(
                select(func.count()).select_from(Document).where(
                    Document.parent_id.isnot(None),
                    Document.status != "deleted",
                    Document.user_id == user_id if user_id else True,
                )
            )
            has_parent = has_parent_result.scalar() or 0

            if has_parent > 0 or parent_id is not None:
                # New parent_id-based tree
                if parent_id is not None:
                    result = await session.execute(
                        select(Document).where(
                            Document.parent_id == parent_id,
                            Document.status != "deleted",
                            Document.user_id == user_id if user_id else True,
                        ).order_by(Document.is_folder.desc(), Document.title)
                    )
                else:
                    result = await session.execute(
                        select(Document).where(
                            Document.parent_id.is_(None),
                            Document.status != "deleted",
                            Document.user_id == user_id if user_id else True,
                        ).order_by(Document.is_folder.desc(), Document.title)
                    )
                docs = result.scalars().all()

                folders = []
                files = []
                for doc in docs:
                    item = {
                        "id": doc.id,
                        "title": doc.title,
                        "source_path": doc.source_path,
                        "doc_type": doc.doc_type,
                        "source": doc.source,
                        "updated_at": doc.updated_at,
                        "is_folder": doc.is_folder,
                        "parent_id": doc.parent_id,
                    }
                    if doc.is_folder:
                        folders.append(item)
                    else:
                        files.append(item)

                return {
                    "current_path": path,
                    "folders": sorted(folders, key=lambda f: f["title"]),
                    "files": sorted(files, key=lambda f: f["title"]),
                }

            # Legacy: source_path-derived tree (obsidian_sync compat)
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
                    "is_folder": False,
                    "parent_id": doc.parent_id,
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

    # ─── Folder management ──────────────────────────────────────────────

    async def create_folder(self, *, user_id: str, parent_id: str | None, name: str) -> dict:
        """Create a virtual folder (Document with is_folder=True)."""
        now = _now()
        folder_id = new_document_id()
        async with self._get_db() as session:
            folder = Document(
                id=folder_id,
                user_id=user_id,
                title=name,
                doc_type="folder",
                source="user_upload",
                status="active",
                created_by="user",
                created_at=now,
                updated_at=now,
                latest_version=0,
                latest_version_id="",
                source_path="",
                is_folder=True,
                parent_id=parent_id,
            )
            session.add(folder)
            await session.flush()
            return _doc_to_dict(folder)

    async def move_document(self, document_id: str, target_parent_id: str | None) -> dict:
        """Move a document/folder to a new parent (updates parent_id)."""
        now = _now()
        async with self._get_db() as session:
            result = await session.execute(
                select(Document).where(Document.id == document_id)
            )
            doc = result.scalar_one_or_none()
            if doc is None:
                raise ValueError(f"Document not found: {document_id}")
            if doc.status == "deleted":
                raise ValueError(f"Cannot move a deleted document: {document_id}")

            # Prevent moving a folder into itself or its descendant
            if target_parent_id and document_id == target_parent_id:
                raise ValueError("Cannot move a document into itself")
            if target_parent_id and doc.is_folder:
                # Check for cycles: walk up the target's parent chain
                current_id = target_parent_id
                while current_id:
                    if current_id == document_id:
                        raise ValueError("Cannot move a folder into its own descendant")
                    parent_result = await session.execute(
                        select(Document.parent_id).where(Document.id == current_id)
                    )
                    current_id = parent_result.scalar_one_or_none()

            doc.parent_id = target_parent_id
            doc.updated_at = now
            await session.flush()
            return _doc_to_dict(doc)

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
        preset_id: str = "",
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
                if preset_id:
                    doc.chunk_preset = preset_id
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
                    chunk_preset=preset_id or "general",
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
                content_md, document_id, version.id, user_id=user_id,
                preset_id=preset_id,
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

            # 状态机检查：检查转换合法性
            from app.rag.file_lifecycle import DocumentLifecycleManager
            if not DocumentLifecycleManager.is_valid_transition(doc.status, "deleted"):
                raise ValueError(
                    f"Cannot delete document in status '{doc.status}' → 'deleted'"
                )

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

    async def ingest_version(
        self, document_id: str, version_id: str, *, user_id: str | None = None,
        preset_id: str = "",
    ) -> dict:
        """Ingest a specific version's content into RAG.

        When ``rag_task_worker_enabled=True``, creates a ``RagTask(type='ingest')``
        for the worker to process asynchronously. Otherwise falls back to
        synchronous ingest.
        """
        # Resolve preset_id from UserSettings or config.py if not explicitly provided
        resolved_preset_id = await self._resolve_preset_id(user_id, preset_id)

        if get_settings().rag_task_worker_enabled:
            rag_task_id = await self._create_ingest_task(
                user_id=user_id or "",
                document_id=document_id,
                version_id=version_id,
                preset_id=resolved_preset_id,
            )
            return {"rag_task_id": rag_task_id, "status": "pending"}

        # Synchronous fallback (degraded mode: rag_task_worker_enabled=False)
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
        return await self._ingest_content(
            content_md, document_id, version_id, user_id=user_id, preset_id=resolved_preset_id
        )

    # ─── Upload file (one-stop) ────────────────────────────────────────────

    async def _resolve_preset_id(self, user_id: str | None, preset_id: str) -> str:
        """Resolve preset_id: explicit > UserSettings.rag_chunk_preset > config.py default."""
        if preset_id:
            return preset_id
        if user_id:
            try:
                from app.services.settings_service import get_user_settings
                us = await get_user_settings(user_id)
                if us.rag_chunk_preset:
                    return us.rag_chunk_preset
            except Exception as e:
                logger.warning("Failed to read user RAG preset: %s", e)
        return get_settings().rag_chunk_preset

    async def _resolve_ocr_engine(self, user_id: str | None) -> str:
        """Resolve ocr_engine: UserSettings.ocr_engine > config.py default."""
        if user_id:
            try:
                from app.services.settings_service import get_user_settings
                us = await get_user_settings(user_id)
                if us.ocr_engine:
                    return us.ocr_engine
            except Exception as e:
                logger.warning("Failed to read user OCR engine: %s", e)
        return get_settings().ocr_engine

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
        preset_id: str = "",
    ) -> dict:
        """Parse file → create document → ingest to RAG (one-stop).

        Returns UploadResult dict. If needs_ocr, returns early without creating a document.
        If document_id is provided, creates a new version for the existing document
        instead of creating a new document.
        """
        ocr_engine = await self._resolve_ocr_engine(user_id)
        result = parse_document(filename, content_type, data, ocr_engine=ocr_engine)

        # Handle ZIP archives — aggregate content from all contained files
        if isinstance(result, ZipParseResult):
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
                    "message": "ZIP 中包含需要 OCR 的文件，请先处理后再入库",
                }
            content = result.content
            parser_name = result.parser
            pages = result.pages
            text_chars = result.text_chars
        elif isinstance(result, ParseResult):
            content = result.content
            parser_name = result.parser
            pages = result.pages
            text_chars = result.text_chars
        else:
            content = str(result)
            parser_name = "unknown"
            pages = 0
            text_chars = len(content)

        needs_ocr = result.needs_ocr if hasattr(result, "needs_ocr") else False

        if needs_ocr:
            return {
                "filename": getattr(result, "filename", filename),
                "content_type": getattr(result, "content_type", content_type),
                "parser": parser_name,
                "pages": pages,
                "text_chars": text_chars,
                "needs_ocr": True,
                "chunk_count": 0,
                "success": False,
                "message": "文件文本抽取结果过少，可能是扫描件，需要 OCR 后再入库",
            }

        # Build metadata from parse result
        meta: dict[str, Any] = {
            "filename": getattr(result, "filename", filename),
            "content_type": getattr(result, "content_type", content_type),
            "parser": parser_name,
            "pages": pages,
            "text_chars": text_chars,
            "needs_ocr": needs_ocr,
        }

        doc_title = title or getattr(result, "filename", filename) or "Untitled"

        # Resolve preset_id from UserSettings or config.py if not explicitly provided
        resolved_preset_id = await self._resolve_preset_id(user_id, preset_id)

        write_result = await self.write_document(
            document_id=document_id,
            title=doc_title,
            doc_type=doc_type,
            source="user_upload",
            created_by="user",
            content_md=content,
            metadata=meta,
            ingest_to_rag=not get_settings().rag_task_worker_enabled,
            user_id=user_id,
            preset_id=resolved_preset_id,
        )

        # Write extracted images to workspace
        extracted_images = getattr(result, "images", None) or []
        if extracted_images:
            doc_id = write_result["document"]["id"]
            image_meta = _write_images_to_workspace(doc_id, extracted_images)
            if image_meta:
                meta["images"] = image_meta
                await self._update_version_meta(
                    write_result["version"]["id"], meta
                )

        ingest = write_result.get("ingest") or {}

        # If worker is enabled and ingest was deferred, create a RagTask
        rag_task_id = ""
        if get_settings().rag_task_worker_enabled and not ingest:
            doc_id = write_result["document"]["id"]
            ver_id = write_result["version"]["id"]
            rag_task_id = await self._create_ingest_task(
                user_id=user_id or "",
                document_id=doc_id,
                version_id=ver_id,
                preset_id=resolved_preset_id,
            )

        return {
            "filename": getattr(result, "filename", filename),
            "content_type": getattr(result, "content_type", content_type),
            "parser": parser_name,
            "pages": pages,
            "text_chars": text_chars,
            "needs_ocr": False,
            "chunk_count": ingest.get("chunk_count", 0),
            "doc_hash": ingest.get("doc_hash", ""),
            "document": write_result["document"],
            "version": write_result["version"],
            "success": True,
            "rag_task_id": rag_task_id,
        }

    # ─── Internal: create RagTask for ingest ───────────────────────────────

    async def _create_ingest_task(
        self, *, user_id: str, document_id: str, version_id: str,
        preset_id: str = "",
    ) -> str:
        """Create a pending RagTask(type='ingest') for the worker to process."""
        from app.db.engine import get_local_db
        from app.db.models import RagTask
        from app.utils.ids import new_rag_task_id

        now = time.time()
        settings = get_settings()
        async with get_local_db() as session:
            task = RagTask(
                id=new_rag_task_id(),
                user_id=user_id,
                task_type="ingest",
                document_id=document_id,
                version_id=version_id,
                status="pending",
                payload={"preset_id": preset_id},
                result=None,
                error_message=None,
                retry_count=0,
                max_retries=settings.rag_task_max_retries,
                created_at=now,
                updated_at=now,
                started_at=None,
                completed_at=None,
            )
            session.add(task)
        logger.info(
            "DocumentService: enqueued ingest task for doc=%s ver=%s",
            document_id, version_id,
        )
        return task.id

    # ─── Internal: ingest content to RAG + backfill ────────────────────────

    async def _ingest_content(
        self, content_md: str, document_id: str, version_id: str,
        *, user_id: str | None = None, preset_id: str = "",
    ) -> dict:
        """Ingest content to RAG and backfill document_id/version_id on chunks."""
        dh = _doc_hash(content_md)

        # Clean old RAG data for this document before ingesting new content
        await self.delete_versions_by_document(document_id)

        # 状态流转：active/parsed → indexing
        await self._try_transition(document_id, "indexing")

        # Call RAGService.ingest() to split + embed + index
        chunk_count = 0
        if self._rag:
            try:
                chunk_count = await self._rag.ingest(
                    content_md, user_id=user_id, preset_id=preset_id
                )
            except Exception as e:
                logger.warning("RAG ingest failed for doc %s: %s", document_id, e)
                await self._try_transition(document_id, "error")
                return {"chunk_count": 0, "doc_hash": dh, "indexed_count": 0}

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

        # Update Document.chunk_preset if preset_id was provided
        if preset_id:
            try:
                async with self._get_db() as session:
                    await session.execute(
                        update(Document)
                        .where(Document.id == document_id)
                        .values(chunk_preset=preset_id)
                    )
            except Exception as e:
                logger.warning(
                    "Failed to set chunk_preset for doc %s: %s", document_id, e
                )

        # ── Graph build trigger (async, non-blocking) ──
        # 7.2: Set graph_status = 'graph_pending' after ingest
        # 7.1: If rag_graph_auto_build=True, trigger GraphBuildTask
        # 7.3: Collect chunk_refs from PG and pass to GraphBuildTask
        settings = get_settings()
        if chunk_count > 0 and settings.rag_graph_auto_build:
            chunk_refs = await self._collect_chunk_refs(dh)
            if chunk_refs:
                try:
                    await self._set_graph_status(document_id, "graph_pending")
                    asyncio.create_task(
                        _trigger_graph_build(dh, chunk_refs, document_id)
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to trigger GraphBuildTask for doc %s: %s",
                        document_id, e,
                    )

        # 状态流转：indexing → indexed → active
        if chunk_count > 0:
            await self._try_transition(document_id, "indexed")
            await self._try_transition(document_id, "active")

        return {
            "chunk_count": chunk_count,
            "doc_hash": dh,
            "indexed_count": chunk_count,
        }

    async def _collect_chunk_refs(self, doc_hash: str) -> list[ChunkRef]:
        """从 PG 查询 chunk_refs（按 doc_hash），用于 GraphBuildTask。"""
        try:
            async with self._get_db() as session:
                result = await session.execute(
                    select(RagChunk.id, RagChunk.chunk_idx, RagChunk.content)
                    .where(RagChunk.doc_hash == doc_hash)
                    .order_by(RagChunk.chunk_idx)
                )
                rows = result.all()
        except Exception as e:
            logger.warning("Failed to collect chunk_refs for doc_hash=%s: %s", doc_hash, e)
            return []
        return [
            ChunkRef(id=row[1] or idx, pg_id=row[0], content=row[2] or "")
            for idx, row in enumerate(rows)
        ]

    async def _set_graph_status(self, document_id: str, status: str) -> None:
        """更新 Document.graph_status。"""
        try:
            async with self._get_db() as session:
                await session.execute(
                    update(Document)
                    .where(Document.id == document_id)
                    .values(graph_status=status)
                )
        except Exception as e:
            logger.warning(
                "Failed to set graph_status=%s for doc %s: %s",
                status, document_id, e,
            )

    async def _update_version_meta(self, version_id: str, meta: dict) -> None:
        """Update DocumentVersion.meta after image paths are written."""
        try:
            async with self._get_db() as session:
                await session.execute(
                    update(DocumentVersion)
                    .where(DocumentVersion.id == version_id)
                    .values(meta=meta)
                )
        except Exception as e:
            logger.warning("Failed to update version meta for %s: %s", version_id, e)

    async def _try_transition(self, document_id: str, target: str) -> None:
        """尝试状态转换，失败时记录日志但不中断主流程。"""
        try:
            from app.rag.file_lifecycle import DocumentLifecycleManager
            result = await DocumentLifecycleManager.transition(
                document_id, target
            )
            if not result["success"]:
                logger.debug(
                    "Status transition to '%s' for doc %s: %s",
                    target, document_id, result["message"],
                )
        except Exception as e:
            logger.warning(
                "Status transition to '%s' failed for doc %s: %s",
                target, document_id, e,
            )


async def _trigger_graph_build(
    doc_hash: str, chunk_refs: list[ChunkRef], document_id: str
) -> None:
    """异步触发 GraphBuildTask（fire-and-forget wrapper）。"""
    try:
        from app.rag.graph_build_task import GraphBuildTask
        await GraphBuildTask.build(doc_hash, chunk_refs, document_id=document_id)
    except Exception as e:
        logger.error("GraphBuildTask failed for doc %s: %s", document_id, e)


# ─── Helpers ───────────────────────────────────────────────────────────────


def _write_images_to_workspace(
    document_id: str, images: list,
) -> list[dict[str, str]]:
    """Write extracted images to <data_dir>/documents/<doc_id>/images/.

    Returns a list of {"filename", "path", "content_type"} dicts for meta.
    """
    from app.rag.parsers.base import ExtractedImage

    settings = get_settings()
    base_dir = settings.data_path / "documents" / document_id / "images"
    try:
        os.makedirs(str(base_dir), exist_ok=True)
    except OSError as e:
        logger.warning("Failed to create image dir %s: %s", base_dir, e)
        return []

    image_meta: list[dict[str, str]] = []
    for img in images:
        if not isinstance(img, ExtractedImage) or not img.data:
            continue
        try:
            file_path = base_dir / img.filename
            with open(str(file_path), "wb") as f:
                f.write(img.data)
            rel_path = f"documents/{document_id}/images/{img.filename}"
            image_meta.append({
                "filename": img.filename,
                "path": rel_path,
                "content_type": img.content_type,
            })
        except OSError as e:
            logger.warning("Failed to write image %s: %s", img.filename, e)
    return image_meta


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
        "chunk_preset": doc.chunk_preset,
        "parent_id": doc.parent_id,
        "is_folder": doc.is_folder,
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
