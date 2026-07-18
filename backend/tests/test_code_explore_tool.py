import asyncio
from pathlib import Path

from app.tools.base import ToolContext


async def _local_context(api_client, agents, tmp_path: Path, monkeypatch):
    from app.code_intelligence import service as code_service
    from app.code_intelligence.metadata import CodeIntelligenceMetadata, MetadataStore
    from app.services.fs_service import get_workspace_for_conversation

    # Code intelligence auto-enables for local workspaces; mock to avoid
    # RuntimeError when the service isn't initialized in tests.
    monkeypatch.setattr(
        code_service,
        "schedule_workspace_enable",
        lambda **kwargs: None,
    )

    project_path = tmp_path / "project"
    project_path.mkdir()
    created = await api_client.post(
        "/api/conversations",
        json={
            "mode": "single",
            "agentIds": [agents["alice"]],
            "boundPath": str(project_path),
        },
    )
    conversation_id = created.json()["conversation"]["id"]
    workspace = await get_workspace_for_conversation(conversation_id)
    assert workspace is not None
    MetadataStore(Path(workspace.root_path)).write(
        CodeIntelligenceMetadata(enabled=True, status="ready")
    )
    return conversation_id, workspace, project_path


async def test_code_explore_validates_query(
    api_client, agents, tmp_path: Path, monkeypatch
) -> None:
    from app.tools.code_explore import code_explore_tool

    conversation_id, workspace, _ = await _local_context(
        api_client, agents, tmp_path, monkeypatch
    )
    result = await code_explore_tool.handler(
        {"query": "   "},
        ToolContext(
            conversation_id=conversation_id,
            workspace_path=workspace.root_path,
            agent_id=agents["alice"],
            run_id="run_test",
            cancel_event=asyncio.Event(),
        ),
    )
    assert result.ok is False
    assert "Invalid args" in (result.error or "")


async def test_code_explore_derives_project_and_bounds_output(
    api_client,
    agents,
    tmp_path: Path,
    monkeypatch,
) -> None:
    from app.code_intelligence import service as code_service
    from app.tools.code_explore import MAX_OUTPUT_CHARS, code_explore_tool

    conversation_id, workspace, project_path = await _local_context(
        api_client, agents, tmp_path, monkeypatch
    )
    calls: list[dict] = []

    class FakeService:
        async def explore(self, **kwargs):
            calls.append(kwargs)
            return "x" * (MAX_OUTPUT_CHARS + 100)

    monkeypatch.setattr(code_service, "_service", FakeService())
    cancel_event = asyncio.Event()
    result = await code_explore_tool.handler(
        {"query": "trace authentication flow"},
        ToolContext(
            conversation_id=conversation_id,
            workspace_path="C:/attacker-controlled",
            agent_id=agents["alice"],
            run_id="run_test",
            cancel_event=cancel_event,
        ),
    )

    assert result.ok is True
    assert calls[0]["project_path"] == project_path.resolve()
    assert calls[0]["cancel_event"] is cancel_event
    assert len(result.value["context"]) <= MAX_OUTPUT_CHARS + 100
    assert result.value["truncated"] is True


async def test_code_explore_unavailable_is_nonfatal_with_fallback(
    api_client,
    agents,
    tmp_path: Path,
    monkeypatch,
) -> None:
    from app.code_intelligence.metadata import CodeIntelligenceMetadata, MetadataStore
    from app.tools.code_explore import code_explore_tool

    conversation_id, workspace, _ = await _local_context(
        api_client, agents, tmp_path, monkeypatch
    )
    MetadataStore(Path(workspace.root_path)).write(
        CodeIntelligenceMetadata(enabled=True, status="failed", error="index failed")
    )
    result = await code_explore_tool.handler(
        {"query": "impact of changing auth"},
        ToolContext(
            conversation_id=conversation_id,
            workspace_path=workspace.root_path,
            agent_id=agents["alice"],
            run_id="run_test",
            cancel_event=asyncio.Event(),
        ),
    )

    assert result.ok is False
    error = result.error or ""
    # Fallback message includes current status
    assert "failed" in error
    # Fallback message includes alternative tool suggestions
    assert "fs_list" in error
    assert "fs_grep" in error
    assert "fs_read" in error
    assert "outline" in error


async def test_code_explore_fallback_indexing_with_progress(
    api_client,
    agents,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Fallback when graph is indexing includes status + progress + guidance."""
    from app.code_intelligence.metadata import CodeIntelligenceMetadata, MetadataStore
    from app.tools.code_explore import code_explore_tool

    conversation_id, workspace, _ = await _local_context(
        api_client, agents, tmp_path, monkeypatch
    )
    MetadataStore(Path(workspace.root_path)).write(
        CodeIntelligenceMetadata(
            enabled=True,
            status="indexing",
            progress_percent=45,
        )
    )
    result = await code_explore_tool.handler(
        {"query": "project entry point"},
        ToolContext(
            conversation_id=conversation_id,
            workspace_path=workspace.root_path,
            agent_id=agents["alice"],
            run_id="run_test",
            cancel_event=asyncio.Event(),
        ),
    )

    assert result.ok is False
    error = result.error or ""
    # Status and progress included
    assert "indexing" in error
    assert "45" in error
    # Tool suggestions included
    assert "fs_list" in error
    assert "depth=3" in error
    assert "fs_grep" in error
    assert "fs_read" in error
    assert "outline" in error
    # Note about future availability
    assert "code_explore" in error
