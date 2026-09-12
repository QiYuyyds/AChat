"""Tests for the shared MCP bridge launch config (mcp_bridge_config).

Locks down the invocation content (args order, env keys, server name) and
asserts both adapter serializations (claude JSON / codex TOML overrides)
carry the identical invocation.
"""

import json

from app.adapters.base import AdapterInput
from app.adapters.claude_adapter import _write_mcp_config
from app.adapters.codex_adapter import _build_achat_mcp_overrides
from app.adapters.mcp_bridge_config import (
    BRIDGE_SERVER_NAME,
    BridgeInvocation,
    build_bridge_invocation,
)


def _make_input(**overrides) -> AdapterInput:
    defaults: dict = dict(
        agent_id="ag_test",
        conversation_id="conv_test",
        run_id="run_test",
        prompt="hello",
        workspace_path="/tmp/ws",
        system_prompt="sys",
        api_key=None,
        api_base_url=None,
        model_id=None,
        tool_names=[],
    )
    defaults.update(overrides)
    return AdapterInput(**defaults)


def test_args_order_and_content():
    invocation = build_bridge_invocation(_make_input())
    assert invocation.args == [
        "-m", "app.mcp_bridge",
        "--conversation-id", "conv_test",
        "--run-id", "run_test",
        "--workspace-path", "/tmp/ws",
        "--agent-id", "ag_test",
    ]
    assert invocation.server_name == BRIDGE_SERVER_NAME == "achat-tools"
    assert invocation.command  # sys.executable — non-empty


def test_user_id_appended_conditionally():
    without = build_bridge_invocation(_make_input())
    assert "--user-id" not in without.args

    with_user = build_bridge_invocation(_make_input(user_id="u_1"))
    assert with_user.args[-2:] == ["--user-id", "u_1"]


def test_tool_names_appended_conditionally():
    with_tools = build_bridge_invocation(
        _make_input(), tool_names=["write_artifact", "read_artifact"]
    )
    assert with_tools.args[-2:] == ["--tool-names", "write_artifact,read_artifact"]

    # None / empty → no --tool-names
    assert "--tool-names" not in build_bridge_invocation(_make_input()).args
    assert "--tool-names" not in build_bridge_invocation(
        _make_input(), tool_names=[]
    ).args


def test_workspace_path_empty_falls_back_to_empty_string():
    invocation = build_bridge_invocation(_make_input(workspace_path=""))
    assert "--workspace-path" in invocation.args
    assert invocation.args[
        invocation.args.index("--workspace-path") + 1
    ] == ""


def test_env_keys(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///bridge-test.db")
    monkeypatch.delenv("DATABASE_LOCAL_URL", raising=False)

    invocation = build_bridge_invocation(_make_input())
    assert set(invocation.env) == {"PYTHONPATH", "PYTHONUNBUFFERED", "DATABASE_URL"}
    assert invocation.env["PYTHONUNBUFFERED"] == "1"
    assert invocation.env["DATABASE_URL"] == "sqlite:///bridge-test.db"
    assert invocation.env["PYTHONPATH"].endswith("backend")


def test_env_database_local_url_optional(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///bridge-test.db")
    monkeypatch.setenv("DATABASE_LOCAL_URL", "sqlite:///bridge-local.db")

    invocation = build_bridge_invocation(_make_input())
    assert set(invocation.env) == {
        "PYTHONPATH", "PYTHONUNBUFFERED", "DATABASE_URL", "DATABASE_LOCAL_URL",
    }
    assert invocation.env["DATABASE_LOCAL_URL"] == "sqlite:///bridge-local.db"


def test_dataclass_defaults():
    invocation = BridgeInvocation(command="python", args=["-m", "app.mcp_bridge"])
    assert invocation.env == {}
    assert invocation.server_name == "achat-tools"


def test_both_adapter_serializations_carry_same_invocation(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///bridge-test.db")
    monkeypatch.delenv("DATABASE_LOCAL_URL", raising=False)

    adapter_input = _make_input(workspace_path=str(tmp_path))

    # Codex: parse `-c mcp_servers.achat-tools.*=<json>` override lines
    # (flat list alternating between the `-c` flag and each `key=value`)
    overrides = _build_achat_mcp_overrides(adapter_input)
    parsed: dict = {}
    for key_value in overrides[1::2]:
        key, value = key_value.split("=", 1)
        parsed[key.removeprefix("mcp_servers.achat-tools.")] = json.loads(value)
    codex_command = parsed["command"]
    codex_args = parsed["args"]
    codex_env = {
        key.removeprefix("env."): value
        for key, value in parsed.items()
        if key.startswith("env.")
    }

    # Claude: write config JSON, read back the achat-tools server entry
    invocation = build_bridge_invocation(adapter_input)
    config_path = _write_mcp_config(invocation)
    assert config_path is not None
    with open(config_path, encoding="utf-8") as fh:
        claude_entry = json.load(fh)["mcpServers"]["achat-tools"]

    # Same command / args / env / server name across both serializations
    assert claude_entry["command"] == codex_command
    assert claude_entry["args"] == codex_args == invocation.args
    assert claude_entry["env"] == codex_env == invocation.env
    assert claude_entry["type"] == "stdio"
