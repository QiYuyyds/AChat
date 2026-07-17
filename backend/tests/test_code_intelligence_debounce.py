import asyncio
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_ready_file_changes_are_debounced_to_one_sync(tmp_path: Path) -> None:
    from app.code_intelligence.debounce import ReadySyncDebouncer
    from app.code_intelligence.metadata import CodeIntelligenceMetadata, MetadataStore

    workspace_root = tmp_path / "internal"
    project_path = tmp_path / "project"
    MetadataStore(workspace_root).write(
        CodeIntelligenceMetadata(enabled=True, status="ready")
    )
    calls: list[Path] = []

    async def sync_callback(workspace: Path, project: Path) -> None:
        calls.append(project)

    debouncer = ReadySyncDebouncer(sync_callback, delay_seconds=0.01)

    assert debouncer.notify(workspace_root, project_path) is True
    assert debouncer.notify(workspace_root, project_path) is True
    assert debouncer.notify(workspace_root, project_path) is True
    await debouncer.drain()

    assert calls == [project_path.resolve()]
    await debouncer.shutdown()


@pytest.mark.asyncio
async def test_disabled_or_nonready_workspace_does_not_schedule_sync(tmp_path: Path) -> None:
    from app.code_intelligence.debounce import ReadySyncDebouncer
    from app.code_intelligence.metadata import CodeIntelligenceMetadata, MetadataStore

    workspace_root = tmp_path / "internal"
    project_path = tmp_path / "project"
    calls = 0

    async def sync_callback(workspace: Path, project: Path) -> None:
        nonlocal calls
        calls += 1

    debouncer = ReadySyncDebouncer(sync_callback, delay_seconds=0)
    assert debouncer.notify(workspace_root, project_path) is False

    MetadataStore(workspace_root).write(
        CodeIntelligenceMetadata(enabled=True, status="indexing")
    )
    assert debouncer.notify(workspace_root, project_path) is False
    await debouncer.drain()

    assert calls == 0
    await debouncer.shutdown()


@pytest.mark.asyncio
async def test_shutdown_cancels_pending_debounce(tmp_path: Path) -> None:
    from app.code_intelligence.debounce import ReadySyncDebouncer
    from app.code_intelligence.metadata import CodeIntelligenceMetadata, MetadataStore

    workspace_root = tmp_path / "internal"
    project_path = tmp_path / "project"
    MetadataStore(workspace_root).write(
        CodeIntelligenceMetadata(enabled=True, status="ready")
    )
    called = asyncio.Event()

    async def sync_callback(workspace: Path, project: Path) -> None:
        called.set()

    debouncer = ReadySyncDebouncer(sync_callback, delay_seconds=60)
    debouncer.notify(workspace_root, project_path)

    await debouncer.shutdown()

    assert called.is_set() is False
    assert debouncer.pending_projects == ()
