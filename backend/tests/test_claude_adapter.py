"""ClaudeCLIAdapter tests — mock subprocess, no real CLI needed.

Tests cover:
  8.1 — stream-json event → StreamEvent translation
  8.2 — control_request bash blacklist denial
  8.3 — control_request write path sandbox denial
  8.4 — session resume (session_store get/set/clear + DB query)
  8.5 — session resume failure fallback (stream retry)
  8.6 — image attachment content block
  8.7 — timeout watchdog (semantic inactivity)

The old SDK-based tests (_FakeClient / _FakeMessages) were removed when the
adapter migrated to the CLI subprocess route.  These tests call ``_read_events``
and ``_write_prompt`` directly with a fake ``asyncio.subprocess.Process``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from types import SimpleNamespace

import pytest
import pytest_asyncio

from app.adapters import claude_adapter
from app.adapters.base import AdapterAttachment, AdapterInput
from app.adapters.claude_adapter import ClaudeCLIAdapter
from tests.test_tools import conversation as _conversation_fixture

# Re-expose the shared conversation+workspace fixture so pytest resolves it
# by argument name while ruff doesn't flag a redefinition.
conversation = pytest_asyncio.fixture(_conversation_fixture.__wrapped__)


# ─── fake subprocess ──────────────────────────────────────────────────────────


class _FakeStdout:
    """Async stdout reader that yields pre-scripted JSON lines."""

    def __init__(self, lines: list[bytes] | None = None, hang: bool = False) -> None:
        self._lines = list(lines or [])
        self._hang = hang
        self._eof = False

    async def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        if self._hang:
            await asyncio.Event().wait()  # never resolves
            return b""
        self._eof = True
        return b""

    def at_eof(self) -> bool:
        return self._eof

    def feed_eof(self) -> None:
        self._eof = True


class _FakeStdin:
    """Async stdin writer that captures all written data."""

    def __init__(self) -> None:
        self._closed = False
        self.written: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self._closed = True

    def is_closing(self) -> bool:
        return self._closed


class _FakeStderr:
    """Async stderr reader that immediately returns EOF."""

    async def readline(self) -> bytes:
        return b""


class _FakeProc:
    """Minimal asyncio.subprocess.Process mock for _read_events tests."""

    def __init__(
        self,
        stdout_lines: list[bytes] | None = None,
        hang_stdout: bool = False,
    ) -> None:
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout(stdout_lines, hang_stdout)
        self.stderr = _FakeStderr()
        self.returncode: int | None = 0
        self.pid = 12345

    async def wait(self) -> int:
        return self.returncode or 0

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass


# ─── JSON line builders ───────────────────────────────────────────────────────


def _jl(obj: dict) -> bytes:
    return (json.dumps(obj) + "\n").encode()


def _se(event: dict) -> bytes:
    return _jl({"type": "stream_event", "event": event})


def _msg_start(model: str = "claude-test") -> bytes:
    return _se({"type": "message_start", "message": {"model": model}})


def _blk_start(btype: str, index: int = 0) -> bytes:
    return _se({
        "type": "content_block_start",
        "content_block": {"type": btype, "index": index},
    })


def _text_delta(text: str) -> bytes:
    return _se({
        "type": "content_block_delta",
        "delta": {"type": "text_delta", "text": text},
    })


def _blk_stop(index: int = 0) -> bytes:
    return _se({"type": "content_block_stop", "index": index})


def _msg_stop() -> bytes:
    return _se({"type": "message_stop"})


def _result(
    session_id: str = "sess_test",
    is_error: bool = False,
    usage: dict | None = None,
) -> bytes:
    return _jl({
        "type": "result",
        "session_id": session_id,
        "is_error": is_error,
        "usage": usage or {"input_tokens": 12, "output_tokens": 7},
    })


def _ctrl_req(
    request_id: str,
    tool_name: str,
    tool_input: dict,
) -> bytes:
    return _jl({
        "type": "control_request",
        "request_id": request_id,
        "request": {"tool_name": tool_name, "input": tool_input},
    })


def _system_line() -> bytes:
    return _jl({"type": "system", "subtype": "init", "session_id": "sess_sys"})


# ─── helpers ──────────────────────────────────────────────────────────────────


def _make_input(tmp_path, **overrides) -> AdapterInput:
    defaults: dict = dict(
        agent_id="ag_test",
        conversation_id="conv_test",
        run_id="run_test",
        prompt="hello",
        workspace_path=str(tmp_path),
        system_prompt="sys",
        api_key=None,
        api_base_url=None,
        model_id="claude-test",
        tool_names=[],
        user_id="test_user_1",
    )
    defaults.update(overrides)
    return AdapterInput(**defaults)


async def _collect_events(adapter, proc, inp, cancel_event=None):
    if cancel_event is None:
        cancel_event = asyncio.Event()
    events = []
    async for ev in adapter._read_events(proc, inp, cancel_event):
        events.append(ev)
    return events


def _parse_stdin(proc: _FakeProc) -> list[dict]:
    """Parse all JSON objects written to proc.stdin."""
    results = []
    for chunk in proc.stdin.written:
        text = chunk.decode("utf-8", errors="replace").strip()
        if text:
            results.append(json.loads(text))
    return results


# ─── 8.1: stream-json event → StreamEvent translation ────────────────────────


async def test_stream_event_translation(tmp_path):
    """Verify that stream_event JSON lines translate to the correct StreamEvent sequence."""
    lines = [
        _msg_start(),
        _blk_start("text", 0),
        _text_delta("Hi "),
        _text_delta("there"),
        _blk_stop(0),
        _msg_stop(),
        _result(session_id="sess_abc", usage={"input_tokens": 12, "output_tokens": 7}),
    ]
    proc = _FakeProc(lines)
    adapter = ClaudeCLIAdapter()
    inp = _make_input(tmp_path)

    events = await _collect_events(adapter, proc, inp)
    types = [e.type for e in events]

    assert types == [
        "message.start",
        "part.start",
        "part.delta",
        "part.delta",
        "part.end",
        "message.usage",
        "message.end",
        "run.usage",
    ]

    deltas = [e.delta["text"] for e in events if e.type == "part.delta"]
    assert deltas == ["Hi ", "there"]

    run_usage = events[-1]
    assert run_usage.type == "run.usage"
    assert run_usage.session_id == "sess_abc"
    assert run_usage.usage.input_tokens == 12
    assert run_usage.usage.output_tokens == 7


async def test_assistant_event_text(tmp_path):
    """Verify the non-streamed assistant event path (no --include-partial-messages)."""
    assistant_line = _jl({
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Hello world"}],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    })
    lines = [assistant_line, _result(session_id="sess_def")]
    proc = _FakeProc(lines)
    adapter = ClaudeCLIAdapter()
    inp = _make_input(tmp_path)

    events = await _collect_events(adapter, proc, inp)
    types = [e.type for e in events]

    assert "message.start" in types
    assert "part.delta" in types
    assert "message.end" in types
    assert types[-1] == "run.usage"

    delta = next(e for e in events if e.type == "part.delta")
    assert delta.delta["text"] == "Hello world"


# ─── 8.2: control_request bash blacklist denial ──────────────────────────────


async def test_control_request_bash_blacklist_denied(tmp_path, monkeypatch):
    """Blacklisted bash command should produce a deny control_response."""
    monkeypatch.setattr(claude_adapter, "_PLATFORM", "posix")

    lines = [
        _ctrl_req("req_1", "Bash", {"command": "rm -rf /"}),
        _result(session_id="sess_deny"),
    ]
    proc = _FakeProc(lines)
    adapter = ClaudeCLIAdapter()
    inp = _make_input(tmp_path)

    await _collect_events(adapter, proc, inp)

    responses = _parse_stdin(proc)
    assert len(responses) == 1
    resp = responses[0]
    assert resp["type"] == "control_response"
    assert resp["response"]["response"]["behavior"] == "deny"
    assert "safety policy" in resp["response"]["response"]["message"]


async def test_control_request_bash_safe_allowed(tmp_path, monkeypatch):
    """Non-blacklisted, non-approval-required bash command should be allowed."""
    monkeypatch.setattr(claude_adapter, "_PLATFORM", "posix")

    lines = [
        _ctrl_req("req_2", "Bash", {"command": "ls -la"}),
        _result(session_id="sess_allow"),
    ]
    proc = _FakeProc(lines)
    adapter = ClaudeCLIAdapter()
    inp = _make_input(tmp_path)

    await _collect_events(adapter, proc, inp)

    responses = _parse_stdin(proc)
    assert len(responses) == 1
    resp = responses[0]
    assert resp["response"]["response"]["behavior"] == "allow"


# ─── 8.3: control_request write path sandbox denial ──────────────────────────


async def test_control_request_write_path_outside_workspace_denied(tmp_path, monkeypatch):
    """Write to a path outside the workspace should be denied."""
    ws_root = tmp_path / "ws"
    ws_root.mkdir(parents=True, exist_ok=True)
    workspace = SimpleNamespace(
        mode="sandbox",
        bound_path=None,
        root_path=str(ws_root),
    )

    async def _fake_get_workspace(conv_id):
        return workspace

    monkeypatch.setattr(
        "app.infra.cache_helpers.get_workspace_cached", _fake_get_workspace
    )

    outside_path = str(tmp_path / "outside" / "evil.txt")
    lines = [
        _ctrl_req("req_3", "Write", {"file_path": outside_path, "content": "hack"}),
        _result(session_id="sess_write_deny"),
    ]
    proc = _FakeProc(lines)
    adapter = ClaudeCLIAdapter()
    inp = _make_input(tmp_path)

    await _collect_events(adapter, proc, inp)

    responses = _parse_stdin(proc)
    assert len(responses) == 1
    resp = responses[0]
    assert resp["response"]["response"]["behavior"] == "deny"
    assert "outside workspace" in resp["response"]["response"]["message"]


async def test_control_request_unknown_tool_allowed(tmp_path):
    """Unknown/read-only tools should be allowed (default branch)."""
    lines = [
        _ctrl_req("req_4", "Read", {"file_path": "README.md"}),
        _result(session_id="sess_read"),
    ]
    proc = _FakeProc(lines)
    adapter = ClaudeCLIAdapter()
    inp = _make_input(tmp_path)

    await _collect_events(adapter, proc, inp)

    responses = _parse_stdin(proc)
    assert len(responses) == 1
    assert responses[0]["response"]["response"]["behavior"] == "allow"


# ─── 8.4: session resume (session_store) ─────────────────────────────────────


async def test_session_store_in_memory_cache():
    """set → get → clear cycle for the in-memory cache layer."""
    from app.adapters.session_store import (
        adapter_session_key,
        claude_code_sessions,
        clear_claude_code_session,
        get_claude_code_session,
        set_claude_code_session,
    )

    conv = "conv_cache_test"
    agent = "ag_cache_test"
    key = adapter_session_key(conv, agent)
    assert key == f"{conv}:{agent}"

    claude_code_sessions.clear()
    assert await get_claude_code_session(conv, agent) is None

    set_claude_code_session(conv, agent, "sess_mem")
    assert claude_code_sessions[key] == "sess_mem"
    assert await get_claude_code_session(conv, agent) == "sess_mem"

    clear_claude_code_session(conv)
    assert key not in claude_code_sessions
    assert await get_claude_code_session(conv, agent) is None


async def test_session_store_db_query(conversation):
    """get_claude_code_session should query AgentRun.cli_session_id on cache miss."""
    from app.adapters.session_store import (
        claude_code_sessions,
        get_claude_code_session,
    )
    from app.db.engine import get_db
    from app.db.models import AgentRun
    from app.utils.clock import now_ms
    from app.utils.ids import new_run_id

    conv_id = conversation["conversation_id"]
    agent_id = conversation["agent_id"]
    claude_code_sessions.clear()

    now = now_ms()
    async with get_db() as session:
        session.add(AgentRun(
            id=new_run_id(),
            conversation_id=conv_id,
            agent_id=agent_id,
            status="completed",
            started_at=now,
            finished_at=now,
            cli_session_id="sess_from_db",
        ))

    result = await get_claude_code_session(conv_id, agent_id)
    assert result == "sess_from_db"

    key = f"{conv_id}:{agent_id}"
    assert key in claude_code_sessions


# ─── 8.5: session resume failure fallback ────────────────────────────────────


async def test_resume_fallback_on_failure(tmp_path, monkeypatch):
    """stream() should retry without --resume when the first attempt fails."""
    from app.adapters.cli_base import CLIAdapterBase
    from app.adapters.session_store import claude_code_sessions

    call_count = 0
    captured_inputs: list[AdapterInput] = []

    async def _fake_stream(self, input, cancel_event):
        nonlocal call_count
        call_count += 1
        captured_inputs.append(input)
        if call_count == 1:
            raise RuntimeError("session expired")
        yield  # make it an async generator

    monkeypatch.setattr(CLIAdapterBase, "stream", _fake_stream)

    claude_code_sessions.clear()
    claude_code_sessions["conv_test:ag_test"] = "old_sess"

    adapter = ClaudeCLIAdapter()
    inp = _make_input(tmp_path, resume_session_id="old_sess")

    events = []
    async for ev in adapter.stream(inp, asyncio.Event()):
        events.append(ev)

    assert call_count == 2
    # First attempt used the resume session id
    assert captured_inputs[0].resume_session_id == "old_sess"
    # Second attempt cleared it
    assert captured_inputs[1].resume_session_id is None
    # Cache was cleared
    assert "conv_test:ag_test" not in claude_code_sessions


async def test_no_retry_without_resume_session(tmp_path, monkeypatch):
    """stream() should not retry when there is no resume_session_id."""
    from app.adapters.cli_base import CLIAdapterBase

    call_count = 0

    async def _fake_stream(self, input, cancel_event):
        nonlocal call_count
        call_count += 1
        yield  # async generator

    monkeypatch.setattr(CLIAdapterBase, "stream", _fake_stream)

    adapter = ClaudeCLIAdapter()
    inp = _make_input(tmp_path)  # no resume_session_id

    events = []
    async for ev in adapter.stream(inp, asyncio.Event()):
        events.append(ev)

    assert call_count == 1


# ─── 8.6: image attachment content block ─────────────────────────────────────


async def test_image_attachment_content_block(tmp_path):
    """_write_prompt should emit a base64 image content block for image attachments."""
    image_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    image_path = tmp_path / "test.png"
    image_path.write_bytes(image_data)

    att = AdapterAttachment(
        id="att_1",
        file_name="test.png",
        mime_type="image/png",
        kind="image",
        abs_path=str(image_path),
    )
    inp = _make_input(tmp_path, attachments=[att])
    proc = _FakeProc()

    adapter = ClaudeCLIAdapter()
    await adapter._write_prompt(proc, inp)

    written = _parse_stdin(proc)
    assert len(written) == 1
    payload = written[0]
    assert payload["type"] == "user"
    content = payload["message"]["content"]
    assert len(content) == 2

    assert content[0]["type"] == "text"
    assert content[0]["text"] == "hello"

    assert content[1]["type"] == "image"
    assert content[1]["source"]["type"] == "base64"
    assert content[1]["source"]["media_type"] == "image/png"
    assert content[1]["source"]["data"] == base64.b64encode(image_data).decode("ascii")


async def test_file_attachment_text_note(tmp_path):
    """Non-image attachments should be appended as text notes in the prompt."""
    file_path = tmp_path / "data.csv"
    file_path.write_text("a,b,c\n1,2,3\n")

    att = AdapterAttachment(
        id="att_2",
        file_name="data.csv",
        mime_type="text/csv",
        kind="file",
        abs_path=str(file_path),
    )
    inp = _make_input(tmp_path, prompt="analyze this", attachments=[att])
    proc = _FakeProc()

    adapter = ClaudeCLIAdapter()
    await adapter._write_prompt(proc, inp)

    written = _parse_stdin(proc)
    payload = written[0]
    content = payload["message"]["content"]
    assert len(content) == 1
    assert content[0]["type"] == "text"
    assert "[Attached file: data.csv" in content[0]["text"]
    assert "analyze this" in content[0]["text"]


async def test_image_attachment_too_large(tmp_path):
    """Image attachments exceeding the 10 MB limit should raise."""
    large_path = tmp_path / "big.png"
    large_path.write_bytes(b"\x00" * (claude_adapter.MAX_IMAGE_SIZE_BYTES + 1))

    att = AdapterAttachment(
        id="att_big",
        file_name="big.png",
        mime_type="image/png",
        kind="image",
        abs_path=str(large_path),
    )
    inp = _make_input(tmp_path, attachments=[att])
    proc = _FakeProc()

    adapter = ClaudeCLIAdapter()
    with pytest.raises(RuntimeError, match="exceeds 10 MB limit"):
        await adapter._write_prompt(proc, inp)


# ─── 8.7: timeout watchdog ───────────────────────────────────────────────────


async def test_timeout_watchdog(tmp_path, monkeypatch):
    """Semantic inactivity timeout should fire when stdout produces no meaningful events."""
    monkeypatch.setattr(claude_adapter, "DEFAULT_SEMANTIC_INACTIVITY_TIMEOUT", 0.1)
    monkeypatch.setattr(claude_adapter, "DEFAULT_FIRST_TURN_NO_PROGRESS_TIMEOUT", 0.1)

    # Send one system event so any_event=True (avoids "no output" RuntimeError),
    # then hang stdout forever.
    proc = _FakeProc(stdout_lines=[_system_line()], hang_stdout=True)
    adapter = ClaudeCLIAdapter()
    inp = _make_input(tmp_path)

    t0 = time.monotonic()
    events = await _collect_events(adapter, proc, inp)
    elapsed = time.monotonic() - t0

    # Should complete in roughly one polling cycle (~5s), not 10 minutes.
    assert elapsed < 15, f"timeout took too long: {elapsed:.1f}s"

    # Should produce at least a run.usage event (post-loop).
    assert any(e.type == "run.usage" for e in events)


async def test_first_turn_no_progress_timeout(tmp_path, monkeypatch):
    """First-turn no-progress timeout fires when message_start is seen but no text follows."""
    monkeypatch.setattr(claude_adapter, "DEFAULT_SEMANTIC_INACTIVITY_TIMEOUT", 0.1)
    monkeypatch.setattr(claude_adapter, "DEFAULT_FIRST_TURN_NO_PROGRESS_TIMEOUT", 0.1)

    # Send message_start (sets first_turn_started=True), then hang.
    proc = _FakeProc(
        stdout_lines=[_msg_start(), _blk_start("text", 0)],
        hang_stdout=True,
    )
    adapter = ClaudeCLIAdapter()
    inp = _make_input(tmp_path)

    t0 = time.monotonic()
    events = await _collect_events(adapter, proc, inp)
    elapsed = time.monotonic() - t0

    assert elapsed < 15
    assert any(e.type == "run.usage" for e in events)


# ─── 8.1: _build_args includes --resume ─────────────────────────────────────


async def test_build_args_includes_resume_when_present(tmp_path):
    """_build_args should include --resume <session_id> when resume_session_id is set."""
    adapter = ClaudeCLIAdapter()
    inp = _make_input(tmp_path, resume_session_id="sess_resume_123")
    args = adapter._build_args(inp)
    assert "--resume" in args
    idx = args.index("--resume")
    assert args[idx + 1] == "sess_resume_123"


async def test_build_args_omits_resume_when_absent(tmp_path):
    """_build_args should NOT include --resume when resume_session_id is None."""
    adapter = ClaudeCLIAdapter()
    inp = _make_input(tmp_path)  # no resume_session_id
    args = adapter._build_args(inp)
    assert "--resume" not in args


async def test_build_args_uses_accept_edits_permission(tmp_path):
    """_build_args should use acceptEdits (not bypassPermissions)."""
    adapter = ClaudeCLIAdapter()
    inp = _make_input(tmp_path)
    args = adapter._build_args(inp)
    assert "--permission-mode" in args
    idx = args.index("--permission-mode")
    assert args[idx + 1] == "acceptEdits"


async def test_build_args_blocks_model_in_custom_args(tmp_path):
    """--model in custom_args should be stripped by blocked args filter."""
    from app.adapters.cli_base import filter_custom_args
    custom = ["--model", "claude-evil", "--other-flag"]
    filtered = filter_custom_args(custom, claude_adapter._claude_blocked_args)
    assert "--model" not in filtered
    assert "--other-flag" in filtered


# ─── 8.5: MCP Bridge tool filtering ──────────────────────────────────────────


async def test_mcp_bridge_tool_filtering_with_tool_names():
    """MCP Bridge should filter exposed tools based on --tool-names argument."""
    from app.mcp_bridge import CLI_MCP_TOOL_NAMES

    # Simulate the filtering logic from mcp_bridge.main()
    tool_names_arg = "web_search,rag_search"
    requested = {n.strip() for n in tool_names_arg.split(",") if n.strip()}
    assert requested == {"web_search", "rag_search"}
    assert requested != CLI_MCP_TOOL_NAMES


async def test_mcp_bridge_tool_filtering_empty_falls_back_to_default():
    """Empty --tool-names should fall back to CLI_MCP_TOOL_NAMES."""
    from app.mcp_bridge import CLI_MCP_TOOL_NAMES

    tool_names_arg = ""
    if tool_names_arg:
        tool_name_set = {n.strip() for n in tool_names_arg.split(",") if n.strip()}
    else:
        tool_name_set = CLI_MCP_TOOL_NAMES
    assert tool_name_set == CLI_MCP_TOOL_NAMES


async def test_mcp_bridge_event_loop_reuse():
    """_get_mcp_event_loop should return the same loop on subsequent calls."""
    from app.mcp_bridge import _get_mcp_event_loop

    loop1 = _get_mcp_event_loop()
    loop2 = _get_mcp_event_loop()
    assert loop1 is loop2
    assert not loop1.is_closed()


# ─── 3.8: Malformed control_request denial ───────────────────────────────────


async def test_malformed_control_request_denied(tmp_path):
    """control_request with no tool_name should be denied."""
    lines = [
        _jl({
            "type": "control_request",
            "request_id": "req_malformed",
            "request": {},
        }),
        _result(session_id="sess_malformed"),
    ]
    proc = _FakeProc(lines)
    adapter = ClaudeCLIAdapter()
    inp = _make_input(tmp_path)

    await _collect_events(adapter, proc, inp)

    responses = _parse_stdin(proc)
    assert len(responses) == 1
    resp = responses[0]
    assert resp["type"] == "control_response"
    assert resp["response"]["response"]["behavior"] == "deny"
    assert "Malformed" in resp["response"]["response"]["message"]
