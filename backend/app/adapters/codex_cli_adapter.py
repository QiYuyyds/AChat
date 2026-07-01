"""CodexCLIAdapter — spawns codex CLI as subprocess and translates JSON-RPC events.

Implements the ``AgentPlatformAdapter`` interface by spawning
``codex app-server --listen stdio://`` and communicating via JSON-RPC 2.0
over stdin/stdout. Codex events are translated to AChat ``StreamEvent`` objects.

See ``specs/codex-cli-adapter/spec.md`` and ``design.md`` for the full
contract.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.adapters.base import AdapterInput, AdapterName, AgentPlatformAdapter
from app.adapters.codex_home import cleanup_codex_home, prepare_codex_home
from app.config import get_settings
from app.schemas.events import (
    MessageEndEvent,
    MessageStartEvent,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
    RunUsageEvent,
    StreamEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from app.schemas.messages import RunUsage
from app.tools.internal_token import generate_tool_token, revoke_tool_token
from app.utils.clock import now_ms
from app.utils.ids import new_message_id, new_tool_call_id

logger = logging.getLogger(__name__)

# Timeout for graceful process termination before SIGKILL
_TERMINATE_TIMEOUT_S = 5


# ─── Popen fallback for SelectorEventLoop (Windows + uvicorn --reload) ────────

class _PopenAsyncStdin:
    """Async wrapper for ``subprocess.Popen.stdin``."""

    def __init__(self, stdin) -> None:
        self._stdin = stdin

    def write(self, data: bytes) -> None:
        self._stdin.write(data)

    async def drain(self) -> None:
        await asyncio.to_thread(self._stdin.flush)

    def close(self) -> None:
        self._stdin.close()


class _PopenAsyncStdout:
    """Async wrapper for ``subprocess.Popen.stdout``."""

    def __init__(self, stdout) -> None:
        self._stdout = stdout

    async def readline(self) -> bytes:
        return await asyncio.to_thread(self._stdout.readline)


class _PopenAsyncProcess:
    """Wraps ``subprocess.Popen`` to mimic ``asyncio.subprocess.Process``.

    Used when ``asyncio.create_subprocess_exec`` is unavailable — notably on
    Windows under ``SelectorEventLoop`` (uvicorn ``--reload`` selects it),
    which raises ``NotImplementedError``.
    """

    def __init__(self, popen: subprocess.Popen) -> None:
        self._popen = popen
        self.stdin: _PopenAsyncStdin | None = (
            _PopenAsyncStdin(popen.stdin) if popen.stdin else None
        )
        self.stdout: _PopenAsyncStdout | None = (
            _PopenAsyncStdout(popen.stdout) if popen.stdout else None
        )
        self.stderr = popen.stderr  # not read by adapter; kept for completeness

    @property
    def returncode(self) -> int | None:
        return self._popen.poll()

    def terminate(self) -> None:
        self._popen.terminate()

    def kill(self) -> None:
        self._popen.kill()

    async def wait(self) -> int:
        return await asyncio.to_thread(self._popen.wait)


@dataclass
class _RunUsage:
    """Accumulated token usage for a single codex run."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    last_input_tokens: int = 0


