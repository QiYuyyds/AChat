from pathlib import Path
from types import SimpleNamespace


def test_code_explore_is_registered() -> None:
    from app.tools.registry import tool_registry

    tool = tool_registry.get("code_explore")
    assert tool is not None
    assert tool.parameters["required"] == ["query"]


def test_ready_custom_run_gets_code_explore_without_changing_presets(tmp_path: Path) -> None:
    from app.code_intelligence.metadata import CodeIntelligenceMetadata, MetadataStore
    from app.services.agent_runner import _inject_code_intelligence_tool

    workspace = SimpleNamespace(
        mode="local",
        bound_path=str(tmp_path / "project"),
        root_path=str(tmp_path / "internal"),
    )
    agent = SimpleNamespace(adapter_name="custom")
    original = ["fs_read"]
    MetadataStore(Path(workspace.root_path)).write(
        CodeIntelligenceMetadata(enabled=True, status="ready")
    )

    injected = _inject_code_intelligence_tool(original, agent, workspace)

    assert injected == ["fs_read", "code_explore"]
    assert original == ["fs_read"]


def test_nonready_or_cli_run_does_not_get_code_explore(tmp_path: Path) -> None:
    from app.code_intelligence.metadata import CodeIntelligenceMetadata, MetadataStore
    from app.services.agent_runner import _inject_code_intelligence_tool

    workspace = SimpleNamespace(
        mode="local",
        bound_path=str(tmp_path / "project"),
        root_path=str(tmp_path / "internal"),
    )
    MetadataStore(Path(workspace.root_path)).write(
        CodeIntelligenceMetadata(enabled=True, status="indexing")
    )

    assert _inject_code_intelligence_tool(
        ["fs_read"], SimpleNamespace(adapter_name="custom"), workspace
    ) == ["fs_read"]
    MetadataStore(Path(workspace.root_path)).write(
        CodeIntelligenceMetadata(enabled=True, status="ready")
    )
    assert _inject_code_intelligence_tool(
        ["fs_read"], SimpleNamespace(adapter_name="claude-code"), workspace
    ) == ["fs_read"]
