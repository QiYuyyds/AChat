import asyncio
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_background_enable_prepares_runtime_and_runs_init(tmp_path: Path) -> None:
    from app.code_intelligence.index_manager import CodeIntelligenceManager
    from app.code_intelligence.metadata import MetadataStore
    from app.code_intelligence.runtime import ResolvedRuntime, RuntimeArtifact
    from app.code_intelligence.service import CodeIntelligenceService

    started = asyncio.Event()
    progress_reported = asyncio.Event()
    release = asyncio.Event()
    operations: list[tuple[Path, str]] = []

    async def runner(
        project_path: Path,
        operation: str,
        cancel_event: asyncio.Event,
        progress_callback=None,
    ):
        from app.code_intelligence.progress import CodeGraphProgress

        operations.append((project_path, operation))
        started.set()
        assert progress_callback is not None
        progress_callback(CodeGraphProgress("parsing", 34, 34))
        progress_reported.set()
        await release.wait()
        return {"files": 5, "symbols": 40, "relationships": 12}

    artifact = RuntimeArtifact(
        platform_key="win32-x64",
        version="0.9.3",
        url="https://example.test/codegraph.zip",
        sha256="a" * 64,
        archive_type="zip",
    )

    class FakeRuntimeManager:
        def resolve(self, platform_key=None, *, download_approved: bool):
            return ResolvedRuntime("packaged", tmp_path / "runtime", artifact)

    service = CodeIntelligenceService(
        runtime_manager=FakeRuntimeManager(),
        index_manager=CodeIntelligenceManager(runner=runner),
    )
    workspace_root = tmp_path / "internal"
    project_path = tmp_path / "project"

    background = service.schedule_enable(
        workspace_root=workspace_root,
        project_path=project_path,
        download_approved=True,
    )

    assert background.done() is False
    await started.wait()
    await asyncio.wait_for(progress_reported.wait(), timeout=0.5)
    await asyncio.sleep(0)
    assert MetadataStore(workspace_root).read().status == "indexing"
    assert MetadataStore(workspace_root).read().progress_percent == 34
    release.set()
    await background

    metadata = MetadataStore(workspace_root).read()
    assert metadata.status == "ready"
    assert metadata.progress_percent is None
    assert metadata.counts.files == 5
    assert operations == [(project_path.resolve(), "init")]
    await service.shutdown()


@pytest.mark.asyncio
async def test_local_conversation_schedules_index_after_creation(
    db,
    agents,
    test_user,
    tmp_path: Path,
    monkeypatch,
) -> None:
    from app.code_intelligence import service as code_service
    from app.services.conversation_service import create_conversation

    project_path = tmp_path / "local-project"
    project_path.mkdir()
    scheduled: list[dict] = []

    def fake_schedule(**kwargs):
        scheduled.append(kwargs)

    monkeypatch.setattr(code_service, "schedule_workspace_enable", fake_schedule)

    conversation = await create_conversation(
        mode="single",
        agent_ids=[agents["alice"]],
        bound_path=str(project_path),
        code_intelligence_enabled=True,
        user_id=test_user["id"],
    )

    assert conversation.workspace_mode == "local"
    assert scheduled[0]["project_path"] == project_path.resolve()
    assert Path(scheduled[0]["workspace_root"]).name == conversation.id
    assert scheduled[0]["download_approved"] is True
