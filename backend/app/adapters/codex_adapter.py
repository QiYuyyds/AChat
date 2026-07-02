"""CodexCLIAdapter — spawn ``codex app-server``, JSON-RPC 2.0 protocol.

Port of multica's ``server/pkg/agent/codex.go`` (codexBackend). Communicates
with the Codex CLI via JSON-RPC 2.0 over stdin/stdout (``app-server --listen
stdio://`` mode). The CLI manages its own tool execution, sandbox, and
session lifecycle. The adapter translates JSON-RPC notifications into AChat
StreamEvent objects.

Protocol reference: Codex CLI ``app-server`` mode / JSON-RPC 2.0 spec.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time as _time
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

# ─── timedeltas ──────────────────────────────────────────────────

DEFAULT_SEMANTIC_INACTIVITY_TIMEOUT = 10 * 60  # seconds
DEFAULT_FIRST_TURN_NO_PROGRESS_TIMEOUT = 30  # seconds
# Codex graceful shutdown: wait this long for codex to exit on its own
# after stdin close, then terminate.
CODEX_GRACE_TIMEOUT = 10.0

# ─── blocked args ────────────────────────────────────────────────

_codex_blocked_args: dict[str, BlockedArgMode] = {
    "--listen": BlockedArgMode.WITH_VALUE,
}


# ─── JSON-RPC 2.0 types ──────────────────────────────────────────


class _RPCError(Exception):
    """A JSON-RPC error returned by the server."""


def _next_rpc_id() -> int:
    """Monotonic JSON-RPC id counter."""
    _next_rpc_id._counter += 1  # type: ignore[attr-defined]
    return _next_rpc_id._counter  # type: ignore[attr-defined]


_next_rpc_id._counter = 0  # type: ignore[attr-defined]


# ─── adapter ─────────────────────────────────────────────────────


class CodexCLIAdapter(CLIAdapterBase):
    """Spawn ``codex app-server --listen stdio://``, JSON-RPC 2.0, translate events."""

    def __init__(
        self,
        executable_path: str = "codex",
        extra_env: dict[str, str] | None = None,
    ) -> None:
        super().__init__(executable_path, extra_env)

    @property
    def name(self) -> AdapterName:
        return "codex"

    # ── CLIAdapterBase hooks ─────────────────────────────────────

    def _build_args(self, input: AdapterInput) -> list[str]:
        args = ["app-server", "--listen", "stdio://"]
        custom = input.custom_args or []
        custom = filter_custom_args(custom, _codex_blocked_args)
        args.extend(custom)
        return args

    async def _write_prompt(
        self, proc: asyncio.subprocess.Process, input: AdapterInput
    ) -> None:
        """Perform JSON-RPC handshake and send turn/start."""
        reader = _RPCReader(proc, input, self.name)
        writer = _RPCWriter(proc, input, self.name)

        # 1. Initialize
        await writer.request("initialize", {
            "clientInfo": {
                "name": "agenthub-codex-adapter",
                "title": "AChat Codex Adapter",
                "version": "0.1.0",
            },
            "capabilities": {"experimentalApi": True},
        })
        try:
            await reader.read_response()
        except _RPCError as exc:
            raise RuntimeError(f"codex initialize failed: {exc}") from exc
        writer.notify("initialized")

        # 2. Start or resume thread
        thread_params: dict[str, Any] = {
            "cwd": input.workspace_path or ".",
            "model": input.model_id or None,
            "developerInstructions": input.system_prompt or None,
        }
        if input.resume_session_id:
            thread_params["threadId"] = input.resume_session_id
            await writer.request("thread/resume", thread_params)
        else:
            await writer.request("thread/start", thread_params)
        resp = await reader.read_response()
        thread_id = resp.get("result", {}).get("threadId", "") if resp else ""
        if not thread_id:
            # Fallback: try extracting from error/details
            raise RuntimeError("codex thread/start returned no threadId")
        logger.info("[codex] thread created: %s", thread_id)

        # 3. Start turn
        turn_params: dict[str, Any] = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": input.prompt}],
        }
        await writer.request("turn/start", turn_params)
        # Response is only an ack; real work arrives via notifications.

    async def _read_events(
        self,
        proc: asyncio.subprocess.Process,
        input: AdapterInput,
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[StreamEvent]:
        if not proc.stdout:
            raise RuntimeError("codex stdout pipe not available")

        reader = _RPCReader(proc, input, self.name)

        # Per-run mutable state
        model_id = input.model_id or "codex"
        run_input_tokens = 0
        run_output_tokens = 0
        run_cache_read = 0
        last_input_tokens = 0
        final_status = "completed"
        final_error = ""

        message_id = ""
        text_part_index = -1
        thinking_part_index = -1
        next_part_index = 0
        in_message = False
        output_parts: list[str] = []

        # Timeout tracking
        semantic_timeout = DEFAULT_SEMANTIC_INACTIVITY_TIMEOUT
        first_turn_timeout = DEFAULT_FIRST_TURN_NO_PROGRESS_TIMEOUT
        last_semantic_activity = _time.monotonic()
        first_turn_started = False
        first_turn_progress = False
        turn_done = False

        try:
            while not turn_done and not cancel_event.is_set():
                # Read next JSON-RPC message with a short timeout so we can
                # check cancel_event and semantic inactivity periodically.
                try:
                    msg = await asyncio.wait_for(
                        reader._read_one(), timeout=5.0
                    )
                except TimeoutError:
                    # Check semantic inactivity
                    elapsed = _time.monotonic() - last_semantic_activity
                    if first_turn_started and not first_turn_progress:
                        if elapsed > first_turn_timeout:
                            final_status = "timeout"
                            final_error = "codex first turn no progress timeout"
                            break
                    if elapsed > semantic_timeout:
                        final_status = "timeout"
                        final_error = "codex semantic inactivity timeout"
                        break
                    continue

                if msg is None:
                    break  # EOF

                method = msg.get("method", "")
                params = msg.get("params", {})
                rid = msg.get("id")

                # If it's a response (has id and no method), skip
                if rid is not None and not method:
                    continue

                # Track semantic activity
                if method:
                    last_semantic_activity = _time.monotonic()
                    desc = str(method)
                    if desc not in ("", "item/added", "turn/completed"):
                        pass  # heartbeat-only tracking

                if method == "item/added":
                    item = params.get("item", params)
                    if not first_turn_started:
                        first_turn_started = True

                    # Ensure we have a message open
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

                    itype = item.get("type", "")
                    content = item.get("content", item)

                    if itype == "agent_message" or itype == "text":
                        text = _extract_text(content)
                        if text:
                            output_parts.append(text)
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

                    elif itype in ("reasoning", "thinking"):
                        text = _extract_text(content)
                        if text:
                            first_turn_progress = True
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

                    elif itype in ("command_execution", "tool_call", "function_call"):
                        tool_name = content.get("command", content.get("name", content.get("tool_name", "")))
                        tool_input = content.get("args", content.get("input", content.get("arguments", {})))
                        if isinstance(tool_input, str):
                            try:
                                tool_input = json.loads(tool_input)
                            except (json.JSONDecodeError, TypeError):
                                tool_input = {}
                        if not isinstance(tool_input, dict):
                            tool_input = {}
                        first_turn_progress = True
                        yield ToolCallEvent(
                            conversation_id=input.conversation_id,
                            timestamp=now_ms(),
                            message_id=message_id,
                            call_id=content.get("id", content.get("call_id", "")),
                            tool_name=str(tool_name),
                            args=tool_input,
                        )

                    elif itype in ("tool_result", "command_result"):
                        result_val = content.get("output", content.get("result", content))
                        if not isinstance(result_val, str):
                            result_val = json.dumps(result_val)
                        first_turn_progress = True
                        yield ToolResultEvent(
                            conversation_id=input.conversation_id,
                            timestamp=now_ms(),
                            message_id=message_id,
                            call_id=content.get("id", content.get("call_id", "")),
                            result=str(result_val),
                            is_error=content.get("is_error", False),
                        )

                elif method == "turn/completed":
                    turn_done = True
                    # Extract usage from turn completion
                    usage = params.get("usage", {})
                    if usage:
                        run_input_tokens += usage.get("input_tokens", 0)
                        run_output_tokens += usage.get("output_tokens", 0)
                        run_cache_read += usage.get("cache_read_tokens", 0)
                        last_input_tokens = usage.get("input_tokens", 0)

                elif method == "turn/error":
                    turn_done = True
                    final_status = "failed"
                    final_error = params.get("message", str(params))

        except asyncio.CancelledError:
            cancel_event.set()
            final_status = "aborted"
        except Exception as exc:
            logger.exception("[codex] stream read error")
            final_status = "failed"
            final_error = str(exc)

        # ── close message ─────────────────────────────────────────
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
            cache_creation_tokens=0,
            last_input_tokens=last_input_tokens,
        )
        yield RunUsageEvent(
            conversation_id=input.conversation_id,
            timestamp=now_ms(),
            run_id=input.run_id,
            usage=run_usage,
        )

        logger.info(
            "[codex] run finished: status=%s input_tokens=%d output_tokens=%d",
            final_status,
            run_input_tokens,
            run_output_tokens,
        )
        if final_error:
            logger.error("[codex] run error: %s", final_error)


