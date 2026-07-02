"""Subprocess spawn compatibility helper for Windows SelectorEventLoop.

On Windows, ``asyncio.create_subprocess_exec`` requires the ProactorEventLoop.
When the running loop is a SelectorEventLoop (uvicorn --reload sometimes
selects it), ``create_subprocess_exec`` raises ``NotImplementedError``.

This module provides :func:`spawn_subprocess` — a drop-in that tries the
native asyncio spawn first and falls back to a thread-backed
``subprocess.Popen`` wrapper that quacks like ``asyncio.subprocess.Process``.

Both the bash tool and the CLI adapters use this so subprocess execution
works regardless of which event loop uvicorn picked.
"""

from __future__ import annotations

import asyncio
import contextlib
import subprocess
import sys
from typing import Any


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
        with contextlib.suppress(Exception):
            self._stream.close()

    def is_closing(self) -> bool:
        return self._closed


class AsyncProcessWrapper:
    """Wraps subprocess.Popen to quack like asyncio.subprocess.Process."""

    def __init__(self, popen: subprocess.Popen, loop: asyncio.AbstractEventLoop):
        self._popen = popen
        self.stdin = _AsyncStreamWriter(popen.stdin, loop) if popen.stdin else None
        self.stdout = _AsyncStreamReader(popen.stdout, loop) if popen.stdout else None
        self.stderr = _AsyncStreamReader(popen.stderr, loop) if popen.stderr else None

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
        with contextlib.suppress(ProcessLookupError):
            self._popen.terminate()

    def kill(self) -> None:
        with contextlib.suppress(ProcessLookupError):
            self._popen.kill()


async def spawn_subprocess(
    program: str,
    *args: str,
    **kwargs: Any,
):
    """Spawn a subprocess, falling back to a Popen wrapper on SelectorEventLoop.

    Accepts the same kwargs as ``asyncio.create_subprocess_exec`` (cwd, env,
    stdin, stdout, stderr, creationflags, start_new_session, ...). Returns an
    object with the ``asyncio.subprocess.Process`` interface.
    """
    try:
        return await asyncio.create_subprocess_exec(program, *args, **kwargs)
    except NotImplementedError:
        if sys.platform != "win32":
            raise
        # SelectorEventLoop fallback: spawn synchronously, wrap for async I/O.
        loop = asyncio.get_running_loop()
        popen = subprocess.Popen([program, *args], **kwargs)
        return AsyncProcessWrapper(popen, loop)