class CodexCLIAdapter(AgentPlatformAdapter):
    """Adapter that spawns ``codex app-server`` as a subprocess."""

    @property
    def name(self) -> AdapterName:
        return "codex"

    async def stream(  # noqa: C901 - complex but faithful to the spec
        self, input: AdapterInput, cancel_event: asyncio.Event
    ) -> AsyncIterator[StreamEvent]:
        """Spawn codex, send the prompt, translate events to StreamEvent."""
        message_id = new_message_id()
        codex_home: str | None = None
        proc: asyncio.subprocess.Process | None = None
        cancel_task: asyncio.Task | None = None
        run_id = input.run_id

        # ─── 1. Resolve executable ───
        executable = self._resolve_executable(input)
        if not executable:
            logger.error(
                "[codex] executable not found. Install with: npm install -g @openai/codex"
            )
            yield MessageStartEvent(
                conversation_id=input.conversation_id,
                timestamp=now_ms(),
                message_id=message_id,
                agent_id=input.agent_id,
                run_id=run_id,
            )
            yield MessageEndEvent(
                conversation_id=input.conversation_id,
                timestamp=now_ms(),
                message_id=message_id,
            )
            return

        # ─── 2. Prepare MCP env and CODEX_HOME ───
        tool_token = generate_tool_token(run_id)
        mcp_env = self._build_mcp_env(input, tool_token)
        mcp_script_path = self._resolve_mcp_script_path()
        settings = get_settings()
        data_dir = str(settings.data_path)

        try:
            codex_home = prepare_codex_home(
                run_id=run_id,
                data_dir=data_dir,
                mcp_env=mcp_env,
                mcp_script_path=mcp_script_path,
            )
        except Exception as e:
            logger.error("[codex] prepare_codex_home failed: %s", e)
            yield MessageStartEvent(
                conversation_id=input.conversation_id,
                timestamp=now_ms(),
                message_id=message_id,
                agent_id=input.agent_id,
                run_id=run_id,
            )
            yield MessageEndEvent(
                conversation_id=input.conversation_id,
                timestamp=now_ms(),
                message_id=message_id,
            )
            return

        # ─── 3. Spawn subprocess ───
        env = os.environ.copy()
        env["CODEX_HOME"] = codex_home

        try:
            proc = await asyncio.create_subprocess_exec(
                executable,
                "app-server",
                "--listen",
                "stdio://",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=input.workspace_path,
                env=env,
            )
        except FileNotFoundError:
            logger.error(
                "[codex] executable not found at %s. "
                "Install with: npm install -g @openai/codex",
                executable,
            )
            yield MessageStartEvent(
                conversation_id=input.conversation_id,
                timestamp=now_ms(),
                message_id=message_id,
                agent_id=input.agent_id,
                run_id=run_id,
            )
            yield MessageEndEvent(
                conversation_id=input.conversation_id,
                timestamp=now_ms(),
                message_id=message_id,
            )
            cleanup_codex_home(codex_home)
            revoke_tool_token(run_id)
            return
        except NotImplementedError:
            # SelectorEventLoop (Windows + uvicorn --reload) cannot do
            # asyncio.create_subprocess_exec; fall back to subprocess.Popen
            # wrapped with asyncio.to_thread for I/O.
            logger.info(
                "[codex] create_subprocess_exec unavailable (SelectorEventLoop); "
                "using subprocess.Popen fallback"
            )
            try:
                popen = await asyncio.to_thread(
                    subprocess.Popen,
                    [executable, "app-server", "--listen", "stdio://"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=input.workspace_path,
                    env=env,
                )
                proc = _PopenAsyncProcess(popen)
            except Exception as e:
                logger.error(
                    "[codex] Popen fallback failed: %s | executable=%s cwd=%s",
                    repr(e), executable, input.workspace_path,
                )
                yield MessageStartEvent(
                    conversation_id=input.conversation_id,
                    timestamp=now_ms(),
                    message_id=message_id,
                    agent_id=input.agent_id,
                    run_id=run_id,
                )
                yield MessageEndEvent(
                    conversation_id=input.conversation_id,
                    timestamp=now_ms(),
                    message_id=message_id,
                )
                cleanup_codex_home(codex_home)
                revoke_tool_token(run_id)
                return
        except Exception as e:
            logger.error(
                "[codex] subprocess spawn failed: %s | executable=%s cwd=%s CODEX_HOME=%s",
                repr(e), executable, input.workspace_path, codex_home,
            )
            yield MessageStartEvent(
                conversation_id=input.conversation_id,
                timestamp=now_ms(),
                message_id=message_id,
                agent_id=input.agent_id,
                run_id=run_id,
            )
            yield MessageEndEvent(
                conversation_id=input.conversation_id,
                timestamp=now_ms(),
                message_id=message_id,
            )
            cleanup_codex_home(codex_home)
            revoke_tool_token(run_id)
            return

        # ─── 4. Start cancel watcher ───
        cancel_task = asyncio.create_task(
            self._watch_cancel(proc, cancel_event)
        )

        try:
            # ─── 5. Initialize + thread/start ───
            # Codex CLI ≥0.142 requires an initialize/initialized handshake
            # before any thread/* or turn/* calls.
            # Initialize handshake — params match multica-main's codex.go
            init_req_id = 1
            await self._send_request(
                proc, init_req_id, "initialize",
                {"clientInfo": {"name": "agenthub", "title": "AgentHub", "version": "0.1.0"},
                 "capabilities": {"experimentalApi": True}},
            )
            # Wait for initialize response (skip notifications)
            init_ok = False
            while not init_ok:
                msg = await self._read_message(proc)
                if msg is None:
                    logger.error("[codex] process exited before initialize response")
                    yield MessageStartEvent(
                        conversation_id=input.conversation_id,
                        timestamp=now_ms(),
                        message_id=message_id,
                        agent_id=input.agent_id,
                        run_id=run_id,
                    )
                    yield MessageEndEvent(
                        conversation_id=input.conversation_id,
                        timestamp=now_ms(),
                        message_id=message_id,
                    )
                    return
                if msg.get("id") == init_req_id:
                    if msg.get("error"):
                        logger.error("[codex] initialize error: %s", msg["error"])
                    init_ok = True

            # Send initialized notification
            await self._send_notification(proc, "initialized", {})

            # thread/start — params match multica-main's startOrResumeThread
            req_id = 2
            thread_start_params: dict[str, Any] = {
                "model": input.model_id or None,
                "modelProvider": None,
                "profile": None,
                "cwd": input.workspace_path,
                "approvalPolicy": None,
                "sandbox": None,
                "config": None,
                "baseInstructions": None,
                "developerInstructions": input.system_prompt or None,
                "compactPrompt": None,
                "includeApplyPatchTool": None,
                "experimentalRawEvents": True,
                "persistExtendedHistory": True,
            }
            await self._send_request(
                proc, req_id, "thread/start", thread_start_params,
            )

            # Read until we get the thread/start response
            thread_id: str | None = None
            while thread_id is None:
                msg = await self._read_message(proc)
                if msg is None:
                    # EOF — process died
                    logger.error("[codex] process exited before thread/start response")
                    yield MessageStartEvent(
                        conversation_id=input.conversation_id,
                        timestamp=now_ms(),
                        message_id=message_id,
                        agent_id=input.agent_id,
                        run_id=run_id,
                    )
                    yield MessageEndEvent(
                        conversation_id=input.conversation_id,
                        timestamp=now_ms(),
                        message_id=message_id,
                    )
                    return

                if msg.get("id") == req_id:
                    result = msg.get("result") or {}
                    # Codex ≥0.142 nests thread_id inside result.thread.id
                    thread_obj = result.get("thread") or {}
                    thread_id = (
                        thread_obj.get("id")
                        or result.get("thread_id")
                        or result.get("threadId")
                        or result.get("id")
                    )
                    if not thread_id:
                        logger.error(
                            "[codex] thread/start response missing thread_id: %s", msg
                        )
                        yield MessageStartEvent(
                            conversation_id=input.conversation_id,
                            timestamp=now_ms(),
                            message_id=message_id,
                            agent_id=input.agent_id,
                            run_id=run_id,
                        )
                        yield MessageEndEvent(
                            conversation_id=input.conversation_id,
                            timestamp=now_ms(),
                            message_id=message_id,
                        )
                        return
                # Ignore notifications before thread/start response

            # ─── 6. Send turn/start — params match multica-main's codex.go
            run_req_id = 3
            run_params: dict[str, Any] = {
                "threadId": thread_id,
                "input": [
                    {"type": "text", "text": input.prompt},
                ],
            }
            await self._send_request(
                proc, run_req_id, "turn/start", run_params
            )

            # ─── 7. Yield MessageStartEvent ───
            yield MessageStartEvent(
                conversation_id=input.conversation_id,
                timestamp=now_ms(),
                message_id=message_id,
                agent_id=input.agent_id,
                run_id=run_id,
            )

            # ─── 8. Read events and translate ───
            # Event protocol follows multica-main's codex.go handleRawNotification.
            # Two protocols exist: legacy (codex/event with msg.type) and
            # raw v2 (method-based notifications like item/*, turn/*).
            text_part_index = -1
            thinking_part_index = -1
            next_part_index = 0
            run_usage = _RunUsage()
            model_id = input.model_id or "codex"
            turn_done = False

            while not turn_done:
                if cancel_event.is_set():
                    break

                msg = await self._read_message(proc)
                if msg is None:
                    # EOF — process exited
                    logger.warning("[codex] stdout EOF before turn/completed")
                    break

                # Skip empty messages (non-JSON lines)
                if not msg:
                    continue

                # turn/start response — just acknowledge, don't end loop
                if msg.get("id") == run_req_id:
                    if msg.get("error"):
                        logger.error("[codex] turn/start error: %s", msg["error"])
                        turn_done = True
                    else:
                        logger.info("[codex] turn/start response received")
                    continue

                method = msg.get("method", "")
                params = msg.get("params") or {}

                # ── Legacy protocol: codex/event ──
                if method == "codex/event" or method.startswith("codex/event/"):
                    inner = params.get("msg") or {}
                    if isinstance(inner, dict):
                        msg_type = inner.get("type", "")
                        if msg_type == "agent_message":
                            text = inner.get("message", "")
                            if isinstance(text, str) and text:
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
                        elif msg_type in ("task_complete", "turn_aborted"):
                            turn_done = True
                    continue

                # ── Raw v2 protocol ──
                logger.info("[codex] event: %s | params keys: %s", method, list(params.keys()))

                if method in ("turn/started", "thread/started"):
                    continue

                elif method == "turn/completed":
                    # Extract usage from params.turn.usage
                    turn_obj = params.get("turn") or {}
                    usage = turn_obj.get("usage")
                    if usage:
                        run_usage = self._merge_usage(run_usage, usage)
                    status = (
                        turn_obj.get("status") or ""
                    )
                    if status == "failed":
                        err_msg = (
                            turn_obj.get("error", {}).get("message")
                            if isinstance(turn_obj.get("error"), dict)
                            else "codex turn failed"
                        )
                        logger.error("[codex] turn failed: %s", err_msg)
                    logger.info("[codex] turn/completed status=%s", status)
                    turn_done = True

                elif method == "error":
                    will_retry = params.get("willRetry", False)
                    err_obj = params.get("error") or {}
                    err_msg = (
                        err_obj.get("message")
                        or params.get("message")
                        or "unknown error"
                    )
                    logger.error("[codex] error event: %s willRetry=%s", err_msg, will_retry)
                    if not will_retry:
                        turn_done = True

                elif method.startswith("mcpServer"):
                    status = params.get("status", "")
                    mcp_error = params.get("error", "")
                    mcp_name = params.get("name", "")
                    if mcp_error:
                        logger.warning(
                            "[codex] MCP server '%s' status=%s error=%s",
                            mcp_name, status, mcp_error,
                        )
                    else:
                        logger.info(
                            "[codex] MCP server '%s' status=%s", mcp_name, status
                        )

                # ── Streaming text deltas (highest priority) ──
                # These arrive as item/agentMessage/delta and
                # item/reasoning/summaryTextDelta with params.delta as string.
                elif method == "item/agentMessage/delta":
                    delta_str = params.get("delta", "")
                    if isinstance(delta_str, str) and delta_str:
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
                            delta={"type": "text.append", "text": delta_str},
                        )

                elif method == "item/reasoning/summaryTextDelta":
                    delta_str = params.get("delta", "")
                    if isinstance(delta_str, str) and delta_str:
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
                            delta={"type": "thinking.append", "text": delta_str},
                        )

                # ── Other ignorable events ──
                elif method in (
                    "rawResponseItem/completed",
                    "item/reasoning/summaryPartAdded",
                    "thread/status/changed",
                    "warning",
                ):
                    if method == "warning":
                        logger.warning("[codex] warning: %s", params.get("message", ""))
                    continue

                # ── Item lifecycle: item/started, item/completed ──
                elif method.startswith("item/"):
                    item = params.get("item") or {}
                    item_type = item.get("type", "")
                    item_id = item.get("id", "")

                    if method == "item/started" and item_type == "commandExecution":
                        call_id = item_id or new_tool_call_id()
                        command = item.get("command", "bash")
                        yield ToolCallEvent(
                            conversation_id=input.conversation_id,
                            timestamp=now_ms(),
                            message_id=message_id,
                            call_id=call_id,
                            tool_name="exec_command",
                            args={"command": command},
                        )

                    elif method == "item/completed" and item_type == "commandExecution":
                        call_id = item_id or new_tool_call_id()
                        output = item.get("aggregatedOutput", "")
                        yield ToolResultEvent(
                            conversation_id=input.conversation_id,
                            timestamp=now_ms(),
                            message_id=message_id,
                            call_id=call_id,
                            result={"output": output},
                            is_error=False,
                        )

                    elif method == "item/started" and item_type == "fileChange":
                        call_id = item_id or new_tool_call_id()
                        yield ToolCallEvent(
                            conversation_id=input.conversation_id,
                            timestamp=now_ms(),
                            message_id=message_id,
                            call_id=call_id,
                            tool_name="patch_apply",
                            args={},
                        )

                    elif method == "item/completed" and item_type == "fileChange":
                        call_id = item_id or new_tool_call_id()
                        yield ToolResultEvent(
                            conversation_id=input.conversation_id,
                            timestamp=now_ms(),
                            message_id=message_id,
                            call_id=call_id,
                            result={"output": ""},
                            is_error=False,
                        )

                    elif method == "item/completed" and item_type == "agentMessage":
                        # Complete agent message — only yield if we haven't
                        # already streamed it via item/agentMessage/delta
                        if text_part_index < 0:
                            text = item.get("text", "")
                            if isinstance(text, str) and text:
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

                    else:
                        logger.debug(
                            "[codex] unhandled item event: %s type=%s",
                            method, item_type,
                        )

                else:
                    logger.debug("[codex] unhandled event: %s", method)

            # ─── 9. Yield PartEndEvents for open parts ───
            if thinking_part_index >= 0:
                yield PartEndEvent(
                    conversation_id=input.conversation_id,
                    timestamp=now_ms(),
                    message_id=message_id,
                    part_index=thinking_part_index,
                )
            if text_part_index >= 0:
                yield PartEndEvent(
                    conversation_id=input.conversation_id,
                    timestamp=now_ms(),
                    message_id=message_id,
                    part_index=text_part_index,
                )

            # ─── 10. Yield MessageEndEvent + RunUsageEvent ───
            yield MessageEndEvent(
                conversation_id=input.conversation_id,
                timestamp=now_ms(),
                message_id=message_id,
            )
            yield RunUsageEvent(
                conversation_id=input.conversation_id,
                timestamp=now_ms(),
                run_id=run_id,
                usage=RunUsage(
                    input_tokens=run_usage.input_tokens,
                    output_tokens=run_usage.output_tokens,
                    cache_creation_tokens=run_usage.cache_creation_tokens,
                    cache_read_tokens=run_usage.cache_read_tokens,
                    last_input_tokens=run_usage.last_input_tokens,
                    model=model_id,
                ),
            )

        except Exception as e:
            logger.error("[codex] stream error: %s", e, exc_info=True)
            yield MessageEndEvent(
                conversation_id=input.conversation_id,
                timestamp=now_ms(),
                message_id=message_id,
            )
        finally:
            # ─── Cleanup ───
            if cancel_task and not cancel_task.done():
                cancel_task.cancel()
                try:
                    await cancel_task
                except asyncio.CancelledError:
                    pass

            if proc and proc.stdin:
                try:
                    proc.stdin.close()
                except Exception:
                    pass

            if proc:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=_TERMINATE_TIMEOUT_S)
                except asyncio.TimeoutError:
                    logger.warning("[codex] process did not exit, killing")
                    try:
                        proc.kill()
                        await proc.wait()
                    except Exception:
                        pass
                except Exception:
                    pass

            if codex_home:
                cleanup_codex_home(codex_home)

            revoke_tool_token(run_id)

    # ─── Helpers ──────────────────────────────────────────────────

    def _resolve_executable(self, input: AdapterInput) -> str | None:
        """Resolve codex binary path: executable_path → CODEX_EXECUTABLE → PATH.

        Returns ``None`` if the executable cannot be found.
        """
        # Priority 1: agent.executable_path
        if input.executable_path:
            p = Path(input.executable_path)
            if p.exists() and os.access(str(p), os.X_OK):
                return str(p)
            logger.warning(
                "[codex] executable_path '%s' does not exist or is not executable",
                input.executable_path,
            )

        # Priority 2: CODEX_EXECUTABLE env var
        env_exe = os.environ.get("CODEX_EXECUTABLE")
        if env_exe:
            p = Path(env_exe)
            if p.exists() and os.access(str(p), os.X_OK):
                return str(p)
            logger.warning(
                "[codex] CODEX_EXECUTABLE '%s' does not exist or is not executable",
                env_exe,
            )

        # Priority 3: PATH search
        which = shutil.which("codex")
        if which:
            return which

        return None

    def _build_mcp_env(self, input: AdapterInput, tool_token: str) -> dict[str, str]:
        """Construct MCP bridge environment variables for the per-task config.toml."""
        settings = get_settings()
        base_url = f"http://127.0.0.1:{settings.port}"

        env: dict[str, str] = {
            "AGENTHUB_INTERNAL_BASE_URL": base_url,
            "AGENTHUB_INTERNAL_TOOL_TOKEN": tool_token,
            "AGENTHUB_CONVERSATION_ID": input.conversation_id,
            "AGENTHUB_AGENT_ID": input.agent_id,
            "AGENTHUB_RUN_ID": input.run_id,
        }

        if input.tool_names:
            env["AGENTHUB_ALLOWED_TOOLS"] = ",".join(input.tool_names)

        return env

    def _resolve_mcp_script_path(self) -> str:
        """Find the ``agenthub-codex-mcp.mjs`` script.

        Search order:
        1. ``AGENTHUB_MCP_SCRIPT`` env var
        2. ``<project_root>/scripts/agenthub-codex-mcp.mjs``
        3. ``<cwd>/scripts/agenthub-codex-mcp.mjs``
        4. Relative to this file (backend/app/adapters/ → ../../scripts/)
        """
        env_path = os.environ.get("AGENTHUB_MCP_SCRIPT")
        if env_path and Path(env_path).exists():
            return str(Path(env_path).resolve())

        # Project root is the parent of the data_dir (.agenthub-data)
        try:
            settings = get_settings()
            project_root = settings.data_path.parent
            candidate = project_root / "scripts" / "agenthub-codex-mcp.mjs"
            if candidate.exists():
                return str(candidate.resolve())
        except Exception:
            pass

        # Try relative to CWD
        cwd_candidate = Path.cwd() / "scripts" / "agenthub-codex-mcp.mjs"
        if cwd_candidate.exists():
            return str(cwd_candidate.resolve())

        # Try relative to this file (backend/app/adapters/ → ../../scripts/)
        file_candidate = Path(__file__).parent.parent.parent.parent / "scripts" / "agenthub-codex-mcp.mjs"
        if file_candidate.exists():
            return str(file_candidate.resolve())

        # Return the expected path even if not found (will fail at spawn time)
        return str((Path.cwd() / "scripts" / "agenthub-codex-mcp.mjs").resolve())

    async def _send_request(
        self,
        proc: asyncio.subprocess.Process,
        req_id: int,
        method: str,
        params: dict[str, Any],
    ) -> None:
        """Write a JSON-RPC 2.0 request to the process stdin."""
        request = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }
        line = json.dumps(request) + "\n"
        if proc.stdin is None:
            raise RuntimeError("codex process stdin is not available")
        proc.stdin.write(line.encode("utf-8"))
        await proc.stdin.drain()

    async def _send_notification(
        self,
        proc: asyncio.subprocess.Process,
        method: str,
        params: dict[str, Any],
    ) -> None:
        """Write a JSON-RPC 2.0 notification (no id, no response expected)."""
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        line = json.dumps(notification) + "\n"
        if proc.stdin is None:
            raise RuntimeError("codex process stdin is not available")
        proc.stdin.write(line.encode("utf-8"))
        await proc.stdin.drain()

    async def _read_message(
        self, proc: asyncio.subprocess.Process
    ) -> dict[str, Any] | None:
        """Read a single JSON-RPC message from stdout.

        Returns ``None`` on EOF (process exited).
        Returns ``{}`` for empty lines or non-JSON output (skipped).
        """
        if proc.stdout is None:
            return None
        try:
            line_bytes = await proc.stdout.readline()
        except Exception:
            return None

        if not line_bytes:
            return None  # EOF

        line = line_bytes.decode("utf-8", errors="replace").strip()
        if not line:
            return {}

        try:
            return json.loads(line)
        except json.JSONDecodeError:
            # codex might emit non-JSON lines on stdout (e.g., progress)
            logger.debug("[codex] non-JSON stdout line: %s", line[:200])
            return {}

    async def _watch_cancel(
        self, proc: asyncio.subprocess.Process, cancel_event: asyncio.Event
    ) -> None:
        """Monitor cancel_event and terminate the process when set."""
        try:
            await cancel_event.wait()
        except asyncio.CancelledError:
            return

        if proc.returncode is not None:
            return  # already exited

        logger.info("[codex] cancel_event set, terminating process")
        try:
            proc.terminate()
        except ProcessLookupError:
            return
        except Exception:
            pass

        try:
            await asyncio.wait_for(proc.wait(), timeout=_TERMINATE_TIMEOUT_S)
        except asyncio.TimeoutError:
            logger.warning("[codex] process did not terminate, killing")
            try:
                proc.kill()
            except Exception:
                pass

    def _merge_usage(
        self, current: _RunUsage, usage: dict[str, Any]
    ) -> _RunUsage:
        """Merge token usage from a codex event into the accumulator."""
        current.input_tokens += _int_or_zero(usage.get("input_tokens") or usage.get("inputTokens"))
        current.output_tokens += _int_or_zero(usage.get("output_tokens") or usage.get("outputTokens"))
        current.cache_creation_tokens += _int_or_zero(
            usage.get("cache_creation_tokens") or usage.get("cacheCreationTokens")
        )
        current.cache_read_tokens += _int_or_zero(
            usage.get("cache_read_tokens") or usage.get("cacheReadTokens")
        )
        last_in = usage.get("last_input_tokens") or usage.get("lastInputTokens")
        if isinstance(last_in, (int, float)):
            current.last_input_tokens = int(last_in)
        return current


def _int_or_zero(value: Any) -> int:
    """Safely convert a value to int, returning 0 for None/non-numeric."""
    if value is None:
        return 0
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0