# ─── JSON-RPC helpers ────────────────────────────────────────────


class _RPCReader:

    def __init__(self, proc: asyncio.subprocess.Process, input: AdapterInput, name: str):
        self._proc = proc
        self._input = input
        self._name = name
        self._buf = b""

    async def read_response(self) -> dict[str, Any] | None:
        """Read one JSON-RPC response (has an id field, no method)."""
        while True:
            msg = await self._read_one()
            if msg is None:
                return None
            if msg.get("id") is not None and not msg.get("method"):
                if msg.get("error"):
                    err = msg["error"]
                    raise _RPCError(err.get("message", str(err)))
                return msg

    async def _read_one(self) -> dict[str, Any] | None:
        """Read one complete JSON-RPC message (request, response, or notification)."""
        if not self._proc.stdout:
            return None
        try:
            # Read until we have a complete JSON line
            while b"\n" not in self._buf:
                chunk = await self._proc.stdout.read(8192)
                if not chunk:
                    if self._buf:
                        # Return whatever's left
                        line = self._buf.decode("utf-8", errors="replace").strip()
                        self._buf = b""
                        if line:
                            return json.loads(line)
                    return None
                self._buf += chunk
            line, self._buf = self._buf.split(b"\n", 1)
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                return None
            return json.loads(text)
        except json.JSONDecodeError:
            return None


