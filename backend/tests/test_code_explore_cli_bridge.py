from app.adapters.base import AdapterInput


def _input() -> AdapterInput:
    return AdapterInput(
        agent_id="ag_test",
        conversation_id="conv_test",
        run_id="run_test",
        prompt="hello",
        workspace_path="C:/project",
        system_prompt="system",
        api_key=None,
        api_base_url=None,
        model_id=None,
        tool_names=[],
    )


def test_cli_bridge_exposes_registered_code_explore_tool() -> None:
    from app.mcp_bridge import CLI_MCP_TOOL_NAMES
    from app.tools.registry import tool_registry

    assert "code_explore" in CLI_MCP_TOOL_NAMES
    assert tool_registry.get("code_explore") is not None


def test_claude_and_codex_hints_use_exact_prefixed_name() -> None:
    from app.adapters.claude_adapter import ACHAT_MCP_TOOL_HINT as claude_hint
    from app.adapters.codex_adapter import ACHAT_MCP_TOOL_HINT as codex_hint

    expected = "`code_explore` → `mcp__achat-tools__code_explore`"
    assert expected in claude_hint
    assert expected in codex_hint
    assert "structure, call paths, and impact" in claude_hint
    assert "file search/read" in codex_hint


def test_codex_launch_configures_existing_achat_mcp_bridge() -> None:
    from app.adapters.codex_adapter import _build_achat_mcp_overrides

    overrides = _build_achat_mcp_overrides(_input())
    joined = "\n".join(overrides)

    assert "mcp_servers.achat-tools.command" in joined
    assert "app.mcp_bridge" in joined
    assert "conv_test" in joined
    assert "run_test" in joined
