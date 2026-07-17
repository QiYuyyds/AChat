"""Coordinate isolated CodeGraph work across local projects."""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.code_intelligence.progress import ProgressCallback

IndexRunner = Callable[..., Awaitable[dict[str, Any]]]


class CodeIntelligenceBusy(RuntimeError):
    """Raised when a project already has active CodeGraph work."""


@dataclass
class _ManagedTask:
    task: asyncio.Task[dict[str, Any]]
    cancel_event: asyncio.Event


class CodeIntelligenceManager:
    def __init__(self, *, runner: IndexRunner, max_concurrency: int = 1) -> None:
        if max_concurrency < 1:
            raise ValueError("Code intelligence concurrency must be at least 1")
        self._runner = runner
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._registry_lock = asyncio.Lock()
        self._tasks: dict[str, _ManagedTask] = {}
        self._shutting_down = False

    @property
    def active_projects(self) -> tuple[str, ...]:
        return tuple(sorted(self._tasks))

    async def start(
        self,
        project_path: Path,
        operation: str,
        progress_callback: ProgressCallback | None = None,
    ) -> asyncio.Task[dict[str, Any]]:
        key = self._project_key(project_path)
        path = Path(project_path).resolve()
        async with self._registry_lock:
            if self._shutting_down:
                raise RuntimeError("Code intelligence manager is shutting down")
            existing = self._tasks.get(key)
            if existing is not None and not existing.task.done():
                raise CodeIntelligenceBusy(f"Code intelligence task already active: {path}")
            cancel_event = asyncio.Event()
            task = asyncio.create_task(
                self._run(key, path, operation, cancel_event, progress_callback),
                name=f"code-intelligence:{operation}:{path.name}",
            )
            self._tasks[key] = _ManagedTask(task=task, cancel_event=cancel_event)
            return task

    async def cancel(self, project_path: Path) -> bool:
        key = self._project_key(project_path)
        async with self._registry_lock:
            managed = self._tasks.get(key)
            if managed is None or managed.task.done():
                return False
            managed.cancel_event.set()
            task = managed.task
        with contextlib.suppress(asyncio.CancelledError):
            await task
        return True

    async def shutdown(self) -> None:
        async with self._registry_lock:
            self._shutting_down = True
            managed_tasks = list(self._tasks.values())
            for managed in managed_tasks:
                managed.cancel_event.set()
        if managed_tasks:
            await asyncio.gather(
                *(managed.task for managed in managed_tasks),
                return_exceptions=True,
            )
        async with self._registry_lock:
            self._tasks.clear()

    async def _run(
        self,
        key: str,
        project_path: Path,
        operation: str,
        cancel_event: asyncio.Event,
        progress_callback: ProgressCallback | None,
    ) -> dict[str, Any]:
        try:
            async with self._semaphore:
                if progress_callback is None:
                    return await self._runner(project_path, operation, cancel_event)
                return await self._runner(
                    project_path,
                    operation,
                    cancel_event,
                    progress_callback,
                )
        finally:
            async with self._registry_lock:
                current = self._tasks.get(key)
                if current is not None and current.task is asyncio.current_task():
                    self._tasks.pop(key, None)

    @staticmethod
    def _project_key(project_path: Path) -> str:
        resolved = str(Path(project_path).resolve())
        return os.path.normcase(resolved)