class _RPCWriter:

    def __init__(self, proc: asyncio.subprocess.Process, input: AdapterInput, name: str):
        self._proc = proc
        self._input = input
        self._name = name

    async def request(self, method: str, params: dict[str, Any]) -> None:
        """Send a JSON-RPC 2.0 request."""
        rid = _next_rpc_id()
        msg = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        await self._send(msg)

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a JSON-RPC 2.0 notification (no id). Fire-and-forget."""
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        # Schedule the write — won't be awaited (fire-and-forget).
        # The caller MUST ensure stdin is writable.
        asyncio.create_task(self._send(msg))

    async def _send(self, msg: dict[str, Any]) -> None:
        if not self._proc.stdin or self._proc.stdin.is_closing():
            return
        data = json.dumps(msg) + "\n"
        self._proc.stdin.write(data.encode())
        await self._proc.stdin.drain()


# ─── content extraction helpers ──────────────────────────────────


def _extract_text(content: Any) -> str:
    """Extract a text string from various Codex item content shapes."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        # Common shapes: {"text": "..."}, {"content": "..."}, {"value": "..."}
        for key in ("text", "content", "value"):
            val = content.get(key)
            if isinstance(val, str) and val:
                return val
        # {"parts": [{"type": "text", "text": "..."}]}
        parts = content.get("parts", [])
        if isinstance(parts, list):
            texts = [
                p.get("text", "")
                for p in parts
                if isinstance(p, dict) and p.get("type") in ("text", "output_text")
            ]
            return "".join(t for t in texts if isinstance(t, str))
    return str(content) if content else ""
