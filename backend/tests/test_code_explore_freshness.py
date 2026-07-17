import asyncio
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_explore_syncs_stale_ready_index_before_query(tmp_path: Path) -> None:
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

    result = await service.explore(
        workspace_root=workspace_root,
        project_path=project_path,
        query="auth flow",
        cancel_event=asyncio.Event(),
    )

    assert result == "bounded context"
    assert calls == ["status", "sync", "explore"]
    await service.shutdown()
