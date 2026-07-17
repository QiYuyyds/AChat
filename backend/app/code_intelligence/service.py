"""Background orchestration for CodeGraph runtime preparation and indexing."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Literal

from app.code_intelligence.debounce import ReadySyncDebouncer
from app.code_intelligence.index_manager import CodeIntelligenceManager
from app.code_intelligence.metadata import CodeIntelligenceCounts, MetadataStore
from app.code_intelligence.process_runner import CodeGraphCommandRunner
from app.code_intelligence.progress import CodeGraphProgress, ProgressCallback
from app.code_intelligence.runtime import ResolvedRuntime, RuntimeManager
from app.code_intelligence.state_machine import ALLOWED_TRANSITIONS, transition
from app.utils.clock import now_ms

IndexOperation = Literal["sync", "rebuild"]
logger = logging.getLogger(__name__)


class CodeIntelligenceService:
    def __init__(
        self,
        *,
        runtime_manager: RuntimeManager,
        index_manager: CodeIntelligenceManager,
        command_runner: CodeGraphCommandRunner | None = None,
    ) -> None:
        self.runtime_manager = runtime_manager
        self.index_manager = index_manager
        self.command_runner = command_runner
        self._background: set[asyncio.Task[Any]] = set()
        self._sync_debouncer = ReadySyncDebouncer(self._run_debounced_sync)

    def schedule_enable(
        self,
        *,
        workspace_root: Path,
        project_path: Path,
        download_approved: bool,
    ) -> asyncio.Task[None]:
        task = asyncio.create_task(
            self._enable_and_init(
                workspace_root=Path(workspace_root),
                project_path=Path(project_path).resolve(),
                download_approved=download_approved,
            ),
            name=f"code-intelligence:enable:{Path(project_path).name}",
        )
        return self._track(task)

    def schedule_operation(
        self,
        *,
        workspace_root: Path,
        project_path: Path,
        operation: IndexOperation,
    ) -> asyncio.Task[None]:
        if operation not in {"sync", "rebuild"}:
            raise ValueError(f"Unsupported code intelligence operation: {operation}")
        task = asyncio.create_task(
            self._run_operation(
                workspace_root=Path(workspace_root),
                project_path=Path(project_path).resolve(),
                operation=operation,
            ),
            name=f"code-intelligence:{operation}:{Path(project_path).name}",
        )
        return self._track(task)

    def notify_files_changed(self, *, workspace_root: Path, project_path: Path) -> bool:
        return self._sync_debouncer.notify(workspace_root, project_path)

    async def sync(self, *, workspace_root: Path, project_path: Path) -> None:
        await self.schedule_operation(
            workspace_root=workspace_root,
            project_path=project_path,
            operation="sync",
        )

    async def rebuild(self, *, workspace_root: Path, project_path: Path) -> None:
        await self.schedule_operation(
            workspace_root=workspace_root,
            project_path=project_path,
            operation="rebuild",
        )

    async def explore(
        self,
        *,
        workspace_root: Path,
        project_path: Path,
        query: str,
        cancel_event: asyncio.Event,
    ) -> str:
        if self.command_runner is None:
            raise RuntimeError("CodeGraph command runner is not initialized")
        metadata = MetadataStore(workspace_root).read()
        if not metadata.enabled or metadata.status != "ready":
            raise RuntimeError(
                f"Source intelligence is unavailable (state: {metadata.status})"
            )
        project = Path(project_path).resolve()
        if await self.command_runner.is_stale(project, cancel_event):
            await self.sync(workspace_root=workspace_root, project_path=project)
        return await self.command_runner.explore(project, query, cancel_event)

    async def retry(self, *, workspace_root: Path, project_path: Path) -> None:
        current = MetadataStore(workspace_root).read()
        if current.status not in {"failed", "interrupted"}:
            raise RuntimeError(
                f"Cannot retry source intelligence from state {current.status}"
            )
        await self.schedule_enable(
            workspace_root=workspace_root,
            project_path=project_path,
            download_approved=True,
        )

    async def cancel(self, *, workspace_root: Path, project_path: Path) -> bool:
        store = MetadataStore(workspace_root)
        current = store.read()
        if "cancelling" in ALLOWED_TRANSITIONS[current.status]:
            transition(store, "cancelling", phase="stopping source intelligence")
        cancelled = await self.index_manager.cancel(Path(project_path))
        if cancelled and store.read().status == "cancelling":
            transition(
                store,
                "interrupted",
                phase=None,
                error="Source intelligence work was cancelled",
            )
        return cancelled

    async def disable(self, *, workspace_root: Path, project_path: Path) -> None:
        await self.cancel(workspace_root=workspace_root, project_path=project_path)
        store = MetadataStore(workspace_root)
        current = store.read()
        if current.status == "disabled":
            return
        if "disabled" not in ALLOWED_TRANSITIONS[current.status]:
            raise RuntimeError(
                f"Cannot disable source intelligence from state {current.status}"
            )
        transition(store, "disabled")

    async def shutdown(self) -> None:
        await self._sync_debouncer.shutdown()
        await self.index_manager.shutdown()
        if self._background:
            await asyncio.gather(*tuple(self._background), return_exceptions=True)

    async def _run_debounced_sync(self, workspace_root: Path, project_path: Path) -> None:
        await self.sync(workspace_root=workspace_root, project_path=project_path)

    async def _enable_and_init(        self,
        *,
        workspace_root: Path,
        project_path: Path,
        download_approved: bool,
    ) -> None:
        store = MetadataStore(workspace_root)
        try:
            transition(store, "preparing_runtime", phase="resolving managed runtime")
            runtime = self.runtime_manager.resolve(download_approved=download_approved)
            if runtime.source == "packaged_archive":
                runtime = await self.runtime_manager.install_packaged(
                    runtime, cancel_event=asyncio.Event()
                )
            if runtime.source == "download":
                runtime = await self.runtime_manager.download_and_install(
                    runtime,
                    cancel_event=asyncio.Event(),
                )
            self._queue_runtime(store, runtime)
            transition(store, "indexing", phase="building source graph")
            task = await self.index_manager.start(
                project_path,
                "init",
                self._progress_callback(store),
            )
            result = await task
            self._mark_ready(store, result)
        except asyncio.CancelledError:
            self._mark_interrupted(store)
        except Exception as exc:  # noqa: BLE001 - isolate failures to this Workspace
            logger.exception("Source intelligence enable/init failed for %s", project_path)
            self._mark_failed(store, f"{type(exc).__name__}: {exc!r}")

    async def _run_operation(
        self,
        *,
        workspace_root: Path,
        project_path: Path,
        operation: IndexOperation,
    ) -> None:
        store = MetadataStore(workspace_root)
        status = "syncing" if operation == "sync" else "rebuilding"
        phase = "updating source graph" if operation == "sync" else "rebuilding source graph"
        try:
            transition(store, status, phase=phase)
            task = await self.index_manager.start(
                project_path,
                operation,
                self._progress_callback(store),
            )
            result = await task
            self._mark_ready(store, result)
        except asyncio.CancelledError:
            self._mark_interrupted(store)
        except Exception as exc:  # noqa: BLE001 - isolate failures to this Workspace
            self._mark_failed(store, str(exc))

    def _track(self, task: asyncio.Task[None]) -> asyncio.Task[None]:
        self._background.add(task)
        task.add_done_callback(self._background.discard)
        return task

    @staticmethod
    def _progress_callback(store: MetadataStore) -> ProgressCallback:
        loop = asyncio.get_running_loop()

        def report(progress: CodeGraphProgress) -> None:
            loop.call_soon_threadsafe(
                CodeIntelligenceService._record_progress,
                store,
                progress,
            )

        return report

    @staticmethod
    def _record_progress(store: MetadataStore, progress: CodeGraphProgress) -> None:
        current = store.read()
        if current.status not in {"indexing", "rebuilding", "syncing"}:
            return
        if (
            current.progress_percent is not None
            and progress.overall_percent <= current.progress_percent
        ):
            return
        store.write(
            current.model_copy(
                update={
                    "progress_percent": progress.overall_percent,
                    "updated_at": now_ms(),
                }
            )
        )

    @staticmethod
    def _queue_runtime(store: MetadataStore, runtime: ResolvedRuntime) -> None:
        transition(
            store,
            "queued",
            phase="waiting for index slot",
            updates={"runtime_version": runtime.artifact.version},
        )

    @staticmethod
    def _mark_ready(store: MetadataStore, result: dict[str, Any]) -> None:
        counts = CodeIntelligenceCounts.model_validate(
            {
                "files": result.get("files", 0),
                "symbols": result.get("symbols", 0),
                "relationships": result.get("relationships", 0),
            }
        )
        transition(store, "ready", phase=None, updates={"counts": counts})

    @staticmethod
    def _mark_failed(store: MetadataStore, error: str) -> None:
        current = store.read()
        if "failed" in ALLOWED_TRANSITIONS[current.status]:
            transition(store, "failed", phase=None, error=error[:2000])

    @staticmethod
    def _mark_interrupted(store: MetadataStore) -> None:
        current = store.read()
        if "cancelling" in ALLOWED_TRANSITIONS[current.status]:
            transition(store, "cancelling", phase="stopping source intelligence")
            current = store.read()
        if "interrupted" in ALLOWED_TRANSITIONS[current.status]:
            transition(
                store,
                "interrupted",
                phase=None,
                error="Source intelligence work was cancelled",
            )


_service: CodeIntelligenceService | None = None


def configure_code_intelligence_service(service: CodeIntelligenceService) -> None:
    global _service
    _service = service


def schedule_workspace_enable(
    *,
    workspace_root: Path,
    project_path: Path,
    download_approved: bool,
) -> asyncio.Task[None]:
    if _service is None:
        raise RuntimeError("Code intelligence service is not initialized")
    return _service.schedule_enable(
        workspace_root=workspace_root,
        project_path=project_path,
        download_approved=download_approved,
    )


def get_code_intelligence_service() -> CodeIntelligenceService:
    if _service is None:
        raise RuntimeError("Code intelligence service is not initialized")
    return _service


async def shutdown_code_intelligence_service() -> None:
    if _service is not None:
        await _service.shutdown()
