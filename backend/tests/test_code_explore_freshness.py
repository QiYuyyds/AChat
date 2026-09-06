import asyncio
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_explore_schedules_sync_for_stale_ready_index(tmp_path: Path) -> None:
    """A stale ready index triggers a background sync; explore still answers.

    判定注记（A 类）：explore 不再内联等待 sync 完成——sync 改为后台任务
    （schedule_operation 用 asyncio.create_task，优化首字延迟），explore 直接
    返回查询结果。旧断言要求 'status, sync, explore' 严格串行已过时。
    """
    from app.code_intelligence.index_manager import CodeIntelligenceManager
    from app.code_intelligence.metadata import CodeIntelligenceMetadata, MetadataStore
    from app.code_intelligence.service import CodeIntelligenceService

    calls: list[str] = []

    async def index_runner(project_path: Path, operation: str, cancel_event: asyncio.Event):
        calls.append(operation)
        return {"files": 1, "symbols": 2, "relationships": 3}

    class CommandRunner:
        async def is_stale(self, project_path: Path, cancel_event: asyncio.Event):
            calls.append("status")
            return True

        async def explore(self, project_path: Path, query: str, cancel_event: asyncio.Event):
            calls.append("explore")
            return "bounded context"

    workspace_root = tmp_path / "internal"
    project_path = tmp_path / "project"
    MetadataStore(workspace_root).write(
        CodeIntelligenceMetadata(enabled=True, status="ready")
    )
    service = CodeIntelligenceService(
        runtime_manager=object(),
        index_manager=CodeIntelligenceManager(runner=index_runner),
        command_runner=CommandRunner(),
    )

    # Observe the scheduled background sync without depending on task timing.
    sync_scheduled: list[Path] = []

    async def fake_sync(*, workspace_root, project_path):
        sync_scheduled.append(Path(project_path))

    from unittest.mock import patch

    with patch.object(service, "sync", fake_sync):
        result = await service.explore(
            workspace_root=workspace_root,
            project_path=project_path,
            query="auth flow",
            cancel_event=asyncio.Event(),
        )

    assert result == "bounded context"
    # Stale check ran before the query, sync was scheduled, and explore
    # answered without waiting for the sync to finish.
    assert calls == ["status", "explore"]
    assert sync_scheduled == [project_path.resolve()]
    await service.shutdown()
