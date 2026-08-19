"""RagTaskWorker — asyncio background worker for the RAG task queue.

Polls ``pending`` tasks from the ``rag_tasks`` table (local SQLite) every
``rag_task_worker_interval`` seconds (default 5s). Processes tasks serially:
one at a time. On failure, retries up to ``max_retries`` times before marking
``failed_permanent``.

Independent from the global Task Board ``TaskSchedulerService`` — separate
table, separate worker, separate API prefix.

Lifecycle:
  pending → running → completed (terminal)
                   → failed (retryable, max_retries times)
  failed → pending (auto-retry or manual retry)
  failed_permanent (terminal, requires POST /retry)

On startup: scans ``running`` tasks and marks them ``failed`` (stale recovery).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

from sqlalchemy import select, update

from app.config import get_settings
from app.db.engine import get_local_db
from app.db.models import RagTask

logger = logging.getLogger(__name__)


def _now() -> float:
    return time.time()


class RagTaskWorker:
    """Singleton asyncio background worker for RAG task queue.

    Started in lifespan after RAGService / DocumentService init.
    When ``rag_task_worker_enabled=False``, caller should use synchronous fallback.
    """

    _instance: RagTaskWorker | None = None

    @classmethod
    def get_instance(cls) -> RagTaskWorker:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._document_service: Any | None = None

    def set_document_service(self, ds: Any) -> None:
        """Inject DocumentService instance for calling _ingest_content / _collect_chunk_refs."""
        self._document_service = ds

    async def start(self, interval_seconds: int = 5) -> None:
        if self._task and not self._task.done():
            return
        self._running = True
        # Stale recovery: mark running tasks as failed
        await self._recover_stale_tasks()
        self._task = asyncio.create_task(self._run_loop(interval_seconds))
        logger.info(
            "[RagTaskWorker] Started (interval=%ds, max_retries=%d)",
            interval_seconds, get_settings().rag_task_max_retries,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("[RagTaskWorker] Stopped")

    async def _run_loop(self, interval_seconds: int) -> None:
        try:
            while self._running:
                try:
                    await self._scan_and_dispatch()
                except Exception:
                    logger.exception("[RagTaskWorker] Error in scan cycle")
                await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            pass

    async def _scan_and_dispatch(self) -> None:
        """Query the oldest ``pending`` task and process it (serial)."""
        async with get_local_db() as session:
            result = await session.execute(
                select(RagTask)
                .where(RagTask.status == "pending")
                .order_by(RagTask.created_at.asc())
                .limit(1)
            )
            task = result.scalar_one_or_none()
            if task is None:
                return

            # Atomically transition pending → running
            task.status = "running"
            task.started_at = _now()
            task.updated_at = _now()

        # Process outside the DB session to avoid holding a long transaction
        await self._execute_task(task)

    async def _execute_task(self, task: RagTask) -> None:
        """Execute a single task by dispatching to the appropriate handler."""
        handler = {
            "parse": self._handle_parse,
            "ingest": self._handle_ingest,
            "graph_build": self._handle_graph_build,
            "delete_cleanup": self._handle_delete_cleanup,
        }.get(task.task_type)

        if handler is None:
            await self._mark_failed(task, f"Unknown task_type: {task.task_type}")
            return

        try:
            result = await handler(task)
            await self._mark_completed(task, result)
        except Exception as e:
            logger.warning(
                "[RagTaskWorker] Task %s failed (%s): %s",
                task.id, task.task_type, e,
            )
            await self._handle_failure(task, e)

    async def _handle_parse(self, task: RagTask) -> dict:
        """Parse task — reserved for future use (currently upload_file parses synchronously)."""
        logger.info("[RagTaskWorker] parse task %s (doc=%s)", task.id, task.document_id)
        return {"status": "skipped", "reason": "parse is done synchronously in upload_file"}

    async def _handle_ingest(self, task: RagTask) -> dict:
        """Ingest task — call DocumentService._ingest_content → optionally enqueue graph_build."""
        ds = self._document_service
        if ds is None:
            raise RuntimeError("DocumentService not injected into RagTaskWorker")

        document_id = task.document_id or ""
        version_id = task.version_id or ""
        if not document_id or not version_id:
            raise ValueError("ingest task requires document_id and version_id")

        # Fetch content from DocumentVersion
        from app.db.engine import get_remote_db
        from app.db.models import DocumentVersion

        async with get_remote_db() as session:
            ver_result = await session.execute(
                select(DocumentVersion).where(DocumentVersion.id == version_id)
            )
            ver = ver_result.scalar_one_or_none()
            if ver is None:
                raise ValueError(f"Version not found: {version_id}")
            content_md = ver.content_md

        payload = task.payload or {}
        preset_id = payload.get("preset_id", "")
        user_id = task.user_id

        ingest_result = await ds._ingest_content(
            content_md, document_id, version_id,
            user_id=user_id, preset_id=preset_id,
        )

        # If rag_graph_auto_build and ingest produced chunks, enqueue graph_build
        settings = get_settings()
        chunk_count = ingest_result.get("chunk_count", 0)
        if chunk_count > 0 and settings.rag_graph_auto_build:
            doc_hash = ingest_result.get("doc_hash", "")
            await self._enqueue_graph_build(
                user_id=task.user_id,
                document_id=document_id,
                doc_hash=doc_hash,
            )

        return ingest_result

    async def _handle_graph_build(self, task: RagTask) -> dict:
        """Graph build task — call GraphBuildTask.build."""
        from app.rag.graph_build_task import GraphBuildTask

        if not GraphBuildTask.available():
            return {"status": "skipped", "reason": "GraphBuildTask not available (KGStore/Extractor not injected)"}

        payload = task.payload or {}
        doc_hash = payload.get("doc_hash", "")
        document_id = task.document_id or payload.get("document_id", "")
        chunk_refs = await self._collect_chunk_refs(doc_hash)

        if not chunk_refs:
            return {"status": "skipped", "reason": "no chunk_refs found"}

        return await GraphBuildTask.build(doc_hash, chunk_refs, document_id=document_id)

    async def _handle_delete_cleanup(self, task: RagTask) -> dict:
        """Delete cleanup task — remove RAG chunks for a deleted document."""
        ds = self._document_service
        if ds is None:
            raise RuntimeError("DocumentService not injected into RagTaskWorker")

        document_id = task.document_id or ""
        if not document_id:
            return {"status": "skipped", "reason": "no document_id"}

        deleted = await ds.delete_versions_by_document(document_id)
        return {"deleted_chunks": deleted}

    async def _handle_failure(self, task: RagTask, error: Exception) -> None:
        """Retry logic: retry_count += 1; if < max_retries → back to pending; else failed_permanent."""
        settings = get_settings()
        max_retries = settings.rag_task_max_retries

        async with get_local_db() as session:
            result = await session.execute(
                select(RagTask).where(RagTask.id == task.id)
            )
            t = result.scalar_one_or_none()
            if t is None:
                return

            t.retry_count += 1
            t.updated_at = _now()
            t.completed_at = _now()
            t.error_message = str(error)

            if t.retry_count < max_retries:
                t.status = "pending"
                t.started_at = None
                t.completed_at = None
                logger.info(
                    "[RagTaskWorker] Task %s retry %d/%d",
                    t.id, t.retry_count, max_retries,
                )
            else:
                t.status = "failed_permanent"
                logger.warning(
                    "[RagTaskWorker] Task %s failed_permanent after %d retries",
                    t.id, max_retries,
                )

    async def _mark_completed(self, task: RagTask, result: dict) -> None:
        async with get_local_db() as session:
            await session.execute(
                update(RagTask)
                .where(RagTask.id == task.id)
                .values(
                    status="completed",
                    result=result,
                    completed_at=_now(),
                    updated_at=_now(),
                    error_message=None,
                )
            )
        logger.info("[RagTaskWorker] Task %s completed (%s)", task.id, task.task_type)

    async def _mark_failed(self, task: RagTask, error_msg: str) -> None:
        async with get_local_db() as session:
            await session.execute(
                update(RagTask)
                .where(RagTask.id == task.id)
                .values(
                    status="failed_permanent",
                    error_message=error_msg,
                    completed_at=_now(),
                    updated_at=_now(),
                )
            )

    async def _recover_stale_tasks(self) -> None:
        """Mark running tasks as failed on startup (stale recovery)."""
        now = _now()
        async with get_local_db() as session:
            result = await session.execute(
                select(RagTask).where(RagTask.status == "running")
            )
            stale_tasks = result.scalars().all()
            for t in stale_tasks:
                t.status = "failed"
                t.error_message = "Stale task recovered on restart"
                t.updated_at = now
                t.completed_at = now

        if stale_tasks:
            logger.warning(
                "[RagTaskWorker] Recovered %d stale running tasks", len(stale_tasks),
            )

    async def _collect_chunk_refs(self, doc_hash: str) -> list:
        """Collect chunk_refs for a doc_hash — delegates to DocumentService."""
        ds = self._document_service
        if ds is None:
            return []
        return await ds._collect_chunk_refs(doc_hash)

    async def _enqueue_graph_build(
        self, *, user_id: str, document_id: str, doc_hash: str,
    ) -> None:
        """Create a graph_build RagTask in pending status."""
        from app.utils.ids import new_rag_task_id

        now = _now()
        async with get_local_db() as session:
            task = RagTask(
                id=new_rag_task_id(),
                user_id=user_id,
                task_type="graph_build",
                document_id=document_id,
                version_id=None,
                status="pending",
                payload={"doc_hash": doc_hash, "document_id": document_id},
                result=None,
                error_message=None,
                retry_count=0,
                max_retries=get_settings().rag_task_max_retries,
                created_at=now,
                updated_at=now,
                started_at=None,
                completed_at=None,
            )
            session.add(task)
        logger.info(
            "[RagTaskWorker] Enqueued graph_build task for doc %s", document_id,
        )
