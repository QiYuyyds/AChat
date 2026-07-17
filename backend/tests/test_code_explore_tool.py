import asyncio
from pathlib import Path

from app.tools.base import ToolContext


async def _local_context(api_client, agents, tmp_path: Path):
    from app.code_intelligence.metadata import CodeIntelligenceMetadata, MetadataStore
    from app.services.fs_service import get_workspace_for_conversation

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


async def test_code_explore_validates_query(api_client, agents, tmp_path: Path) -> None:
    from app.tools.code_explore import code_explore_tool

    conversation_id, workspace, _ = await _local_context(api_client, agents, tmp_path)
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
        api_client, agents, tmp_path
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
) -> None:
    from app.code_intelligence.metadata import CodeIntelligenceMetadata, MetadataStore
    from app.tools.code_explore import code_explore_tool

    conversation_id, workspace, _ = await _local_context(api_client, agents, tmp_path)
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
    assert "file search/read tools" in (result.error or "")
