"""MCP client manager — per-run lifecycle for external MCP server connections.

Manages connect → listTools → callTool → close for all MCP servers enabled
on a Custom agent. Connections are established at run start and torn down
in the finally block when the run ends or aborts.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Tool name namespace prefix — must match the routing logic in _run_react_loop.
MCP_TOOL_PREFIX = "mcp__"

# Timeout for a single MCP tool call. The MCP library can silently swallow
# transport-level exceptions (e.g. RemoteProtocolError when the server closes
# the connection prematurely), leaving call_tool() hanging indefinitely.
# This timeout ensures we surface the error to the LLM instead of deadlocking.
MCP_CALL_TIMEOUT_SECONDS = 120

# ${ENV_NAME} placeholder pattern for env / headers values.
_ENV_PLACEHOLDER_RE = re.compile(r"\$\{([^}]+)\}")


def _expand_env_placeholders(value: Any) -> Any:
    """Replace ${ENV_NAME} placeholders in strings with os.environ values."""
    if isinstance(value, str):
        return _ENV_PLACEHOLDER_RE.sub(
            lambda m: os.environ.get(m.group(1), m.group(0)), value
        )
    if isinstance(value, dict):
        return {k: _expand_env_placeholders(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env_placeholders(item) for item in value]
    return value


@dataclass
class McpServerConfig:
    """Configuration for a single MCP server connection."""

    id: str
    name: str
    transport: str  # 'stdio' | 'sse' | 'streamable_http'
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    trust: str = "ask"  # 'always' | 'ask'


@dataclass
class _ConnectedServer:
    """Internal state for a connected MCP server."""

    config: McpServerConfig
    session: Any  # mcp.ClientSession
    available: bool = True


class McpClientManager:
    """Manages MCP client connections for a single run.

    Lifecycle:
    1. ``connect_all(configs)`` — establish connections + initialize sessions
    2. ``list_tools_as_api()`` — discover tools, return OpenAI function-calling format
    3. ``call_tool(full_name, args)`` — route and execute a tool call
    4. ``close_all()`` — tear down all connections + kill stdio subprocesses
    """

    def __init__(self) -> None:
        self._servers: dict[str, _ConnectedServer] = {}
        self._exit_stack: AsyncExitStack = AsyncExitStack()

    async def connect_all(self, configs: list[McpServerConfig]) -> None:
        """Connect to all configured MCP servers.

        Each server is connected independently; failures are isolated —
        a failed server is marked unavailable but does not crash the run.
        """
        for config in configs:
            try:
                await self._connect_one(config)
            except Exception as err:  # noqa: BLE001 - isolate failures
                logger.warning(
                    "[McpClientManager] Failed to connect to server '%s': %s",
                    config.name,
                    err,
                )
                self._servers[config.name] = _ConnectedServer(
                    config=config, session=None, available=False
                )

    async def _connect_one(self, config: McpServerConfig) -> None:
        """Connect to a single MCP server."""
        from mcp import ClientSession

        expanded_env = _expand_env_placeholders(config.env)
        expanded_headers = _expand_env_placeholders(config.headers)

        if config.transport == "stdio":
            from mcp.client.stdio import StdioServerParameters, stdio_client

            if not config.command:
                raise ValueError(
                    f"MCP server '{config.name}': stdio transport requires 'command'"
                )

            # On Windows, hide the subprocess window to avoid popping consoles.
            env_dict: dict[str, str] = dict(os.environ)
            if isinstance(expanded_env, dict):
                env_dict.update(expanded_env)

            server_params = StdioServerParameters(
                command=config.command,
                args=config.args,
                env=env_dict,
            )
            read, write = await self._exit_stack.enter_async_context(
                stdio_client(server_params)
            )
        elif config.transport == "sse":
            from mcp.client.sse import sse_client

            if not config.url:
                raise ValueError(
                    f"MCP server '{config.name}': sse transport requires 'url'"
                )

            read, write = await self._exit_stack.enter_async_context(
                sse_client(config.url, headers=expanded_headers)
            )
        elif config.transport == "streamable_http":
            from mcp.client.streamable_http import streamable_http_client
            from mcp.shared._httpx_utils import create_mcp_http_client

            if not config.url:
                raise ValueError(
                    f"MCP server '{config.name}': streamable_http transport requires 'url'"
                )

            http_client = await self._exit_stack.enter_async_context(
                create_mcp_http_client(headers=expanded_headers)
            )
            streams = await self._exit_stack.enter_async_context(
                streamable_http_client(config.url, http_client=http_client)
            )
            read, write = streams[0], streams[1]
        else:
            raise ValueError(
                f"MCP server '{config.name}': unknown transport '{config.transport}'"
            )

        session = await self._exit_stack.enter_async_context(
            ClientSession(read, write)
        )
        await session.initialize()

        self._servers[config.name] = _ConnectedServer(
            config=config, session=session, available=True
        )
        logger.info(
            "[McpClientManager] Connected to MCP server '%s' (%s)",
            config.name,
            config.transport,
        )

    async def list_tools_as_api(self) -> list[dict]:
        """Discover tools from all connected servers.

        Returns a list of OpenAI function-calling tool declarations:
        ``{"type": "function", "function": {"name": "mcp__<server>__<tool>", ...}}``
        """
        api_tools: list[dict] = []
        for server_name, server in self._servers.items():
            if not server.available or server.session is None:
                continue
            try:
                result = await server.session.list_tools()
                for tool in result.tools:
                    full_name = f"{MCP_TOOL_PREFIX}{server_name}__{tool.name}"
                    api_tools.append({
                        "type": "function",
                        "function": {
                            "name": full_name,
                            "description": tool.description or "",
                            "parameters": tool.inputSchema
                            if tool.inputSchema
                            else {"type": "object", "properties": {}},
                        },
                    })
            except Exception as err:  # noqa: BLE001 - degrade per-server
                logger.warning(
                    "[McpClientManager] listTools failed for server '%s': %s",
                    server_name,
                    err,
                )
                server.available = False
        return api_tools

    async def call_tool(self, full_name: str, args: dict) -> Any:
        """Route a tool call to the correct MCP server.

        ``full_name`` must be ``mcp__<serverName>__<toolName>``.
        Returns the tool result (dict or string).
        """
        server_name, tool_name = self._parse_tool_name(full_name)
        if server_name is None:
            return {"error": f"Invalid MCP tool name: {full_name}"}

        server = self._servers.get(server_name)
        if server is None or not server.available or server.session is None:
            return {"error": f"MCP server '{server_name}' is not connected"}

        try:
            result = await asyncio.wait_for(
                server.session.call_tool(tool_name, args),
                timeout=MCP_CALL_TIMEOUT_SECONDS,
            )
            # MCP CallToolResult has .content (list of content blocks)
            # and .isError flag.
            if getattr(result, "isError", False):
                return {"error": self._extract_text(result), "isError": True}
            return self._extract_text(result)
        except TimeoutError:
            logger.warning(
                "[McpClientManager] callTool timed out for '%s' after %ds",
                full_name,
                MCP_CALL_TIMEOUT_SECONDS,
            )
            server.available = False
            return {
                "error": (
                    f"MCP tool call timed out after {MCP_CALL_TIMEOUT_SECONDS}s"
                    f" — the server may have closed the connection prematurely"
                )
            }
        except Exception as err:  # noqa: BLE001 - surface error to LLM
            logger.warning(
                "[McpClientManager] callTool failed for '%s': %s", full_name, err
            )
            return {"error": f"MCP tool call failed: {err}"}

    def is_tool_available(self, full_name: str) -> bool:
        """Check if the server for a given tool name is connected."""
        server_name, _ = self._parse_tool_name(full_name)
        if server_name is None:
            return False
        server = self._servers.get(server_name)
        return server is not None and server.available

    def get_trust(self, server_name: str) -> str:
        """Get the trust level for a server ('always' | 'ask')."""
        server = self._servers.get(server_name)
        return server.config.trust if server else "ask"

    def get_server_name(self, full_name: str) -> str | None:
        """Extract the server name from a namespaced tool name."""
        server_name, _ = self._parse_tool_name(full_name)
        return server_name

    async def close_all(self) -> None:
        """Close all MCP client connections and kill stdio subprocesses."""
        try:
            await self._exit_stack.aclose()
        except Exception as err:  # noqa: BLE001 - best-effort cleanup
            logger.warning("[McpClientManager] Error during close_all: %s", err)
        finally:
            self._servers.clear()
            self._exit_stack = AsyncExitStack()

    @staticmethod
    def _parse_tool_name(full_name: str) -> tuple[str | None, str | None]:
        """Parse ``mcp__<serverName>__<toolName>`` into (serverName, toolName).

        Tool names may contain double underscores themselves, so we only split
        on the first two ``__`` separators after the ``mcp__`` prefix.
        """
        if not full_name.startswith(MCP_TOOL_PREFIX):
            return None, None
        rest = full_name[len(MCP_TOOL_PREFIX):]
        parts = rest.split("__", 1)
        if len(parts) != 2:
            return None, None
        return parts[0], parts[1]

    @staticmethod
    def _extract_text(result: Any) -> Any:
        """Extract text content from an MCP CallToolResult.

        MCP results have a ``.content`` list of content blocks (text, image,
        etc.). We extract text blocks; if there's only one, return the string
        directly; if multiple, return a dict with ``content`` list.
        """
        content = getattr(result, "content", None)
        if content is None:
            return ""
        texts: list[str] = []
        for block in content:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                texts.append(getattr(block, "text", ""))
        if len(texts) == 0:
            return ""
        if len(texts) == 1:
            return texts[0]
        return {"content": texts}


def build_mcp_server_configs_from_db(rows: list[Any]) -> list[McpServerConfig]:
    """Build McpServerConfig list from McpServer ORM rows."""
    configs: list[McpServerConfig] = []
    for row in rows:
        configs.append(
            McpServerConfig(
                id=row.id,
                name=row.name,
                transport=row.transport,
                command=row.command,
                args=list(row.args) if row.args else [],
                env=dict(row.env) if row.env else None,
                url=row.url,
                headers=dict(row.headers) if row.headers else None,
                trust=row.trust,
            )
        )
    return configs
