"""ClaudeCLIAdapter — spawn ``claude`` CLI, stream-json protocol.

Port of multica's ``server/pkg/agent/claude.go`` (claudeBackend). Communicates
with the Claude Code CLI via stream-json over stdin/stdout; the CLI manages its
own tool execution and session lifecycle. The adapter translates CLI events into
AChat StreamEvent objects.

Protocol reference: Claude Code ``--output-format stream-json`` manual.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import os
import sys
import tempfile
import time
from collections.abc import AsyncIterator
from dataclasses import replace as dc_replace
from typing import Any

from app.adapters.base import AdapterInput, AdapterName
from app.adapters.cli_base import BlockedArgMode, CLIAdapterBase, filter_custom_args
from app.adapters.session_store import clear_claude_code_session
from app.schemas.events import (
    BashCommandResolvedEvent,
    FsWriteResolvedEvent,
    MessageEndEvent,
    MessageStartEvent,
    MessageUsageEventPayload,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    RunUsageEvent,
    StreamEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from app.schemas.messages import MessageUsage, RunUsage
from app.utils.clock import now_ms
from app.utils.ids import new_message_id
from app.utils.platform import IS_WINDOWS

logger = logging.getLogger(__name__)

_PLATFORM = "windows" if IS_WINDOWS else "posix"

# ─── timeout watchdog (mirrors Codex adapter) ────────────────────

DEFAULT_SEMANTIC_INACTIVITY_TIMEOUT = 10 * 60  # seconds
DEFAULT_FIRST_TURN_NO_PROGRESS_TIMEOUT = 30  # seconds

MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB

# Default MCP tool names exposed when the agent has no explicit tool_names.
_DEFAULT_MCP_TOOL_NAMES = [
    "write_artifact",
    "read_artifact",
    "ask_user",
    "deploy_artifact",
    "deploy_workspace",
    "code_explore",
]


def _build_mcp_tool_hint(tool_names: list[str]) -> str:
    """Generate the MCP tool hint dynamically from the actual tool list."""
    names = tool_names if tool_names else _DEFAULT_MCP_TOOL_NAMES
    lines = [
        f"- `{n}` → `mcp__achat-tools__{n}`"
        for n in names
    ]
    hint = (
        "\n\n## AChat MCP Tools\n"
        "AChat platform tools are available via the \"achat-tools\" MCP server. "
        "Use the exact MCP-prefixed name:\n\n"
        + "\n".join(lines)
        + "\n"
    )
    if "code_explore" in names:
        hint += (
            "Use code_explore for structure, call paths, and impact; fall back to "
            "file search/read tools for exact lines or unavailable results.\n"
        )
    return hint


# ─── blocked args (mirrors multica claudeBlockedArgs) ──────────

_claude_blocked_args: dict[str, BlockedArgMode] = {
    "-p": BlockedArgMode.STANDALONE,
    "--output-format": BlockedArgMode.WITH_VALUE,
    "--input-format": BlockedArgMode.WITH_VALUE,
    "--permission-mode": BlockedArgMode.WITH_VALUE,
    "--mcp-config": BlockedArgMode.WITH_VALUE,
    "--effort": BlockedArgMode.WITH_VALUE,
    "--include-partial-messages": BlockedArgMode.STANDALONE,
    "--disallowedTools": BlockedArgMode.WITH_VALUE,
    "--model": BlockedArgMode.WITH_VALUE,
}

DEFAULT_CLAUDE_MODEL = "claude-opus-4-7"


# ─── stream-json type defs ─────────────────────────────────────


class _ClaudeSDKMessage:
    """One JSON line from Claude Code stream-json stdout."""

    __slots__ = (
        "type", "message", "subtype", "session_id", "model",
        "result_text", "is_error", "duration_ms", "num_turns",
        "usage", "model_usage", "request_id", "request",
        "event", "parent_tool_use_id",
    )

    def __init__(self, raw: dict[str, Any]) -> None:
        self.type: str = raw.get("type", "")
        self.message: dict[str, Any] | None = raw.get("message")
        self.subtype: str = raw.get("subtype", "")
        self.session_id: str = raw.get("session_id", "")
        self.model: str = raw.get("model", "")
        # result fields
        self.result_text: str = raw.get("result", "")
        self.is_error: bool = raw.get("is_error", False)
        self.duration_ms: float = raw.get("duration_ms", 0)
        self.num_turns: int = raw.get("num_turns", 0)
        self.usage: dict[str, Any] | None = raw.get("usage")
        self.model_usage: dict[str, Any] | None = raw.get("modelUsage")
        # control request
        self.request_id: str = raw.get("request_id", "")
        self.request: dict[str, Any] | None = raw.get("request")
        # stream_event fields (--include-partial-messages)
        self.event: dict[str, Any] | None = raw.get("event")
        self.parent_tool_use_id: str = raw.get("parent_tool_use_id", "")


# ─── the adapter ───────────────────────────────────────────────


class ClaudeCLIAdapter(CLIAdapterBase):
    """Spawn ``claude`` CLI with stream-json protocol, translate to StreamEvent."""

    def __init__(
        self,
        executable_path: str = "claude",
        extra_env: dict[str, str] | None = None,
    ) -> None:
        super().__init__(executable_path, extra_env)
        self._system_prompt_file: str | None = None
        self._mcp_config_file: str | None = None
        self._mcp_config: dict[str, Any] | None = None

    @property
    def name(self) -> AdapterName:
        return "claude-code"

    # ── template method override: resume retry ───────────────────

    async def stream(
        self, input: AdapterInput, cancel_event: asyncio.Event
    ) -> AsyncIterator[StreamEvent]:
        """Stream events, retrying without --resume if session resume fails."""
        if not input.resume_session_id:
            async for event in super().stream(input, cancel_event):
                yield event
            return

        try:
            async for event in super().stream(input, cancel_event):
                yield event
            return
        except Exception as exc:
            if cancel_event.is_set():
                raise
            logger.warning(
                "[claude] resume session failed (%s); retrying with fresh session", exc
            )
            clear_claude_code_session(input.conversation_id)
            fresh_input = dc_replace(input, resume_session_id=None)
            async for event in super().stream(fresh_input, cancel_event):
                yield event

    # ── CLIAdapterBase hooks ─────────────────────────────────────

    def _build_args(self, input: AdapterInput) -> list[str]:
        args = [
            "-p",
            "--output-format", "stream-json",
            "--input-format", "stream-json",
            "--verbose",
            "--permission-mode", "acceptEdits",
            # Include partial message chunks as the model generates them
            # (token-by-token streaming, like OpenAI's stream=True).
            # Without this flag, Claude CLI accumulates the full response
            # before emitting a single assistant event.
            "--include-partial-messages",
            # Prevent Claude Code from invoking the interactive AskUserQuestion
            # tool. In non-interactive/daemon mode there is no UI to render the
            # prompt, so a call returns an empty answer and the agent infers
            # silently. (mirrors multica claude.go:576)
            "--disallowedTools", "AskUserQuestion",
        ]
        if input.model_id:
            args.extend(("--model", input.model_id))
        if input.resume_session_id:
            args.extend(("--resume", input.resume_session_id))
        # Claude prefixes tools exposed by the AChat MCP Bridge.
        _sp_content = (input.system_prompt or "") + _build_mcp_tool_hint(input.tool_names)
        # Windows command-line cannot carry newlines; write to temp file.
        # Both --system-prompt[-file] and --append-system-prompt[-file]
        # variants exist in the Claude Code CLI.
        self._system_prompt_file = _write_temp_system_prompt(_sp_content)
        args.extend(("--append-system-prompt-file", self._system_prompt_file))

        # Expose AChat project tools (write_artifact, etc.) to Claude CLI
        # via an MCP server. The CLI spawns the server as a subprocess;
        # the server translates MCP tool calls to AChat ToolRegistry calls.
        self._mcp_config_file = _write_mcp_config(
            input.conversation_id,
            input.run_id,
            input.workspace_path or "",
            input.agent_id,
            input.user_id,
            tool_names=input.tool_names,
        )
        if self._mcp_config_file:
            # Use = format to avoid any argument parsing ambiguity on Windows.
            args.append(f"--mcp-config={self._mcp_config_file}")

        # Append user custom args (blocked flags already filtered)
        custom = input.custom_args or []
        custom = filter_custom_args(custom, _claude_blocked_args)
        args.extend(custom)
        return args

    async def _write_prompt(
        self, proc: asyncio.subprocess.Process, input: AdapterInput
    ) -> None:
        if not proc.stdin:
            raise RuntimeError("claude stdin pipe not available")

        content: list[dict[str, Any]] = [{"type": "text", "text": input.prompt}]
        file_notes: list[str] = []

        for att in (input.attachments or []):
            if att.kind == "image":
                try:
                    with open(att.abs_path, "rb") as f:
                        raw = f.read()
                except OSError as exc:
                    raise RuntimeError(
                        f"Failed to read image attachment '{att.file_name}': {exc}"
                    ) from exc
                if len(raw) > MAX_IMAGE_SIZE_BYTES:
                    raise RuntimeError(
                        f"Image attachment '{att.file_name}' exceeds 10 MB limit "
                        f"({len(raw)} bytes)"
                    )
                b64 = base64.b64encode(raw).decode("ascii")
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": att.mime_type or "image/png",
                        "data": b64,
                    },
                })
            else:
                file_notes.append(
                    f"[Attached file: {att.file_name} ({att.mime_type}) at {att.abs_path}]"
                )

        prompt_text = input.prompt
        if file_notes:
            prompt_text = prompt_text + "\n\n" + "\n".join(file_notes)
            content[0] = {"type": "text", "text": prompt_text}

        payload = json.dumps({
            "type": "user",
            "message": {
                "role": "user",
                "content": content,
            },
        })
        proc.stdin.write((payload + "\n").encode())
        await proc.stdin.drain()
        logger.info("[claude] prompt written to stdin (%d chars)", len(payload))
        # Keep stdin open — Claude may send control_request mid-run and
        # expects control_response frames on the same input stream.

    async def _read_events(
        self,
        proc: asyncio.subprocess.Process,
        input: AdapterInput,
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[StreamEvent]:
        if not proc.stdout:
            raise RuntimeError("claude stdout pipe not available")

        # Watchdog: close stdout when cancelled so readline() unblocks.
        async def _close_stdout_on_cancel() -> None:
            await cancel_event.wait()
            if proc.stdout and not proc.stdout.at_eof():
                try:
                    proc.stdout.feed_eof()
                except Exception:
                    pass

        cancel_watchdog = asyncio.create_task(_close_stdout_on_cancel())

        # Accumulate stderr in background for post-mortem diagnostics.
        stderr_chunks: list[str] = []
        stderr_task = asyncio.create_task(
            self._drain_stderr(proc, "[claude:stderr]", stderr_chunks)
        )

        # Per-run mutable state
        session_id = ""
        model_id = input.model_id or DEFAULT_CLAUDE_MODEL
        run_input_tokens = 0
        run_output_tokens = 0
        run_cache_read = 0
        run_cache_write = 0
        last_input_tokens = 0
        last_cache_read_tokens = 0
        last_output_tokens = 0
        any_event = False  # track whether we received any meaningful output
        result_is_error = False  # track error flag from the result event
        final_status = "complete"  # timeout / complete / aborted

        message_id = ""
        text_part_index = -1
        thinking_part_index = -1
        next_part_index = 0
        in_message = False

        # per-content-block tracking for stream_event deltas
        _blk_type: str = ""          # "text" | "thinking" | "tool_use"
        _blk_index: int = -1         # content block index
        _tool_name: str = ""         # tool name (for tool_use blocks)
        _tool_id: str = ""           # tool call id
        _tool_input_buf: str = ""    # accumulated JSON input deltas
        _streamed: bool = False      # True once we see a stream_event

        # Timeout watchdog tracking (mirrors Codex adapter)
        last_semantic_activity = time.monotonic()
        first_turn_started = False
        first_turn_progress = False
        timed_out = False

        t_spawn = time.monotonic()
        logger.info("[claude] reading events from stdout (t=%.3fs)...", 0.0)
        try:
            while not cancel_event.is_set():
                # Read next line with a short timeout so we can check
                # cancel_event and semantic inactivity periodically.
                try:
                    line_raw = await asyncio.wait_for(
                        proc.stdout.readline(), timeout=5.0
                    )
                except TimeoutError:
                    elapsed = time.monotonic() - last_semantic_activity
                    if first_turn_started and not first_turn_progress:
                        if elapsed > DEFAULT_FIRST_TURN_NO_PROGRESS_TIMEOUT:
                            logger.warning(
                                "[claude] first turn no progress timeout (%.1fs)",
                                elapsed,
                            )
                            final_status = "timeout"
                            timed_out = True
                            break
                    if elapsed > DEFAULT_SEMANTIC_INACTIVITY_TIMEOUT:
                        logger.warning(
                            "[claude] semantic inactivity timeout (%.1fs)",
                            elapsed,
                        )
                        final_status = "timeout"
                        timed_out = True
                        break
                    continue

                if not line_raw:
                    break  # EOF

                line = line_raw.decode("utf-8", errors="replace").strip()
                if not line:
                    continue

                t_line = time.monotonic() - t_spawn

                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    # Non-JSON lines are likely stderr output mixed into the
                    # ConPTY stream (or CLI startup banners). Log them so
                    # they're visible for diagnostics.
                    logger.debug("[claude:stderr] %s", line[:500])
                    continue

                msg = _ClaudeSDKMessage(raw)

                if not any_event:
                    logger.info("[claude] first event: type=%s (t=%.3fs)", msg.type, t_line)
                any_event = True
                last_semantic_activity = time.monotonic()

                # Capture session id from any event that carries it
                if msg.session_id:
                    session_id = msg.session_id

                if msg.type == "system":
                    # system/init and similar events carry session metadata
                    # (cwd, tools, permissions, etc.) — not user-facing content.
                    # Silently capture session_id and skip; do NOT create a message.
                    pass

                elif msg.type == "stream_event":
                    # --include-partial-messages: raw Anthropic streaming events
                    # delivered token-by-token. This is the primary streaming path.
                    event = msg.event
                    if not event:
                        continue
                    _streamed = True
                    etype = event.get("type", "")

                    if etype == "message_start":
                        if not in_message:
                            in_message = True
                            first_turn_started = True
                            message_id = new_message_id()
                            text_part_index = -1
                            thinking_part_index = -1
                            next_part_index = 0
                            _blk_type = ""
                            _blk_index = -1
                            _tool_name = ""
                            _tool_id = ""
                            _tool_input_buf = ""
                            yield MessageStartEvent(
                                conversation_id=input.conversation_id,
                                timestamp=now_ms(),
                                message_id=message_id,
                                agent_id=input.agent_id,
                                run_id=input.run_id,
                            )
                        # Capture model from the message_start event
                        msg_obj = event.get("message", {})
                        if msg_obj.get("model"):
                            model_id = msg_obj["model"]

                    elif etype == "content_block_start":
                        blk = event.get("content_block", {})
                        _blk_type = blk.get("type", "")
                        _blk_index = blk.get("index", -1)
                        if _blk_type == "text":
                            text_part_index = next_part_index
                            next_part_index += 1
                            yield PartStartEvent(
                                conversation_id=input.conversation_id,
                                timestamp=now_ms(),
                                message_id=message_id,
                                part_index=text_part_index,
                                part={"type": "text", "content": ""},
                            )
                        elif _blk_type == "thinking":
                            thinking_part_index = next_part_index
                            next_part_index += 1
                            yield PartStartEvent(
                                conversation_id=input.conversation_id,
                                timestamp=now_ms(),
                                message_id=message_id,
                                part_index=thinking_part_index,
                                part={"type": "thinking", "content": ""},
                            )
                        elif _blk_type == "tool_use":
                            _tool_name = blk.get("name", "")
                            _tool_id = blk.get("id", "")
                            _tool_input_buf = ""

                    elif etype == "content_block_delta":
                        delta = event.get("delta", {})
                        dtype = delta.get("type", "")
                        if dtype == "text_delta":
                            text = delta.get("text", "")
                            if text:
                                first_turn_progress = True
                                yield PartDeltaEvent(
                                    conversation_id=input.conversation_id,
                                    timestamp=now_ms(),
                                    message_id=message_id,
                                    part_index=text_part_index,
                                    delta={"type": "text.append", "text": text},
                                )
                        elif dtype == "thinking_delta":
                            text = delta.get("thinking", "")
                            if text:
                                yield PartDeltaEvent(
                                    conversation_id=input.conversation_id,
                                    timestamp=now_ms(),
                                    message_id=message_id,
                                    part_index=thinking_part_index,
                                    delta={"type": "thinking.append", "text": text},
                                )
                        elif dtype == "input_json_delta":
                            _tool_input_buf += delta.get("partial_json", "")

                    elif etype == "content_block_stop":
                        if _blk_type == "text" and text_part_index >= 0:
                            yield PartEndEvent(
                                conversation_id=input.conversation_id,
                                timestamp=now_ms(),
                                message_id=message_id,
                                part_index=text_part_index,
                            )
                        elif _blk_type == "thinking" and thinking_part_index >= 0:
                            yield PartEndEvent(
                                conversation_id=input.conversation_id,
                                timestamp=now_ms(),
                                message_id=message_id,
                                part_index=thinking_part_index,
                            )
                        elif _blk_type == "tool_use" and _tool_id:
                            # Try to parse accumulated JSON input
                            tool_input: dict[str, Any] = {}
                            if _tool_input_buf:
                                try:
                                    tool_input = json.loads(_tool_input_buf)
                                except (json.JSONDecodeError, TypeError):
                                    tool_input = {}
                            yield ToolCallEvent(
                                conversation_id=input.conversation_id,
                                timestamp=now_ms(),
                                message_id=message_id,
                                call_id=_tool_id,
                                tool_name=_tool_name,
                                args=tool_input,
                            )
                        _blk_type = ""
                        _blk_index = -1

                    elif etype in ("message_delta", "message_stop", "ping"):
                        # message_delta: carries stop_reason, usage
                        # message_stop: end-of-message signal
                        # ping: keep-alive, ignore
                        pass

                elif msg.type == "assistant":
                    if not in_message:
                        in_message = True
                        message_id = new_message_id()
                        text_part_index = -1
                        thinking_part_index = -1
                        next_part_index = 0
                        _streamed = False
                        yield MessageStartEvent(
                            conversation_id=input.conversation_id,
                            timestamp=now_ms(),
                            message_id=message_id,
                            agent_id=input.agent_id,
                            run_id=input.run_id,
                        )

                    if msg.message:
                        content = msg.message.get("content", [])
                        usage = msg.message.get("usage")
                        if usage:
                            u_input = usage.get("input_tokens", 0)
                            u_output = usage.get("output_tokens", 0)
                            u_cache_read = usage.get("cache_read_input_tokens", 0)
                            u_cache_write = usage.get("cache_creation_input_tokens", 0)
                            run_input_tokens += u_input
                            run_output_tokens += u_output
                            run_cache_read += u_cache_read
                            run_cache_write += u_cache_write
                            if u_input:
                                last_input_tokens = u_input
                                last_cache_read_tokens = u_cache_read
                                last_output_tokens = u_output

                        for block in content:
                            btype = block.get("type", "")

                            if btype == "text":
                                # When --include-partial-messages is active the
                                # text content has already been streamed token-by-
                                # token via stream_event; skip it here to avoid
                                # duplicate events.
                                if _streamed:
                                    continue
                                text = block.get("text", "")
                                if text:
                                    first_turn_progress = True
                                    if text_part_index < 0:
                                        text_part_index = next_part_index
                                        next_part_index += 1
                                        yield PartStartEvent(
                                            conversation_id=input.conversation_id,
                                            timestamp=now_ms(),
                                            message_id=message_id,
                                            part_index=text_part_index,
                                            part={"type": "text", "content": ""},
                                        )
                                    yield PartDeltaEvent(
                                        conversation_id=input.conversation_id,
                                        timestamp=now_ms(),
                                        message_id=message_id,
                                        part_index=text_part_index,
                                        delta={"type": "text.append", "text": text},
                                    )

                            elif btype == "thinking":
                                if _streamed:
                                    continue
                                text = block.get("text", "")
                                if text:
                                    if thinking_part_index < 0:
                                        thinking_part_index = next_part_index
                                        next_part_index += 1
                                        yield PartStartEvent(
                                            conversation_id=input.conversation_id,
                                            timestamp=now_ms(),
                                            message_id=message_id,
                                            part_index=thinking_part_index,
                                            part={"type": "thinking", "content": ""},
                                        )
                                    yield PartDeltaEvent(
                                        conversation_id=input.conversation_id,
                                        timestamp=now_ms(),
                                        message_id=message_id,
                                        part_index=thinking_part_index,
                                        delta={"type": "thinking.append", "text": text},
                                    )

                            elif btype == "tool_use":
                                tool_input = block.get("input")
                                if isinstance(tool_input, str):
                                    try:
                                        tool_input = json.loads(tool_input)
                                    except (json.JSONDecodeError, TypeError):
                                        tool_input = {}
                                if not isinstance(tool_input, dict):
                                    tool_input = {}
                                yield ToolCallEvent(
                                    conversation_id=input.conversation_id,
                                    timestamp=now_ms(),
                                    message_id=message_id,
                                    call_id=block.get("id", ""),
                                    tool_name=block.get("name", ""),
                                    args=tool_input,
                                )

                elif msg.type == "user":
                    if msg.message:
                        for block in msg.message.get("content", []):
                            if block.get("type") == "tool_result":
                                result_content = block.get("content", "")
                                # MCP tools return content in the format:
                                #   [{"type":"text","text":"<actual_result>"}]
                                # Extract the text from MCP content blocks.
                                if isinstance(result_content, list):
                                    texts = []
                                    for item in result_content:
                                        if isinstance(item, dict) and item.get("type") == "text":
                                            texts.append(item.get("text", ""))
                                    if texts:
                                        result_content = "\n".join(texts)
                                    else:
                                        result_content = json.dumps(result_content)
                                elif isinstance(result_content, dict):
                                    result_content = json.dumps(result_content)
                                yield ToolResultEvent(
                                    conversation_id=input.conversation_id,
                                    timestamp=now_ms(),
                                    message_id=message_id,
                                    call_id=block.get("tool_use_id", ""),
                                    result=result_content,
                                    is_error=block.get("is_error", False),
                                )

                elif msg.type == "result":
                    t_result = time.monotonic() - t_spawn
                    logger.info("[claude] result event (t=%.3fs)", t_result)
                    if msg.session_id:
                        session_id = msg.session_id
                    result_is_error = msg.is_error
                    if msg.is_error:
                        stderr_tail = "".join(stderr_chunks)[-2000:]
                        raise RuntimeError(
                            f"claude reported error: {msg.result_text}\n\n"
                            f"[claude stderr tail]\n{stderr_tail}"
                        )

                    if msg.model_usage:
                        for _model, u in msg.model_usage.items():
                            run_input_tokens += u.get("inputTokens", 0)
                            run_output_tokens += u.get("outputTokens", 0)
                            run_cache_read += u.get("cacheReadInputTokens", 0)
                            run_cache_write += u.get("cacheCreationInputTokens", 0)
                    elif msg.usage:
                        run_input_tokens += msg.usage.get("input_tokens", 0)
                        run_output_tokens += msg.usage.get("output_tokens", 0)
                        run_cache_read += msg.usage.get("cache_read_input_tokens", 0)
                        run_cache_write += msg.usage.get("cache_creation_input_tokens", 0)

                    # Signal EOF to the CLI so it can exit cleanly instead
                    # of waiting for more stdin (avoids 5 s timeout on Windows).
                    if proc.stdin and not proc.stdin.is_closing():
                        proc.stdin.close()
                    break  # result is the terminal event

                elif msg.type == "control_request":
                    await self._handle_control_request(proc, msg, input, cancel_event)

                elif msg.type == "log":
                    pass

            # If we broke out due to timeout, close stdin to unblock the CLI.
            if timed_out and proc.stdin and not proc.stdin.is_closing():
                proc.stdin.close()

        except asyncio.CancelledError:
            cancel_event.set()
            stderr_task.cancel()
            raise

        finally:
            # Clean up the cancel watchdog only.  stderr_task is finalized
            # in the post-loop block below so we can inspect its content.
            cancel_watchdog.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await cancel_watchdog
            # Remove temp files
            if self._system_prompt_file:
                _remove_temp_file(self._system_prompt_file)
                self._system_prompt_file = None
            if self._mcp_config_file:
                _remove_temp_file(self._mcp_config_file)
                self._mcp_config_file = None

        # ── post-loop: check exit code ─────────────────────────────
        logger.info("[claude] event loop ended, any_event=%s, cancel=%s",
                    any_event, cancel_event.is_set())
        if not cancel_event.is_set():
            # Wait briefly for process to flush and exit (it should be done by now)
            try:
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except TimeoutError:
                logger.warning("[claude] process did not exit within 5s, terminating")
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=3.0)
                except TimeoutError:
                    logger.warning("[claude] process did not respond to terminate, killing")
                    proc.kill()
                    await proc.wait()

            # Drain stderr now that the process has exited.  (Moved here
            # from the finally block so that stderr content is available
            # for diagnostics below.)
            try:
                await asyncio.wait_for(stderr_task, timeout=2.0)
            except (TimeoutError, asyncio.CancelledError):
                pass

            exit_code = proc.returncode
            logger.info("[claude] process exit_code=%s, result_is_error=%s",
                        exit_code, result_is_error)

            if not any_event:
                stderr_tail = "".join(stderr_chunks)[-2000:]
                raise RuntimeError(
                    "claude exited without producing any output"
                    + (f"\n\n[claude stderr tail]\n{stderr_tail}" if stderr_tail else "")
                )

            # A non-zero exit code after a successful result event is
            # non-fatal — Claude Code CLI on Windows is known to exit
            # with code 1 even on successful runs.  Only treat it as
            # fatal when the result event itself signalled an error.
            if exit_code is not None and exit_code != 0:
                stderr_tail = "".join(stderr_chunks)[-2000:]
                if result_is_error:
                    raise RuntimeError(
                        f"claude reported an error and exited with code {exit_code}"
                        + (f"\n\n[claude stderr tail]\n{stderr_tail}" if stderr_tail else "")
                    )
                else:
                    logger.warning(
                        "[claude] process exited with code %d after a successful "
                        "result event — ignoring (known Windows behaviour). "
                        "stderr tail: %s",
                        exit_code, stderr_tail or "(empty)",
                    )

        # ── drain remaining output parts ──────────────────────────
        if in_message and message_id:
            # With --include-partial-messages, PartEnd events are already
            # emitted by the stream_event → content_block_stop handler.
            if not _streamed:
                if text_part_index >= 0:
                    yield PartEndEvent(
                        conversation_id=input.conversation_id,
                        timestamp=now_ms(),
                        message_id=message_id,
                        part_index=text_part_index,
                    )
                if thinking_part_index >= 0:
                    yield PartEndEvent(
                        conversation_id=input.conversation_id,
                        timestamp=now_ms(),
                        message_id=message_id,
                        part_index=thinking_part_index,
                    )
            msg_usage = MessageUsage(
                input_tokens=last_input_tokens,
                output_tokens=run_output_tokens,
                cache_read_tokens=run_cache_read,
            )
            if msg_usage.input_tokens or msg_usage.output_tokens:
                yield MessageUsageEventPayload(
                    conversation_id=input.conversation_id,
                    timestamp=now_ms(),
                    message_id=message_id,
                    usage=msg_usage,
                )
            yield MessageEndEvent(
                conversation_id=input.conversation_id,
                timestamp=now_ms(),
                message_id=message_id,
            )

        # ── emit run usage ─────────────────────────────────────────
        run_usage = RunUsage(
            model=model_id,
            input_tokens=run_input_tokens,
            output_tokens=run_output_tokens,
            cache_read_tokens=run_cache_read,
            cache_creation_tokens=run_cache_write,
            last_input_tokens=last_input_tokens,
            last_cache_read_tokens=last_cache_read_tokens,
            last_output_tokens=last_output_tokens,
        )
        yield RunUsageEvent(
            conversation_id=input.conversation_id,
            timestamp=now_ms(),
            run_id=input.run_id,
            usage=run_usage,
            session_id=session_id or None,
        )

        logger.info(
            "[claude] run finished: session=%s input_tokens=%d output_tokens=%d",
            session_id,
            run_input_tokens,
            run_output_tokens,
        )

    # ── control_request handler (smart approval) ──────────────────

    async def _handle_control_request(
        self,
        proc: asyncio.subprocess.Process,
        msg: _ClaudeSDKMessage,
        input: AdapterInput,
        cancel_event: asyncio.Event,
    ) -> None:
        """Route a control_request through AChat's security infrastructure.

        Parses the tool name and input from the request, then:
        - Bash  → find_banned_pattern + classify_bash_approval + wait_for_bash_approval
        - Write/Edit → resolve_safe_path + pending_writes (review mode)
        - Malformed (no tool_name) → deny + warning event
        - Other → allow (read-only tools auto-approved by acceptEdits)

        Emits BashCommandResolvedEvent / FsWriteResolvedEvent for immediate
        denials so the frontend has visibility into blocked operations.
        """
        req = msg.request or {}
        # Spike logging: record the raw request dict so the exact field
        # names can be verified at runtime.
        logger.info(
            "[claude] control_request raw: request_id=%s request=%s",
            msg.request_id,
            json.dumps(req, ensure_ascii=False)[:1000],
        )

        # Defensive parsing: try multiple possible field names.
        tool_name = (
            req.get("tool_name")
            or req.get("toolName")
            or req.get("name")
            or ""
        )
        tool_input = req.get("input") or req.get("parameters") or req.get("args") or {}
        if not isinstance(tool_input, dict):
            tool_input = {}

        # Malformed control_request: no recognisable tool name.
        if not tool_name:
            logger.warning(
                "[claude] malformed control_request (no tool_name): request_id=%s",
                msg.request_id,
            )
            await self._deny(proc, msg, "Malformed control_request: no tool name found")
            return

        tool_lower = tool_name.lower()

        # ── Bash command routing ──
        if tool_lower == "bash":
            command = str(tool_input.get("command", ""))
            if command:
                from app.utils.security import find_banned_pattern
                banned = find_banned_pattern(command, _PLATFORM)
                if banned:
                    logger.warning(
                        "[claude] denying bash (blacklisted): %s", banned
                    )
                    await self._deny(proc, msg, f"Command rejected by safety policy: {banned}")
                    self._emit_approval_event(
                        input, msg.request_id, approved=False,
                        reason=f"Banned: {banned}", tool_name="bash",
                    )
                    return

                from app.services.bash_command_approval import (
                    classify_bash_approval,
                    wait_for_bash_approval,
                )
                approval = classify_bash_approval(command, _PLATFORM)
                if approval.required:
                    logger.info("[claude] bash requires approval: %s", approval.reason)
                    approved = await wait_for_bash_approval(
                        conversation_id=input.conversation_id,
                        agent_id=input.agent_id,
                        run_id=input.run_id,
                        command=command,
                        cwd=input.workspace_path or "",
                        reason=approval.reason,
                        cancel_event=cancel_event,
                        user_id=input.user_id,
                    )
                    if not approved:
                        await self._deny(proc, msg, f"User rejected command: {approval.reason}")
                        self._emit_approval_event(
                            input, msg.request_id, approved=False,
                            reason=f"User rejected: {approval.reason}", tool_name="bash",
                        )
                        return
            await self._allow(proc, msg)
            return

        # ── Write / Edit file routing ──
        if tool_lower in ("write", "edit", "multiedit"):
            file_path = str(tool_input.get("file_path") or tool_input.get("path") or "")
            if file_path:
                from app.infra.cache_helpers import get_workspace_cached
                from app.utils.workspace_utils import resolve_safe_path
                workspace = await get_workspace_cached(input.conversation_id)
                if workspace is not None:
                    safe_path = resolve_safe_path(workspace, file_path)
                    if safe_path is None:
                        logger.warning(
                            "[claude] denying write (path outside workspace): %s",
                            file_path,
                        )
                        await self._deny(
                            proc, msg, f'Path "{file_path}" is outside workspace'
                        )
                        self._emit_write_approval_event(
                            input, msg.request_id, applied=False,
                            reason=f'Path outside workspace: {file_path}',
                        )
                        return

                    # Check conversation's fs_write_approval_mode
                    from sqlalchemy import select as sa_select
                    from app.db.engine import get_local_db
                    from app.db.models import Conversation
                    try:
                        async with get_local_db() as db:
                            conv = (
                                await db.execute(
                                    sa_select(Conversation.fs_write_approval_mode).where(
                                        Conversation.id == input.conversation_id
                                    )
                                )
                            ).scalar_one_or_none()
                        mode = conv or "review"
                    except Exception:
                        mode = "review"

                    if mode == "review":
                        from app.services.pending_writes import pending_writes
                        from app.utils.approval import await_pending_decision
                        new_content = str(tool_input.get("content") or tool_input.get("new_string") or "")
                        write = pending_writes.register(
                            conversation_id=input.conversation_id,
                            agent_id=input.agent_id,
                            run_id=input.run_id,
                            path=file_path,
                            absolute_path=safe_path,
                            old_content=None,
                            new_content=new_content,
                            workspace=workspace,
                            user_id=input.user_id,
                        )
                        decision = await await_pending_decision(
                            attach_resolver=lambda r: pending_writes.attach_resolver(write.id, r),
                            cancel=lambda: pending_writes.cancel(write.id),
                            cancel_event=cancel_event,
                            cancelled_value={"applied": False},
                        )
                        applied = isinstance(decision, dict) and decision.get("applied", False)
                        if not applied:
                            await self._deny(proc, msg, "User rejected file write")
                            self._emit_write_approval_event(
                                input, msg.request_id, applied=False,
                                reason="User rejected file write",
                            )
                            return
            await self._allow(proc, msg)
            return

        # ── Default: allow (read-only tools, MCP tools, etc.) ──
        await self._allow(proc, msg)

    async def _allow(
        self, proc: asyncio.subprocess.Process, msg: _ClaudeSDKMessage
    ) -> None:
        """Respond ``allow`` to a ``control_request``."""
        if not proc.stdin or proc.stdin.is_closing():
            return
        response = {
            "type": "control_response",
            "response": {
                "subtype": "success",
                "request_id": msg.request_id,
                "response": {"behavior": "allow", "updatedInput": {}},
            },
        }
        try:
            proc.stdin.write((json.dumps(response) + "\n").encode())
            await proc.stdin.drain()
        except Exception:
            pass

    async def _deny(
        self, proc: asyncio.subprocess.Process, msg: _ClaudeSDKMessage, reason: str
    ) -> None:
        """Respond ``deny`` to a ``control_request``."""
        if not proc.stdin or proc.stdin.is_closing():
            return
        response = {
            "type": "control_response",
            "response": {
                "subtype": "success",
                "request_id": msg.request_id,
                "response": {"behavior": "deny", "message": reason},
            },
        }
        try:
            proc.stdin.write((json.dumps(response) + "\n").encode())
            await proc.stdin.drain()
        except Exception:
            pass

    def _emit_approval_event(
        self,
        input: AdapterInput,
        request_id: str,
        *,
        approved: bool,
        reason: str,
        tool_name: str,
    ) -> None:
        """Emit a BashCommandResolvedEvent for a control_request denial.

        Uses event_bus so the frontend has visibility into denied bash
        commands even when no pending approval flow was created (e.g.
        immediate blacklist denial).
        """
        from app.services.event_bus import event_bus

        event_bus.publish(
            BashCommandResolvedEvent(
                conversation_id=input.conversation_id,
                timestamp=now_ms(),
                pending_id=f"deny_{request_id}",
                approved=approved,
            ),
            user_id=input.user_id,
        )

    def _emit_write_approval_event(
        self,
        input: AdapterInput,
        request_id: str,
        *,
        applied: bool,
        reason: str,
    ) -> None:
        """Emit a FsWriteResolvedEvent for a control_request denial.

        Uses event_bus so the frontend has visibility into denied file
        writes even when no pending approval flow was created (e.g.
        immediate path violation).
        """
        from app.services.event_bus import event_bus

        event_bus.publish(
            FsWriteResolvedEvent(
                conversation_id=input.conversation_id,
                timestamp=now_ms(),
                pending_id=f"deny_{request_id}",
                applied=applied,
            ),
            user_id=input.user_id,
        )

    async def _drain_stderr(
        self,
        proc: asyncio.subprocess.Process,
        prefix: str,
        chunks: list[str] | None = None,
    ) -> None:
        """Read stderr lines and log them (best-effort).

        If ``chunks`` is provided, each decoded line is appended so the
        caller can inspect the tail after the run finishes.
        """
        if not proc.stderr:
            return
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    logger.info("%s %s", prefix, text)
                    if chunks is not None:
                        chunks.append(text)
        except Exception:
            pass


# ─── temp file helpers ─────────────────────────────────────────


def _write_temp_system_prompt(system_prompt: str) -> str:
    """Write the system prompt to a temp file for ``--append-system-prompt-file``.

    Windows ``CreateProcess`` command lines cannot carry embedded newlines;
    writing to a temp file and passing the path avoids truncation.
    """
    fd, path = tempfile.mkstemp(prefix="agenthub_sp_", suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(system_prompt)
    return path


def _remove_temp_file(path: str) -> None:
    """Remove a temp file, swallowing any error."""
    with contextlib.suppress(OSError):
        os.remove(path)


# ─── MCP config builder ───────────────────────────────────────────

def _write_mcp_config(
    conversation_id: str,
    run_id: str,
    workspace_path: str,
    agent_id: str,
    user_id: str | None = None,
    tool_names: list[str] | None = None,
) -> str | None:
    """Write the Claude CLI MCP config JSON file.

    Tells Claude CLI how to spawn the AChat MCP Bridge (a stdio-based MCP
    server that exposes AChat platform tools like ``write_artifact`` /
    ``read_artifact`` and ``ask_user``).

    Returns the path to the temp config file, or ``None`` if the bridge
    module cannot be located.
    """
    # Find the backend directory so the MCP server can import app.mcp_bridge.
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # backend_dir is .../backend/app/adapters → we need .../backend
    backend_root = os.path.dirname(backend_dir)  # .../backend

    # The MCP server script is at backend/app/mcp_bridge.py.
    # We launch it as: python -m app.mcp_bridge ...
    # For that to work, backend_root must be on PYTHONPATH.
    python_exe = sys.executable

    bridge_args = [
        "-m", "app.mcp_bridge",
        "--conversation-id", conversation_id,
        "--run-id", run_id,
        "--workspace-path", workspace_path,
        "--agent-id", agent_id,
    ]
    if user_id:
        bridge_args.extend(["--user-id", user_id])
    if tool_names:
        bridge_args.extend(["--tool-names", ",".join(tool_names)])

    mcp_config = {
        "mcpServers": {
            "achat-tools": {
                "type": "stdio",
                "command": python_exe,
                "args": bridge_args,
                "env": {
                    "PYTHONPATH": backend_root,
                    "PYTHONUNBUFFERED": "1",
                    "DATABASE_URL": os.environ.get("DATABASE_URL", ""),
                    **({"DATABASE_LOCAL_URL": os.environ["DATABASE_LOCAL_URL"]}
                       if os.environ.get("DATABASE_LOCAL_URL") else {}),
                },
            }
        }
    }
    logger.info("[claude] MCP config: %s", json.dumps(mcp_config, indent=2))

    fd, path = tempfile.mkstemp(prefix="agenthub_mcp_", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(mcp_config, f, indent=2)
    logger.info("[claude] wrote MCP config to %s", path)
    return path


# ─── legacy alias ───

ClaudeAdapter = ClaudeCLIAdapter
