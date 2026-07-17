"""Debounce incremental CodeGraph sync after a ready project changes."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.code_intelligence.metadata import MetadataStore

SyncCallback = Callable[[Path, Path], Awaitable[None]]


class ReadySyncDebouncer:
    def __init__(self, callback: SyncCallback, *, delay_seconds: float = 1.0) -> None:
        if delay_seconds < 0:
            raise ValueError("Code intelligence debounce delay cannot be negative")
        self._callback = callback
        self._delay_seconds = delay_seconds
        self._tasks: dict[str, asyncio.Task[None]] = {}

    @property
    def pending_projects(self) -> tuple[str, ...]:
        return tuple(sorted(self._tasks))

    def notify(self, workspace_root: Path, project_path: Path) -> bool:
        metadata = MetadataStore(workspace_root).read()
        if not metadata.enabled or metadata.status != "ready":
            return False

        workspace = Path(workspace_root)
        project = Path(project_path).resolve()
        key = os.path.normcase(str(project))
        previous = self._tasks.get(key)
        if previous is not None:
            previous.cancel()

        task = asyncio.create_task(
            self._wait_and_sync(workspace, project),
            name=f"code-intelligence:debounce:{project.name}",
        )
        self._tasks[key] = task

        def cleanup(completed: asyncio.Task[None]) -> None:
            if self._tasks.get(key) is completed:
                self._tasks.pop(key, None)

        task.add_done_callback(cleanup)
        return True

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*tuple(self._tasks.values()), return_exceptions=True)

    async def shutdown(self) -> None:
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def _wait_and_sync(self, workspace_root: Path, project_path: Path) -> None:
        await asyncio.sleep(self._delay_seconds)
        metadata = MetadataStore(workspace_root).read()
        if metadata.enabled and metadata.status == "ready":
            await self._callback(workspace_root, project_path)
