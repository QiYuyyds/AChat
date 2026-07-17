import asyncio
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_manager_allows_only_one_task_per_project(tmp_path: Path) -> None:
    from app.code_intelligence.index_manager import CodeIntelligenceBusy, CodeIntelligenceManager

    started = asyncio.Event()
    release = asyncio.Event()

    async def runner(project_path: Path, operation: str, cancel_event: asyncio.Event):
        started.set()
        await release.wait()
        return {"operation": operation}

    manager = CodeIntelligenceManager(runner=runner)
    first = await manager.start(tmp_path / "project", "init")
    await started.wait()

    with pytest.raises(CodeIntelligenceBusy):
        await manager.start(tmp_path / "project" / ".", "sync")

    release.set()
    assert await first == {"operation": "init"}
    await manager.shutdown()


@pytest.mark.asyncio
async def test_manager_limits_global_work_to_one_task(tmp_path: Path) -> None:
    from app.code_intelligence.index_manager import CodeIntelligenceManager

    active = 0
    max_active = 0
    release = asyncio.Event()

    async def runner(project_path: Path, operation: str, cancel_event: asyncio.Event):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await release.wait()
        active -= 1
        return {"project": str(project_path)}

    manager = CodeIntelligenceManager(runner=runner, max_concurrency=1)
    first = await manager.start(tmp_path / "one", "init")
    second = await manager.start(tmp_path / "two", "init")
    await asyncio.sleep(0)
    assert max_active == 1

    release.set()
    await asyncio.gather(first, second)
    assert max_active == 1
    await manager.shutdown()


@pytest.mark.asyncio
async def test_cancel_and_shutdown_signal_and_drain_tasks(tmp_path: Path) -> None:
    from app.code_intelligence.index_manager import CodeIntelligenceManager

    cancelled: set[str] = set()

    async def runner(project_path: Path, operation: str, cancel_event: asyncio.Event):
        await cancel_event.wait()
        cancelled.add(project_path.name)
        raise asyncio.CancelledError

    manager = CodeIntelligenceManager(runner=runner)
    first = await manager.start(tmp_path / "one", "init")
    second = await manager.start(tmp_path / "two", "init")

    assert await manager.cancel(tmp_path / "one") is True
    await manager.shutdown()

    assert cancelled == {"one", "two"}
    assert first.done()
    assert second.done()
    assert manager.active_projects == ()
