"""ClaudeCLIAdapter — spawn ``claude`` CLI, stream-json protocol.

Port of multica's ``server/pkg/agent/claude.go`` (claudeBackend). Communicates
with the Claude Code CLI via stream-json over stdin/stdout; the CLI manages its
own tool execution and session lifecycle. The adapter translates CLI events into
AChat StreamEvent objects.

Protocol reference: Claude Code ``--output-format stream-json`` manual.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import tempfile
from collections.abc import AsyncIterator
from typing import Any

from app.adapters.base import AdapterInput, AdapterName
from app.adapters.cli_base import BlockedArgMode, CLIAdapterBase, filter_custom_args
from app.schemas.events import (
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

logger = logging.getLogger(__name__)

# ─── blocked args (mirrors multica claudeBlockedArgs) ──────────

_claude_blocked_args: dict[str, BlockedArgMode] = {
    "-p": BlockedArgMode.STANDALONE,
    "--output-format": BlockedArgMode.WITH_VALUE,
    "--input-format": BlockedArgMode.WITH_VALUE,
    "--permission-mode": BlockedArgMode.WITH_VALUE,
    "--mcp-config": BlockedArgMode.WITH_VALUE,
    "--effort": BlockedArgMode.WITH_VALUE,
}

DEFAULT_CLAUDE_MODEL = "claude-sonnet-4-5"


# ─── stream-json type defs ─────────────────────────────────────


class _ClaudeSDKMessage:
    """One JSON line from Claude Code stream-json stdout."""

    __slots__ = (
        "type", "message", "subtype", "session_id", "model",
        "result_text", "is_error", "duration_ms", "num_turns",
        "usage", "model_usage", "request_id", "request",
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
        self._empty_plugin_dir: str | None = None

    @property
    def name(self) -> AdapterName:
        return "claude-code"

    # ── CLIAdapterBase hooks ─────────────────────────────────────

    def _build_args(self, input: AdapterInput) -> list[str]:
        args = [
            "-p",
            "--output-format", "stream-json",
            "--input-format", "stream-json",
            "--verbose",
            "--strict-mcp-config",
            "--permission-mode", "bypassPermissions",
            # Prevent Claude Code from invoking the interactive AskUserQuestion
            # tool. In non-interactive/daemon mode there is no UI to render the
            # prompt, so a call returns an empty answer and the agent infers
            # silently. (mirrors multica claude.go:576)
            "--disallowedTools", "AskUserQuestion",
        ]
        # Point plugin-dir at an empty temp directory so that Claude Code
        # starts without any third-party plugins (superpowers, etc.).
        # CLAUDE.md auto-discovery and OAuth login are unaffected.
        self._empty_plugin_dir = tempfile.mkdtemp(prefix="agenthub_no_plugins_")
        args.extend(("--plugin-dir", self._empty_plugin_dir))
        if input.model_id:
            args.extend(("--model", input.model_id))
        if input.resume_session_id:
            args.extend(("--resume", input.resume_session_id))
        if input.system_prompt:
            # Windows command-line cannot carry newlines; write to temp file.
            # Both --system-prompt[-file] and --append-system-prompt[-file]
            # variants exist in the Claude Code CLI.
            self._system_prompt_file = _write_temp_system_prompt(input.system_prompt)
            args.extend(("--append-system-prompt-file", self._system_prompt_file))
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
        payload = json.dumps({
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": input.prompt}],
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
        output_parts: list[str] = []
        any_event = False  # track whether we received any meaningful output
        result_is_error = False  # track error flag from the result event

        message_id = ""
        text_part_index = -1
        thinking_part_index = -1
        next_part_index = 0
        in_message = False

        logger.info("[claude] reading events from stdout...")
        try:
            async for line_raw in _read_lines(proc.stdout, cancel_event):
                if cancel_event.is_set():
                    break

                line = line_raw.strip()
                if not line:
                    continue

                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg = _ClaudeSDKMessage(raw)

                if not any_event:
                    logger.info("[claude] first event: type=%s", msg.type)
                any_event = True

                # Capture session id from any event that carries it
                if msg.session_id:
                    session_id = msg.session_id

                if msg.type == "system":
                    # system/init and similar events carry session metadata
                    # (cwd, tools, permissions, etc.) — not user-facing content.
                    # Silently capture session_id and skip; do NOT create a message.
                    pass

                elif msg.type == "assistant":
                    if not in_message:
                        in_message = True
                        message_id = new_message_id()
                        text_part_index = -1
                        thinking_part_index = -1
                        next_part_index = 0
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

                        for block in content:
                            btype = block.get("type", "")

                            if btype == "text":
                                text = block.get("text", "")
                                if text:
                                    output_parts.append(text)
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
                                if isinstance(result_content, (dict, list)):
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
                    await self._auto_approve(proc, msg)

                elif msg.type == "log":
                    pass

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
            # Remove temp system prompt file
            if self._system_prompt_file:
                _remove_temp_file(self._system_prompt_file)
                self._system_prompt_file = None
            # Remove empty plugin-dir
            if self._empty_plugin_dir:
                _remove_temp_dir(self._empty_plugin_dir)
                self._empty_plugin_dir = None

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
        )
        yield RunUsageEvent(
            conversation_id=input.conversation_id,
            timestamp=now_ms(),
            run_id=input.run_id,
            usage=run_usage,
        )

        logger.info(
            "[claude] run finished: session=%s input_tokens=%d output_tokens=%d",
            session_id,
            run_input_tokens,
            run_output_tokens,
        )

    # ── helpers ───────────────────────────────────────────────────

    async def _auto_approve(
        self, proc: asyncio.subprocess.Process, msg: _ClaudeSDKMessage
    ) -> None:
        """Respond ``allow`` to a ``control_request`` (autonomous mode)."""
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


# ─── line reader helper ─────────────────────────────────────────


async def _read_lines(
    stream: asyncio.StreamReader,
    cancel_event: asyncio.Event,
) -> AsyncIterator[str]:
    """Read lines from a StreamReader, yielding each non-empty line.

    Stops when the stream is exhausted or ``cancel_event`` is set.
    """
    while not cancel_event.is_set():
        try:
            line = await stream.readline()
        except asyncio.CancelledError:
            return
        if not line:
            return  # EOF
        decoded = line.decode("utf-8", errors="replace")
        if decoded.strip():
            yield decoded


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


def _remove_temp_dir(path: str) -> None:
    """Remove a temp directory recursively, swallowing any error."""
    with contextlib.suppress(OSError):
        shutil.rmtree(path)


# ─── legacy alias ───

ClaudeAdapter = ClaudeCLIAdapter
