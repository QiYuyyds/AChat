"""AChat MCP Bridge — expose AChat tools to CLI agents via MCP stdio protocol.

Claude Code CLI agents bring their own tool suite (bash, fs_read, fs_write,
grep, glob, ...) but lack AChat platform tools like ``write_artifact`` /
``read_artifact`` (AChat artifact system) and ``ask_user`` (user interaction).

This module implements a **stdio-based MCP server** that Claude CLI spawns
as a child process (configured via ``--mcp-config``).  The server translates
MCP ``tools/list`` and ``tools/call`` JSON-RPC 2.0 requests into
:class:`ToolRegistry` calls and returns the results to Claude CLI.

**Synchronous I/O**: Uses blocking stdin/stdout (not asyncio) because
Windows ``ProactorEventLoop`` has known issues with pipe I/O.  Async
tool handlers are run in a single module-level asyncio event loop reused
across all calls (see ``_get_mcp_event_loop``).

Usage (spawned by Claude CLI, not run directly)::

    python -m app.mcp_bridge \\
        --conversation-id CONV123 \\
        --run-id RUN456 \\
        --workspace-path /path/to/workspace \\
        --agent-id AGENT789

Protocol: MCP 2024-11-05 (JSON-RPC 2.0 over stdin/stdout).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from typing import Any

from app.config import apply_env_overrides
from app.db.engine import init_db
from app.tools.base import ToolContext, ToolDef, ToolResult
from app.tools.registry import tool_registry

# ─── Tools exposed to CLI agents ──────────────────────────────────

CLI_MCP_TOOL_NAMES = frozenset({
    "write_artifact",       # create AChat artifacts
    "read_artifact",        # read existing artifacts
    "ask_user",             # user interaction
    "deploy_artifact",      # deploy single artifact
    "deploy_workspace",     # deploy workspace
    "code_explore",        # managed source intelligence
})

# ─── MCP Protocol constants ───────────────────────────────────────

MCP_PROTOCOL_VERSION = "2024-11-05"
JSON_RPC_VERSION = "2.0"
STDIN = sys.stdin.buffer
STDOUT = sys.stdout.buffer
STDERR = sys.stderr.buffer


def _log(msg: str) -> None:
    """Write to stderr so Claude CLI can surface errors in its logs."""
    try:
        STDERR.write(f"[achat-mcp] {msg}\n".encode())
        STDERR.flush()
    except Exception:
        pass


def _write(response: dict) -> None:
    """Write a JSON-RPC response to stdout."""
    data = json.dumps(response, ensure_ascii=False) + "\n"
    STDOUT.write(data.encode("utf-8"))
    STDOUT.flush()


def _mcp_error(code: int, message: str, id: Any = None) -> dict:
    return {"jsonrpc": JSON_RPC_VERSION, "id": id, "error": {"code": code, "message": message}}


def _mcp_result(result: Any, id: Any) -> dict:
    return {"jsonrpc": JSON_RPC_VERSION, "id": id, "result": result}


def _tool_to_mcp(tool: ToolDef) -> dict:
    """Convert an AChat ToolDef to an MCP tool schema."""
    return {
        "name": tool.name,
        "description": tool.description,
        "inputSchema": {
            "type": "object",
            "properties": tool.parameters.get("properties", {}),
            "required": tool.parameters.get("required", []),
        },
    }


# ─── Sync dispatcher ──────────────────────────────────────────────

class McpBridge:
    """Synchronous MCP stdio server."""

    def __init__(self, ctx: ToolContext, tools: dict[str, ToolDef]) -> None:
        self._ctx = ctx
        self._tools = tools
        self._initialized = False

    # ── main loop ────────────────────────────────────────────────

    def run(self) -> None:
        _log("starting MCP bridge")
        while True:
            line = STDIN.readline()
            if not line:
                _log("stdin closed, exiting")
                break
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                request = json.loads(text)
            except json.JSONDecodeError:
                _log(f"skipped non-JSON line: {text[:120]}")
                continue
            response = self._dispatch(request)
            if response is not None:
                _write(response)

    # ── dispatch ─────────────────────────────────────────────────

    def _dispatch(self, request: dict) -> dict | None:
        method = request.get("method", "")
        req_id = request.get("id")
        params = request.get("params", {})

        # Special case: if the client sends an empty "method" after init,
        # treat it as a ping — return nothing.
        if not method and not req_id:
            return None

        if method == "initialize":
            return self._handle_initialize(req_id)
        if method == "notifications/initialized":
            self._initialized = True
            _log("initialized")
            return None
        if not self._initialized:
            return _mcp_error(-32002, "Not initialized", req_id)
        if method == "tools/list":
            return self._handle_tools_list(req_id)
        if method == "tools/call":
            return self._handle_tools_call(params, req_id)
        if method == "shutdown":
            return _mcp_result(None, req_id)
        # Some clients send ping
        if method == "ping":
            return _mcp_result({}, req_id)
        return _mcp_error(-32601, f"Method not found: {method}", req_id)

    # ── handlers ─────────────────────────────────────────────────

    def _handle_initialize(self, req_id: Any) -> dict:
        self._initialized = True
        _log(f"initialize (protocol={MCP_PROTOCOL_VERSION})")
        return _mcp_result({
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "achat-mcp-bridge", "version": "0.1.0"},
        }, req_id)

    def _handle_tools_list(self, req_id: Any) -> dict:
        tools = [_tool_to_mcp(t) for t in self._tools.values()]
        _log(f"tools/list → {len(tools)} tools: {[t['name'] for t in tools]}")
        return _mcp_result({"tools": tools}, req_id)

    def _handle_tools_call(self, params: dict, req_id: Any) -> dict:
        tool_name = params.get("name", "")
        tool = self._tools.get(tool_name)
        if tool is None:
            _log(f"tools/call UNKNOWN: {tool_name}")
            return _mcp_error(-32602, f"Unknown tool: {tool_name}", req_id)

        raw_args = params.get("arguments", {})
        if not isinstance(raw_args, dict):
            raw_args = {}

        _log(f"tools/call {tool_name} args={json.dumps(raw_args, ensure_ascii=False)[:200]}")

        try:
            result = _execute_tool(tool, raw_args, self._ctx)
        except Exception as exc:
            _log(f"tools/call {tool_name} ERROR: {exc}")
            return _mcp_result({
                "content": [{"type": "text", "text": f"Tool error: {exc}"}],
                "isError": True,
            }, req_id)

        if result.ok:
            text = (
                json.dumps(result.value, ensure_ascii=False)
                if not isinstance(result.value, str)
                else result.value
            )
            _log(f"tools/call {tool_name} OK: {text[:120]}")
            return _mcp_result({"content": [{"type": "text", "text": text}]}, req_id)
        else:
            _log(f"tools/call {tool_name} FAIL: {result.error}")
            return _mcp_result({
                "content": [{"type": "text", "text": result.error or "Unknown error"}],
                "isError": True,
            }, req_id)


_mcp_event_loop: asyncio.AbstractEventLoop | None = None


def _get_mcp_event_loop() -> asyncio.AbstractEventLoop:
    """Return a single module-level event loop, creating it on first use.

    Reusing one loop avoids per-call creation/teardown overhead and prevents
    cross-loop reference bugs when async tool handlers hold asyncio primitives
    (Event, Lock, etc.) created on a prior, now-closed loop.
    """
    global _mcp_event_loop
    if _mcp_event_loop is None or _mcp_event_loop.is_closed():
        _mcp_event_loop = asyncio.new_event_loop()
    return _mcp_event_loop


def _execute_tool(tool: ToolDef, args: dict, ctx: ToolContext) -> ToolResult:
    """Execute a tool handler — handles both sync and async handlers."""
    result = tool.handler(args, ctx)
    if asyncio.iscoroutine(result):
        loop = _get_mcp_event_loop()
        result = loop.run_until_complete(result)
    return result


# ─── CLI entry point ──────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="AChat MCP Bridge")
    p.add_argument("--conversation-id", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--workspace-path", required=True)
    p.add_argument("--agent-id", required=True)
    p.add_argument("--user-id", default=None)
    p.add_argument("--tool-names", default="",
                   help="Comma-separated tool names to expose (empty = default set)")
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    # Write a startup marker so we can verify the bridge was actually
    # spawned by Claude CLI (useful for debugging MCP connectivity).
    marker_path = os.path.join(
        tempfile.gettempdir(),
        f"achat_mcp_startup_{args.run_id}.txt",
    )
    try:
        with open(marker_path, "w") as f:
            f.write(f"pid={os.getpid()}\n")
            f.write(f"conversation_id={args.conversation_id}\n")
            f.write(f"run_id={args.run_id}\n")
            f.write(f"started_at={time.time()}\n")
            f.write(f"python={sys.executable}\n")
            f.write(f"cwd={os.getcwd()}\n")
            f.write(f"pythonpath={os.environ.get('PYTHONPATH', 'NOT SET')}\n")
            f.write(f"database_url={'SET' if os.environ.get('DATABASE_URL') else 'NOT SET'}\n")
    except Exception:
        pass

    # Determine which tools to expose: if --tool-names is provided,
    # filter to that set; otherwise fall back to the default CLI_MCP_TOOL_NAMES.
    if args.tool_names:
        requested = {n.strip() for n in args.tool_names.split(",") if n.strip()}
        tool_name_set = requested
    else:
        tool_name_set = CLI_MCP_TOOL_NAMES

    tools = {
        name: tool_registry.get(name)
        for name in tool_name_set
        if tool_registry.get(name) is not None
    }
    if not tools:
        _log("FATAL: No CLI MCP tools registered")
        sys.exit(1)

    _log(f"exposing {len(tools)} tools: {sorted(tools.keys())}")
    _log(f"startup marker: {marker_path}")

    # Initialize database engines so tool handlers can use get_db().
    # The MCP bridge is a separate process — it doesn't go through the
    # FastAPI lifespan that normally calls init_db().
    apply_env_overrides()
    try:
        asyncio.run(init_db())
        _log("database initialized")
    except Exception as exc:
        _log(f"FATAL: init_db failed: {exc}")
        sys.exit(1)

    ctx = ToolContext(
        conversation_id=args.conversation_id,
        workspace_path=args.workspace_path,
        agent_id=args.agent_id,
        run_id=args.run_id,
        cancel_event=asyncio.Event(),
        user_id=args.user_id,
    )

    bridge = McpBridge(ctx, tools)
    bridge.run()


if __name__ == "__main__":
    main()
