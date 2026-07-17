import asyncio
from pathlib import Path

import pytest


class _UnusedRuntimeManager:
    def resolve(self, platform_key=None, *, download_approved: bool):
        raise AssertionError("runtime resolution is not expected for sync/rebuild/disable")


@pytest.mark.asyncio
async def test_sync_and_rebuild_use_valid_lifecycle_transitions(tmp_path: Path) -> None:
    from app.code_intelligence.index_manager import CodeIntelligenceManager
    from app.code_intelligence.metadata import CodeIntelligenceMetadata, MetadataStore
    from app.code_intelligence.service import CodeIntelligenceService

    operations: list[str] = []

    async def runner(
        project_path: Path,
        operation: str,
        cancel_event: asyncio.Event,
        progress_callback=None,
    ):
        operations.append(operation)
        return {"files": 9, "symbols": 80, "relationships": 25}

    workspace_root = tmp_path / "internal"
    project_path = tmp_path / "project"
    store = MetadataStore(workspace_root)
    store.write(CodeIntelligenceMetadata(enabled=True, status="ready"))
    service = CodeIntelligenceService(
        runtime_manager=_UnusedRuntimeManager(),
        index_manager=CodeIntelligenceManager(runner=runner),
    )

    await service.sync(workspace_root=workspace_root, project_path=project_path)
    assert store.read().status == "ready"
    assert store.read().counts.symbols == 80
    await service.rebuild(workspace_root=workspace_root, project_path=project_path)

    assert operations == ["sync", "rebuild"]
    assert store.read().status == "ready"
    await service.shutdown()


@pytest.mark.asyncio
async def test_cancel_keeps_enabled_intent_and_disable_preserves_index(tmp_path: Path) -> None:
    from app.code_intelligence.index_manager import CodeIntelligenceManager
    from app.code_intelligence.metadata import CodeIntelligenceMetadata, MetadataStore
    from app.code_intelligence.service import CodeIntelligenceService

    started = asyncio.Event()

    async def runner(
        project_path: Path,
        operation: str,
        cancel_event: asyncio.Event,
        progress_callback=None,
    ):
        started.set()
        await cancel_event.wait()
        raise asyncio.CancelledError

    workspace_root = tmp_path / "internal"
    project_path = tmp_path / "project"
    index_file = project_path / ".codegraph" / "graph.db"
    index_file.parent.mkdir(parents=True)
    index_file.write_bytes(b"index")
    store = MetadataStore(workspace_root)
    store.write(CodeIntelligenceMetadata(enabled=True, status="ready"))
    service = CodeIntelligenceService(
        runtime_manager=_UnusedRuntimeManager(),
        index_manager=CodeIntelligenceManager(runner=runner),
    )

    syncing = service.schedule_operation(
        workspace_root=workspace_root,
        project_path=project_path,
        operation="sync",
    )
    await started.wait()
    assert await service.cancel(workspace_root=workspace_root, project_path=project_path)
    await syncing

    assert store.read().status == "interrupted"
    assert store.read().enabled is True
    await service.disable(workspace_root=workspace_root, project_path=project_path)
    assert store.read().status == "disabled"
    assert index_file.read_bytes() == b"index"
    await service.shutdown()


@pytest.mark.asyncio
async def test_retry_restarts_failed_workspace(tmp_path: Path) -> None:
    from app.code_intelligence.index_manager import CodeIntelligenceManager
    from app.code_intelligence.metadata import CodeIntelligenceMetadata, MetadataStore
    from app.code_intelligence.runtime import ResolvedRuntime, RuntimeArtifact
    from app.code_intelligence.service import CodeIntelligenceService

    artifact = RuntimeArtifact(
        platform_key="win32-x64",
        version="0.9.3",
        url="https://example.test/codegraph.zip",
        sha256="a" * 64,
        archive_type="zip",
    )

    class RuntimeManager:
        def resolve(self, platform_key=None, *, download_approved: bool):
            return ResolvedRuntime("packaged", tmp_path / "runtime", artifact)

    async def runner(
        project_path: Path,
        operation: str,
        cancel_event: asyncio.Event,
        progress_callback=None,
    ):
        return {"files": 1, "symbols": 2, "relationships": 3}

    workspace_root = tmp_path / "internal"
    project_path = tmp_path / "project"
    store = MetadataStore(workspace_root)
    store.write(CodeIntelligenceMetadata(enabled=True, status="failed", error="boom"))
    service = CodeIntelligenceService(
        runtime_manager=RuntimeManager(),
        index_manager=CodeIntelligenceManager(runner=runner),
    )

    await service.retry(workspace_root=workspace_root, project_path=project_path)

    assert store.read().status == "ready"
    assert store.read().error is None
    await service.shutdown()
