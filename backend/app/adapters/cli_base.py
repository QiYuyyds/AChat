"""CLI adapter base — shared subprocess lifecycle + protocol helpers.

Port & simplification of multica's server/pkg/agent patterns:
  - CLIProcess         ≈ multica's stdin/stdout/stderr pipe + graceful shutdown
  - filter_custom_args ≈ multica's filterCustomArgs
  - is_filtered_child_env_key ≈ multica's isFilteredChildEnvKey

All CLI-based adapters (ClaudeCLIAdapter, CodexCLIAdapter) inherit from
CLIAdapterBase and implement _build_args / _write_prompt / _read_events.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import sys
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum

from app.adapters.base import AdapterInput, AgentPlatformAdapter
from app.schemas.events import StreamEvent

logger = logging.getLogger(__name__)

# ─── blocked arg mode ──────────────────────────────────────────


class BlockedArgMode(Enum):
    """Whether a blocked CLI arg takes a value or is standalone."""

    WITH_VALUE = "with_value"  # e.g. --output-format stream-json
    STANDALONE = "standalone"  # e.g. --yolo


# ─── subprocess wrapper ───────────────────────────────────────


@dataclass
class CLIProcess:
    """Encapsulate a running CLI subprocess and provide graceful shutdown.

    Graceful shutdown sequence (mirrors multica's drainAndWait pattern):
      1. Close stdin → CLI receives EOF
      2. Wait grace_timeout for clean exit
      3. proc.terminate() → SIGTERM (POSIX) / TerminateProcess (Windows)
      4. Wait 2s
      5. proc.kill() → SIGKILL (POSIX) / TerminateProcess (forced)
    """

    proc: asyncio.subprocess.Process
    cancel_event: asyncio.Event
    grace_timeout: float = 10.0
    _shutdown: bool = field(default=False, init=False)

    async def shutdown(self) -> None:
        """Gracefully shut down the CLI subprocess. Idempotent."""
        if self._shutdown:
            return
        self._shutdown = True

        if self.proc.returncode is not None:
            return  # already exited

        # Phase 1: close stdin to signal EOF
        if self.proc.stdin and not self.proc.stdin.is_closing():
            self.proc.stdin.close()

        # Phase 2: wait for graceful exit
        try:
            await asyncio.wait_for(
                self._wait_exit(), timeout=self.grace_timeout
            )
            return
        except TimeoutError:
            logger.debug("CLI process did not exit gracefully; sending terminate")

        # Phase 3: terminate
        try:
            self.proc.terminate()
        except ProcessLookupError:
            return

        try:
            await asyncio.wait_for(self._wait_exit(), timeout=2.0)
            return
        except TimeoutError:
            logger.debug("CLI process did not respond to terminate; killing")

        # Phase 4: kill
        try:
            self.proc.kill()
        except ProcessLookupError:
            pass
        await self._wait_exit()

    async def _wait_exit(self) -> None:
        """Wait for the process to exit, swallowing any cancel."""
        try:
            await self.proc.wait()
        except asyncio.CancelledError:
            pass


# ─── async subprocess fallback (Windows SelectorEventLoop) ──────
#
# On Windows asyncio.create_subprocess_exec requires ProactorEventLoop.
# If the running loop is a SelectorEventLoop (set by some lib or env config),
# _make_subprocess_transport raises NotImplementedError.  The classes below
# wrap a synchronous subprocess.Popen with thread-pool-based async I/O so
# the rest of the code can treat the result like an asyncio.subprocess.Process.


class _AsyncStreamReader:
    """Async wrapper around a synchronous readable pipe (stdout / stderr)."""

    def __init__(self, stream, loop: asyncio.AbstractEventLoop):
        self._stream = stream
        self._loop = loop
        self._eof = False

    async def readline(self) -> bytes:
        if self._eof:
            return b""
        line = await self._loop.run_in_executor(None, self._stream.readline)
        if not line:
            self._eof = True
        return line

    async def read(self, n: int = -1) -> bytes:
        if self._eof:
            return b""
        data = await self._loop.run_in_executor(None, self._stream.read, n)
        if not data:
            self._eof = True
        return data

    def at_eof(self) -> bool:
        return self._eof

    def feed_eof(self) -> None:
        self._eof = True


class _AsyncStreamWriter:
    """Async wrapper around a synchronous writable pipe (stdin)."""

    def __init__(self, stream, loop: asyncio.AbstractEventLoop):
        self._stream = stream
        self._loop = loop
        self._closed = False

    def write(self, data: bytes) -> None:
        self._stream.write(data)

    async def drain(self) -> None:
        await self._loop.run_in_executor(None, self._stream.flush)

    def close(self) -> None:
        self._closed = True
        try:
            self._stream.close()
        except Exception:
            pass

    def is_closing(self) -> bool:
        return self._closed


class _AsyncProcessWrapper:
    """Wraps subprocess.Popen to quack like asyncio.subprocess.Process."""

    def __init__(self, popen: subprocess.Popen, loop: asyncio.AbstractEventLoop):
        self._popen = popen
        self.stdin = (
            _AsyncStreamWriter(popen.stdin, loop) if popen.stdin else None
        )
        self.stdout = (
            _AsyncStreamReader(popen.stdout, loop) if popen.stdout else None
        )
        self.stderr = (
            _AsyncStreamReader(popen.stderr, loop) if popen.stderr else None
        )

    @property
    def pid(self) -> int | None:
        return self._popen.pid

    @property
    def returncode(self) -> int | None:
        return self._popen.returncode

    async def wait(self) -> int:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._popen.wait)

    def terminate(self) -> None:
        self._popen.terminate()

    def kill(self) -> None:
        self._popen.kill()


async def _spawn_subprocess_fallback(
    exec_path: str,
    *args: str,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    creationflags: int = 0,
) -> _AsyncProcessWrapper:
    """Spawn a subprocess using Popen with async I/O wrappers.

    Used as a fallback when the running event loop doesn't support
    ``asyncio.create_subprocess_exec`` (e.g. SelectorEventLoop on Windows).
    """
    loop = asyncio.get_running_loop()

    popen = subprocess.Popen(
        [exec_path, *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
        creationflags=creationflags,
    )
    return _AsyncProcessWrapper(popen, loop)


# ─── arg filtering (mirrors multica filterCustomArgs) ─────────


def filter_custom_args(
    args: list[str],
    blocked: dict[str, BlockedArgMode],
) -> list[str]:
    """Remove protocol-critical flags from user-configured custom args.

    Each CLI backend defines its own ``blocked`` set (the flags it hardcodes).
    The daemon only blocks args that would break the communication protocol,
    not every possible dangerous flag. Workspace members are trusted to
    configure agents sensibly.

    Shell quoting is stripped from each arg (users commonly type
    --deny-tool='write' in config fields — since we spawn processes without
    a shell, those quotes would be passed literally).
    """
    if not args:
        return args

    filtered: list[str] = []
    skip = False
    for raw in args:
        if skip:
            skip = False
            continue

        arg = unshell_quote_arg(raw)
        flag = arg
        has_inline_value = False
        if "=" in arg and arg.startswith("-"):
            flag = arg.split("=", 1)[0]
            has_inline_value = True

        mode = blocked.get(flag)
        if mode is not None:
            logger.debug("custom_args: blocked protocol-critical flag %r, skipping", flag)
            if mode == BlockedArgMode.WITH_VALUE and not has_inline_value:
                skip = True  # next arg is the value for this flag
            continue

        filtered.append(arg)
    return filtered


def unshell_quote_arg(arg: str) -> str:
    """Strip a single layer of shell-style quotes from a flag argument.

    ``--flag='value'`` → ``--flag=value``
    ``'standalone'`` → ``standalone``
    """
    if arg.startswith("-") and "=" in arg:
        prefix, value = arg.split("=", 1)
        unquoted, ok = _strip_surrounding_quotes(value)
        if ok:
            return f"{prefix}={unquoted}"
        return arg
    unquoted, ok = _strip_surrounding_quotes(arg)
    if ok:
        return unquoted
    return arg


def _strip_surrounding_quotes(s: str) -> tuple[str, bool]:
    if len(s) >= 2:
        if (s.startswith("'") and s.endswith("'")) or (
            s.startswith('"') and s.endswith('"')
        ):
            return s[1:-1], True
    return s, False


# ─── environment isolation (mirrors multica isFilteredChildEnvKey) ─


def is_filtered_child_env_key(key: str) -> bool:
    """Report whether an inherited env var is an internal runtime marker.

    Internal markers (CLAUDE_CODE_SESSION_ID, CLAUDECODE, etc.) MUST NOT
    leak into spawned child processes. User-facing config vars
    (CLAUDE_CODE_GIT_BASH_PATH, ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.)
    are deliberately preserved.
    """
    internal_exact = {
        "CLAUDECODE",  # "1" when running inside Claude Code
        "CLAUDE_CODE_ENTRYPOINT",
        "CLAUDE_CODE_EXECPATH",
        "CLAUDE_CODE_SESSION_ID",
        "CLAUDE_CODE_SSE_PORT",
    }
    if key in internal_exact:
        return True
    # CLAUDECODE_* (no underscore between CLAUDE and CODE) is wholly internal
    if key.startswith("CLAUDECODE_"):
        return True
    # Unrelated internal markers from other tools
    if key in {
        "MULTICA_DAEMON_ID",
        "MULTICA_AGENT_TOKEN",
        "CONFIG_AGENT_RUN_ID",
    }:
        return True
    return False


def build_child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build env dict for CLI subprocess.

    Starts from ``os.environ``, strips internal runtime markers, then
    merges ``extra`` on top (so per-agent API key overrides win).
    """
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if not is_filtered_child_env_key(key):
            env[key] = value
    if extra:
        env.update(extra)
    return env


# ─── Windows EXE resolution ──────────────────────────────────


def _resolve_windows_exe(exec_path: str) -> str:
    """Resolve a ``.cmd`` npm wrapper to the underlying ``.exe`` on Windows.

    npm global installs on Windows produce ``<name>.cmd`` batch files that invoke
    the real executable via ``cmd.exe``. Spawning through ``cmd.exe`` can break
    stdio pipe passthrough. We resolve ``.cmd`` files (and short names that
    resolve to ``.cmd`` files) to the underlying ``.exe``.

    ``exec_path`` may be a full path to a ``.cmd`` file, or a short name like
    ``"claude"``. In either case we find the real ``.exe``.
    """
    # If it's a short name (no path separator), resolve via PATH first.
    if not os.sep in exec_path and os.altsep not in exec_path:
        resolved = shutil.which(exec_path)
        if resolved and os.path.isfile(resolved):
            exec_path = resolved

    # Not a .cmd file — nothing to resolve.
    if not exec_path.lower().endswith(".cmd"):
        return exec_path

    cmd_dir = os.path.dirname(exec_path)

    # Try to read the .cmd file to extract the underlying .exe path.
    try:
        with open(exec_path, encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return exec_path  # can't read .cmd file, fall back

    # npm .cmd files have the pattern:
    #   "%dp0%\node_modules\@anthropic-ai\claude-code\bin\claude.exe" %*
    m = re.search(r'"([^"]+\.exe)"', content)
    if not m:
        return exec_path  # no .exe reference found, fall back to .cmd

    exe_rel = m.group(1)
    # Expand %dp0% if present (resolves relative to the .cmd file's directory)
    exe_rel = exe_rel.replace("%dp0%", cmd_dir + os.sep)
    exe_abs = os.path.normpath(os.path.join(cmd_dir, exe_rel))

    if os.path.isfile(exe_abs):
        logger.debug("Resolved Windows .cmd wrapper %r → .exe %r", exec_path, exe_abs)
        return exe_abs
    return exec_path  # .exe not found, fall back to .cmd


# ─── abstract CLI adapter base ────────────────────────────────


class CLIAdapterBase(AgentPlatformAdapter, ABC):
    """Base class for CLI-spawning adapters (Claude Code, Codex, ...).

    Subclasses implement three methods:
      - ``_build_args(input) → list[str]``
      - ``_write_prompt(proc, input) → None``
      - ``_read_events(proc, input, cancel_event) → AsyncIterator[StreamEvent]``

    The base class handles process lifecycle, graceful shutdown, and
    the top-level ``stream()`` contract.
    """

    def __init__(self, executable_path: str, extra_env: dict[str, str] | None = None):
        self._executable_path = executable_path
        self._extra_env = extra_env or {}

    # ── subclasses MUST implement ──────────────────────────────

    @abstractmethod
    def _build_args(self, input: AdapterInput) -> list[str]:
        """Build the CLI argument list (excluding the executable)."""
        ...

    @abstractmethod
    async def _write_prompt(
        self, proc: asyncio.subprocess.Process, input: AdapterInput
    ) -> None:
        """Write the prompt to the CLI's stdin in its expected format."""
        ...

    @abstractmethod
    async def _read_events(
        self,
        proc: asyncio.subprocess.Process,
        input: AdapterInput,
        cancel_event: asyncio.Event,
    ) -> AsyncIterator[StreamEvent]:
        """Read CLI stdout and yield translated StreamEvent objects."""
        ...

    # ── template method ────────────────────────────────────────

    async def stream(
        self, input: AdapterInput, cancel_event: asyncio.Event
    ) -> AsyncIterator[StreamEvent]:
        """Spawn CLI subprocess, feed prompt, translate output to events."""
        exec_path = input.executable_path or self._executable_path
        if not exec_path:
            raise ValueError(
                f"{self.name}: executable_path is required; configure it on the "
                "agent or pass it via AdapterInput"
            )

        # On Windows, resolve .cmd wrappers to the underlying .exe so stdio
        # pipes connect directly without cmd.exe in the middle.
        if sys.platform == "win32":
            exec_path = _resolve_windows_exe(exec_path)

        args = self._build_args(input)
        env = build_child_env({**self._extra_env, **(input.extra_env or {})})
        cwd = input.workspace_path or None

        # Ensure the workspace directory exists before spawning (sandbox dirs
        # may not have been created for old conversations or after cleanup).
        if cwd:
            os.makedirs(cwd, exist_ok=True)

        logger.info("[%s] spawning: %s %s", self.name, exec_path, " ".join(args))

        # CREATE_NO_WINDOW (0x08000000) prevents a console window from popping
        # up on Windows when spawning a CLI subprocess.
        popen_kwargs = {
            "stdin": asyncio.subprocess.PIPE,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
            "cwd": cwd,
            "env": env,
            "creationflags": subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        }

        try:
            proc = await asyncio.create_subprocess_exec(
                exec_path, *args, **popen_kwargs
            )
        except NotImplementedError:
            if sys.platform != "win32":
                raise
            loop_type = type(asyncio.get_running_loop()).__name__
            logger.warning(
                "[%s] asyncio.create_subprocess_exec not supported on this "
                "event loop (%s); falling back to thread-based Popen wrapper. "
                "Consider adding asyncio.set_event_loop_policy("
                "asyncio.WindowsProactorEventLoopPolicy()) at startup.",
                self.name,
                loop_type,
            )
            # Drop creationflags from kwargs — it's passed positionally
            proc = await _spawn_subprocess_fallback(
                exec_path,
                *args,
                cwd=cwd,
                env=env,
                creationflags=popen_kwargs["creationflags"],
            )

        cli = CLIProcess(proc=proc, cancel_event=cancel_event)

        try:
            # Write prompt from a task so it doesn't deadlock if the CLI's
            # stdout buffer fills before it reads stdin.
            write_task = asyncio.create_task(self._write_prompt(proc, input))

            async for event in self._read_events(proc, input, cancel_event):
                if cancel_event.is_set():
                    break
                yield event

            await write_task
        except asyncio.CancelledError:
            cancel_event.set()
        finally:
            # Ensure prompt write is done and stdin is closed
            if proc.stdin and not proc.stdin.is_closing():
                try:
                    proc.stdin.close()
                except Exception:
                    pass
            await cli.shutdown()

    # ── helpers subclasses may use ─────────────────────────────

    @staticmethod
    def _yield(msg: StreamEvent) -> StreamEvent:
        """Return a StreamEvent (syntactic sugar for yield in helper methods)."""
        return msg

    @staticmethod
    async def _consume_stderr(proc: asyncio.subprocess.Process, prefix: str) -> str:
        """Read all of stderr (non-blocking; for post-mortem diagnostics)."""
        if not proc.stderr:
            return ""
        try:
            data = await proc.stderr.read()
            return data.decode("utf-8", errors="replace")[:4096]
        except Exception:
            return ""
