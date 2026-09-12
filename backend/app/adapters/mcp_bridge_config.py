"""Shared AChat MCP Bridge launch configuration.

Single source for how the adapters spawn the AChat MCP Bridge stdio server
(``python -m app.mcp_bridge …``) — previously duplicated between
``claude_adapter._write_mcp_config`` (Claude JSON) and
``codex_adapter._build_achat_mcp_overrides`` (Codex TOML overrides).

Each adapter formats :class:`BridgeInvocation` into its own config shape;
the invocation content (command / args / env / server name) lives only here.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from app.adapters.base import AdapterInput

BRIDGE_SERVER_NAME = "achat-tools"


@dataclass
class BridgeInvocation:
    """How to spawn the AChat MCP Bridge as a stdio MCP server."""

    command: str
    args: list[str]
    env: dict[str, str] = field(default_factory=dict)
    server_name: str = BRIDGE_SERVER_NAME


def _backend_root() -> str:
    """``.../backend`` so the spawned server can ``import app.mcp_bridge``.

    This module lives at ``backend/app/adapters/mcp_bridge_config.py``.
    """
    return str(Path(__file__).resolve().parents[2])


def build_bridge_invocation(
    input: AdapterInput,
    tool_names: list[str] | None = None,
) -> BridgeInvocation:
    """Build the bridge launch invocation shared by all CLI adapters.

    ``tool_names`` restricts the exposed platform tools (Claude adapter only;
    other adapters omit it). Env mirrors the parent process's database config.
    """
    args = [
        "-m", "app.mcp_bridge",
        "--conversation-id", input.conversation_id,
        "--run-id", input.run_id,
        "--workspace-path", input.workspace_path or "",
        "--agent-id", input.agent_id,
    ]
    if input.user_id:
        args.extend(["--user-id", input.user_id])
    if tool_names:
        args.extend(["--tool-names", ",".join(tool_names)])

    env = {
        "PYTHONPATH": _backend_root(),
        "PYTHONUNBUFFERED": "1",
        "DATABASE_URL": os.environ.get("DATABASE_URL", ""),
    }
    if os.environ.get("DATABASE_LOCAL_URL"):
        env["DATABASE_LOCAL_URL"] = os.environ["DATABASE_LOCAL_URL"]

    return BridgeInvocation(command=sys.executable, args=args, env=env)
