"""Unit tests for CodexCLIAdapter — JSON-RPC communication and event translation.

Tests mock the subprocess to verify:
- JSON-RPC request format (thread/start, thread/run)
- Event translation (agent_message, reasoning, command_execution, mcp_tool_call, turn.completed)
- Executable resolution (executable_path → CODEX_EXECUTABLE → PATH)
- MCP env construction
- Cancel logic (terminate → kill)
- Error handling (executable not found, EOF, spawn failure)
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.adapters.base import AdapterInput
from app.adapters.codex_cli_adapter import CodexCLIAdapter, _RunUsage


# ─── Test fixtures ───────────────────────────────────────────────


def _make_input(**overrides: Any) -> AdapterInput:
    """Build a minimal AdapterInput for codex adapter tests."""
    base = {
        "agent_id": "ag_test",
        "conversation_id": "conv_test",
        "run_id": "run_test",
        "prompt": "Hello, codex!",
        "workspace_path": "/tmp/ws",
        "system_prompt": "You are a test agent.",
        "api_key": None,
        "api_base_url": None,
        "model_id": None,
        "tool_names": ["write_artifact", "fs_read"],
        "executable_path": None,
    }
    base.update(overrides)
    return AdapterInput(**base)


class FakeStreamWriter:
    """Captures data written to stdin for assertion."""

    def __init__(self) -> None:
        self.written: list[str] = []
        self._closed = False

    def write(self, data: bytes) -> None:
        if self._closed:
            return
        self.written.append(data.decode("utf-8"))

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self._closed = True

    @property
    def is_closing(self) -> bool:
        return self._closed


class FakeStreamReader:
    """Yields pre-configured JSON-RPC lines, then EOF."""

    def __init__(self, lines: list[str] | None = None) -> None:
        self._lines = list(lines or [])
        self._index = 0

    async def readline(self) -> bytes:
        if self._index < len(self._lines):
            line = self._lines[self._index]
            self._index += 1
            return (line + "\n").encode("utf-8")
        return b""  # EOF


class FakeProcess:
    """Mimics asyncio.subprocess.Process for testing."""

    def __init__(self, stdout_lines: list[str] | None = None) -> None:
        self.stdin = FakeStreamWriter()
        self.stdout = FakeStreamReader(stdout_lines)
        self.stderr = FakeStreamReader([])
        self.returncode: int | None = None
        self._terminated = False
        self._killed = False

    def terminate(self) -> None:
        self._terminated = True
        self.returncode = -15  # SIGTERM

    def kill(self) -> None:
        self._killed = True
        self.returncode = -9  # SIGKILL

    async def wait(self) -> int:
        return self.returncode or 0


def _make_rpc_response(req_id: int, result: dict) -> str:
    """Build a JSON-RPC response line."""
    return json.dumps({"jsonrpc": "2.0", "id": req_id, "result": result})


def _make_rpc_notification(method: str, params: dict) -> str:
    """Build a JSON-RPC notification line."""
    return json.dumps({"jsonrpc": "2.0", "method": method, "params": params})


# ─── Executable resolution ───────────────────────────────────────


def test_resolve_executable_from_input():
    """_resolve_executable uses input.executable_path when set."""
    adapter = CodexCLIAdapter()
    with patch("pathlib.Path.exists", return_value=True), \
         patch("os.access", return_value=True):
        result = adapter._resolve_executable(_make_input(executable_path="/usr/local/bin/codex"))
    assert result == "/usr/local/bin/codex"


def test_resolve_executable_from_env(monkeypatch):
    """_resolve_executable falls back to CODEX_EXECUTABLE env var."""
    monkeypatch.setenv("CODEX_EXECUTABLE", "/opt/codex/bin/codex")
    monkeypatch.delenv("CODEX_EXECUTABLE", raising=False)
    monkeypatch.setenv("CODEX_EXECUTABLE", "/opt/codex/bin/codex")
    adapter = CodexCLIAdapter()
    with patch("pathlib.Path.exists", return_value=True), \
         patch("os.access", return_value=True):
        result = adapter._resolve_executable(_make_input())
    assert result == "/opt/codex/bin/codex"


def test_resolve_executable_from_path(monkeypatch):
    """_resolve_executable falls back to PATH search."""
    monkeypatch.delenv("CODEX_EXECUTABLE", raising=False)
    adapter = CodexCLIAdapter()
    with patch("shutil.which", return_value="/usr/bin/codex"):
        result = adapter._resolve_executable(_make_input())
    assert result == "/usr/bin/codex"


def test_resolve_executable_not_found(monkeypatch):
    """_resolve_executable returns None when codex is not available."""
    monkeypatch.delenv("CODEX_EXECUTABLE", raising=False)
    adapter = CodexCLIAdapter()
    with patch("shutil.which", return_value=None):
        result = adapter._resolve_executable(_make_input())
    assert result is None


# ─── MCP env construction ────────────────────────────────────────


def test_build_mcp_env_contains_all_vars():
    """_build_mcp_env includes all required AGENTHUB_* variables."""
    adapter = CodexCLIAdapter()
    with patch("app.adapters.codex_cli_adapter.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(port=3000)
        result = adapter._build_mcp_env(_make_input(), "test-token")

    assert "AGENTHUB_INTERNAL_BASE_URL" in result
    assert "AGENTHUB_INTERNAL_TOOL_TOKEN" in result
    assert result["AGENTHUB_INTERNAL_TOOL_TOKEN"] == "test-token"
    assert result["AGENTHUB_CONVERSATION_ID"] == "conv_test"
    assert result["AGENTHUB_AGENT_ID"] == "ag_test"
    assert result["AGENTHUB_RUN_ID"] == "run_test"
    assert "AGENTHUB_ALLOWED_TOOLS" in result
    assert "write_artifact" in result["AGENTHUB_ALLOWED_TOOLS"]
    assert "fs_read" in result["AGENTHUB_ALLOWED_TOOLS"]


def test_build_mcp_env_without_tool_names():
    """_build_mcp_env omits AGENTHUB_ALLOWED_TOOLS when tool_names is empty."""
    adapter = CodexCLIAdapter()
    with patch("app.adapters.codex_cli_adapter.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(port=3000)
        result = adapter._build_mcp_env(_make_input(tool_names=[]), "tok")

    assert "AGENTHUB_ALLOWED_TOOLS" not in result


# ─── Event translation (via stream() with mocked subprocess) ─────


@pytest.mark.asyncio
async def test_stream_executable_not_found():
    """When codex executable is not found, yields MessageStart + MessageEnd."""
    adapter = CodexCLIAdapter()
    with patch.object(adapter, "_resolve_executable", return_value=None):
        events = []
        async for ev in adapter.stream(_make_input(), asyncio.Event()):
            events.append(ev)

    # Should have at least MessageStartEvent and MessageEndEvent
    types = [e.type for e in events]
    assert "message.start" in types
    assert "message.end" in types


@pytest.mark.asyncio
async def test_stream_sends_thread_start_and_run():
    """Verify JSON-RPC thread/start and thread/run requests are sent."""
    # Simulate: thread/start response → agent_message → turn.completed → thread/run response
    stdout_lines = [
        _make_rpc_response(1, {"thread_id": "thread_abc"}),  # thread/start response
        _make_rpc_notification("agent_message", {"delta": "Hello!"}),
        _make_rpc_notification("turn.completed", {"usage": {"input_tokens": 10, "output_tokens": 5}}),
        _make_rpc_response(2, {"status": "completed"}),  # thread/run response
    ]
    fake_proc = FakeProcess(stdout_lines)

    adapter = CodexCLIAdapter()
    cancel_event = asyncio.Event()

    with patch.object(adapter, "_resolve_executable", return_value="/usr/bin/codex"), \
         patch("app.adapters.codex_cli_adapter.generate_tool_token", return_value="tok"), \
         patch("app.adapters.codex_cli_adapter.revoke_tool_token"), \
         patch("app.adapters.codex_cli_adapter.prepare_codex_home", return_value="/tmp/codex-home"), \
         patch("app.adapters.codex_cli_adapter.cleanup_codex_home"), \
         patch("app.adapters.codex_cli_adapter.get_settings") as mock_settings, \
         patch.object(adapter, "_resolve_mcp_script_path", return_value="/path/mcp.mjs"), \
         patch("asyncio.create_subprocess_exec", return_value=fake_proc), \
         patch("asyncio.create_task", side_effect=lambda coro: asyncio.ensure_future(coro)):
        mock_settings.return_value = MagicMock(port=3000, data_path=MagicMock())

        events = []
        async for ev in adapter.stream(_make_input(), cancel_event):
            events.append(ev)

    # Verify stdin received thread/start and thread/run
    written = "".join(fake_proc.stdin.written)
    assert "thread/start" in written
    assert "thread/run" in written
    assert '"jsonrpc": "2.0"' in written
    assert "thread_abc" in written  # thread_id in thread/run request
    assert "Hello, codex!" in written  # prompt in thread/run request


@pytest.mark.asyncio
async def test_stream_translates_agent_message_delta():
    """agent_message deltas produce PartStartEvent + PartDeltaEvent."""
    stdout_lines = [
        _make_rpc_response(1, {"thread_id": "t1"}),
        _make_rpc_notification("agent_message", {"delta": "Hello"}),
        _make_rpc_notification("agent_message", {"delta": " world"}),
        _make_rpc_notification("turn.completed", {}),
        _make_rpc_response(2, {"status": "ok"}),
    ]
    fake_proc = FakeProcess(stdout_lines)

    adapter = CodexCLIAdapter()
    with patch.object(adapter, "_resolve_executable", return_value="/usr/bin/codex"), \
         patch("app.adapters.codex_cli_adapter.generate_tool_token", return_value="tok"), \
         patch("app.adapters.codex_cli_adapter.revoke_tool_token"), \
         patch("app.adapters.codex_cli_adapter.prepare_codex_home", return_value="/tmp/ch"), \
         patch("app.adapters.codex_cli_adapter.cleanup_codex_home"), \
         patch("app.adapters.codex_cli_adapter.get_settings") as mock_settings, \
         patch.object(adapter, "_resolve_mcp_script_path", return_value="/p/m.mjs"), \
         patch("asyncio.create_subprocess_exec", return_value=fake_proc), \
         patch("asyncio.create_task", side_effect=lambda coro: asyncio.ensure_future(coro)):
        mock_settings.return_value = MagicMock(port=3000, data_path=MagicMock())

        events = []
        async for ev in adapter.stream(_make_input(), asyncio.Event()):
            events.append(ev)

    types = [e.type for e in events]
    assert "message.start" in types
    assert "part.start" in types
    assert "part.delta" in types
    assert "part.end" in types
    assert "message.end" in types

    # Verify text content
    part_starts = [e for e in events if e.type == "part.start"]
    assert any(e.part.get("type") == "text" for e in part_starts)

    deltas = [e for e in events if e.type == "part.delta"]
    text = "".join(d.delta.get("text", "") for d in deltas)
    assert "Hello" in text
    assert "world" in text


@pytest.mark.asyncio
async def test_stream_translates_reasoning_delta():
    """reasoning deltas produce thinking PartStartEvent + PartDeltaEvent."""
    stdout_lines = [
        _make_rpc_response(1, {"thread_id": "t1"}),
        _make_rpc_notification("reasoning", {"delta": "Thinking..."}),
        _make_rpc_notification("turn.completed", {}),
        _make_rpc_response(2, {"status": "ok"}),
    ]
    fake_proc = FakeProcess(stdout_lines)

    adapter = CodexCLIAdapter()
    with patch.object(adapter, "_resolve_executable", return_value="/usr/bin/codex"), \
         patch("app.adapters.codex_cli_adapter.generate_tool_token", return_value="tok"), \
         patch("app.adapters.codex_cli_adapter.revoke_tool_token"), \
         patch("app.adapters.codex_cli_adapter.prepare_codex_home", return_value="/tmp/ch"), \
         patch("app.adapters.codex_cli_adapter.cleanup_codex_home"), \
         patch("app.adapters.codex_cli_adapter.get_settings") as mock_settings, \
         patch.object(adapter, "_resolve_mcp_script_path", return_value="/p/m.mjs"), \
         patch("asyncio.create_subprocess_exec", return_value=fake_proc), \
         patch("asyncio.create_task", side_effect=lambda coro: asyncio.ensure_future(coro)):
        mock_settings.return_value = MagicMock(port=3000, data_path=MagicMock())

        events = []
        async for ev in adapter.stream(_make_input(), asyncio.Event()):
            events.append(ev)

    part_starts = [e for e in events if e.type == "part.start"]
    assert any(e.part.get("type") == "thinking" for e in part_starts)


@pytest.mark.asyncio
async def test_stream_translates_command_execution():
    """command_execution produces ToolCallEvent + ToolResultEvent."""
    stdout_lines = [
        _make_rpc_response(1, {"thread_id": "t1"}),
        _make_rpc_notification("command_execution", {
            "call_id": "cmd_1",
            "command": "bash",
            "args": {"command": "ls -la"},
            "output": "total 0",
            "exit_code": 0,
        }),
        _make_rpc_notification("turn.completed", {}),
        _make_rpc_response(2, {"status": "ok"}),
    ]
    fake_proc = FakeProcess(stdout_lines)

    adapter = CodexCLIAdapter()
    with patch.object(adapter, "_resolve_executable", return_value="/usr/bin/codex"), \
         patch("app.adapters.codex_cli_adapter.generate_tool_token", return_value="tok"), \
         patch("app.adapters.codex_cli_adapter.revoke_tool_token"), \
         patch("app.adapters.codex_cli_adapter.prepare_codex_home", return_value="/tmp/ch"), \
         patch("app.adapters.codex_cli_adapter.cleanup_codex_home"), \
         patch("app.adapters.codex_cli_adapter.get_settings") as mock_settings, \
         patch.object(adapter, "_resolve_mcp_script_path", return_value="/p/m.mjs"), \
         patch("asyncio.create_subprocess_exec", return_value=fake_proc), \
         patch("asyncio.create_task", side_effect=lambda coro: asyncio.ensure_future(coro)):
        mock_settings.return_value = MagicMock(port=3000, data_path=MagicMock())

        events = []
        async for ev in adapter.stream(_make_input(), asyncio.Event()):
            events.append(ev)

    tool_calls = [e for e in events if e.type == "tool.call"]
    tool_results = [e for e in events if e.type == "tool.result"]
    assert len(tool_calls) >= 1
    assert tool_calls[0].tool_name == "bash"
    assert len(tool_results) >= 1
    assert tool_results[0].is_error is False


@pytest.mark.asyncio
async def test_stream_translates_mcp_tool_call():
    """mcp_tool_call produces ToolCallEvent + ToolResultEvent."""
    stdout_lines = [
        _make_rpc_response(1, {"thread_id": "t1"}),
        _make_rpc_notification("mcp_tool_call", {
            "call_id": "mcp_1",
            "tool_name": "write_artifact",
            "args": {"type": "document", "title": "Test"},
            "result": {"artifactId": "art_123"},
            "is_error": False,
        }),
        _make_rpc_notification("turn.completed", {}),
        _make_rpc_response(2, {"status": "ok"}),
    ]
    fake_proc = FakeProcess(stdout_lines)

    adapter = CodexCLIAdapter()
    with patch.object(adapter, "_resolve_executable", return_value="/usr/bin/codex"), \
         patch("app.adapters.codex_cli_adapter.generate_tool_token", return_value="tok"), \
         patch("app.adapters.codex_cli_adapter.revoke_tool_token"), \
         patch("app.adapters.codex_cli_adapter.prepare_codex_home", return_value="/tmp/ch"), \
         patch("app.adapters.codex_cli_adapter.cleanup_codex_home"), \
         patch("app.adapters.codex_cli_adapter.get_settings") as mock_settings, \
         patch.object(adapter, "_resolve_mcp_script_path", return_value="/p/m.mjs"), \
         patch("asyncio.create_subprocess_exec", return_value=fake_proc), \
         patch("asyncio.create_task", side_effect=lambda coro: asyncio.ensure_future(coro)):
        mock_settings.return_value = MagicMock(port=3000, data_path=MagicMock())

        events = []
        async for ev in adapter.stream(_make_input(), asyncio.Event()):
            events.append(ev)

    tool_calls = [e for e in events if e.type == "tool.call"]
    assert len(tool_calls) >= 1
    assert tool_calls[0].tool_name == "write_artifact"


@pytest.mark.asyncio
async def test_stream_translates_turn_completed_usage():
    """turn.completed with usage produces RunUsageEvent."""
    stdout_lines = [
        _make_rpc_response(1, {"thread_id": "t1"}),
        _make_rpc_notification("turn.completed", {
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_tokens": 20,
            }
        }),
        _make_rpc_response(2, {
            "status": "ok",
            "usage": {"input_tokens": 100, "output_tokens": 50},
        }),
    ]
    fake_proc = FakeProcess(stdout_lines)

    adapter = CodexCLIAdapter()
    with patch.object(adapter, "_resolve_executable", return_value="/usr/bin/codex"), \
         patch("app.adapters.codex_cli_adapter.generate_tool_token", return_value="tok"), \
         patch("app.adapters.codex_cli_adapter.revoke_tool_token"), \
         patch("app.adapters.codex_cli_adapter.prepare_codex_home", return_value="/tmp/ch"), \
         patch("app.adapters.codex_cli_adapter.cleanup_codex_home"), \
         patch("app.adapters.codex_cli_adapter.get_settings") as mock_settings, \
         patch.object(adapter, "_resolve_mcp_script_path", return_value="/p/m.mjs"), \
         patch("asyncio.create_subprocess_exec", return_value=fake_proc), \
         patch("asyncio.create_task", side_effect=lambda coro: asyncio.ensure_future(coro)):
        mock_settings.return_value = MagicMock(port=3000, data_path=MagicMock())

        events = []
        async for ev in adapter.stream(_make_input(), asyncio.Event()):
            events.append(ev)

    usage_events = [e for e in events if e.type == "run.usage"]
    assert len(usage_events) >= 1
    usage = usage_events[0].usage
    assert usage.input_tokens >= 100
    assert usage.output_tokens >= 50


# ─── Error handling ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stream_eof_before_turn_completed():
    """When stdout EOFs before turn.completed, adapter yields MessageEnd gracefully."""
    stdout_lines = [
        _make_rpc_response(1, {"thread_id": "t1"}),
        _make_rpc_notification("agent_message", {"delta": "partial"}),
        # No turn.completed, no thread/run response — just EOF
    ]
    fake_proc = FakeProcess(stdout_lines)

    adapter = CodexCLIAdapter()
    with patch.object(adapter, "_resolve_executable", return_value="/usr/bin/codex"), \
         patch("app.adapters.codex_cli_adapter.generate_tool_token", return_value="tok"), \
         patch("app.adapters.codex_cli_adapter.revoke_tool_token"), \
         patch("app.adapters.codex_cli_adapter.prepare_codex_home", return_value="/tmp/ch"), \
         patch("app.adapters.codex_cli_adapter.cleanup_codex_home"), \
         patch("app.adapters.codex_cli_adapter.get_settings") as mock_settings, \
         patch.object(adapter, "_resolve_mcp_script_path", return_value="/p/m.mjs"), \
         patch("asyncio.create_subprocess_exec", return_value=fake_proc), \
         patch("asyncio.create_task", side_effect=lambda coro: asyncio.ensure_future(coro)):
        mock_settings.return_value = MagicMock(port=3000, data_path=MagicMock())

        events = []
        async for ev in adapter.stream(_make_input(), asyncio.Event()):
            events.append(ev)

    types = [e.type for e in events]
    assert "message.end" in types  # graceful end despite EOF


@pytest.mark.asyncio
async def test_stream_spawn_failure():
    """When subprocess spawn raises, adapter yields MessageStart + MessageEnd."""
    adapter = CodexCLIAdapter()
    with patch.object(adapter, "_resolve_executable", return_value="/usr/bin/codex"), \
         patch("app.adapters.codex_cli_adapter.generate_tool_token", return_value="tok"), \
         patch("app.adapters.codex_cli_adapter.revoke_tool_token"), \
         patch("app.adapters.codex_cli_adapter.prepare_codex_home", return_value="/tmp/ch"), \
         patch("app.adapters.codex_cli_adapter.cleanup_codex_home"), \
         patch("app.adapters.codex_cli_adapter.get_settings") as mock_settings, \
         patch.object(adapter, "_resolve_mcp_script_path", return_value="/p/m.mjs"), \
         patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("codex not found")), \
         patch("asyncio.create_task", side_effect=lambda coro: asyncio.ensure_future(coro)):
        mock_settings.return_value = MagicMock(port=3000, data_path=MagicMock())

        events = []
        async for ev in adapter.stream(_make_input(), asyncio.Event()):
            events.append(ev)

    types = [e.type for e in events]
    assert "message.start" in types
    assert "message.end" in types


# ─── Usage merge ─────────────────────────────────────────────────


def test_merge_usage_accumulates():
    """_merge_usage accumulates token counts from multiple events."""
    adapter = CodexCLIAdapter()
    usage = _RunUsage()
    adapter._merge_usage(usage, {"input_tokens": 10, "output_tokens": 5})
    adapter._merge_usage(usage, {"input_tokens": 20, "output_tokens": 15, "cache_read_tokens": 5})
    assert usage.input_tokens == 30
    assert usage.output_tokens == 20
    assert usage.cache_read_tokens == 5


def test_merge_usage_handles_missing_fields():
    """_merge_usage handles events with missing usage fields."""
    adapter = CodexCLIAdapter()
    usage = _RunUsage()
    adapter._merge_usage(usage, {})
    assert usage.input_tokens == 0
    assert usage.output_tokens == 0


def test_merge_usage_snake_and_camel_keys():
    """_merge_usage accepts both snake_case and camelCase keys."""
    adapter = CodexCLIAdapter()
    usage = _RunUsage()
    adapter._merge_usage(usage, {"input_tokens": 10, "outputTokens": 5})
    assert usage.input_tokens == 10
    assert usage.output_tokens == 5
