"""Argv-safe, cancellable execution of the verified CodeGraph runtime."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.code_intelligence.progress import CodeGraphProgressTracker, ProgressCallback
from app.code_intelligence.runtime import ResolvedRuntime, RuntimeManager

CodeGraphOperation = Literal["init", "sync", "rebuild", "status", "explore"]
MAX_CAPTURE_CHARS = 1_000_000
POST_INDEX_STATUS_TIMEOUT = 180.0

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str


class CodeGraphCommandRunner:
    def __init__(self, runtime_manager: RuntimeManager) -> None:
        self.runtime_manager = runtime_manager

    async def run_index(
        self,
        project_path: Path,
        operation: str,
        cancel_event: asyncio.Event,
        progress_callback: ProgressCallback | None = None,
    ) -> dict[str, int]:
        if operation not in {"init", "sync", "rebuild"}:
            raise ValueError(f"Unsupported CodeGraph index operation: {operation}")
        runtime = self._verified_runtime()
        project = Path(project_path).resolve()
        tracker = CodeGraphProgressTracker()

        def on_stdout_line(line: str) -> None:
            progress = tracker.feed(line)
            if progress is not None and progress_callback is not None:
                progress_callback(progress)

        await self._execute(
            runtime,
            operation,
            project,
            cancel_event=cancel_event,
            on_stdout_line=on_stdout_line if progress_callback is not None else None,
        )
        try:
            status = await self._execute(
                runtime,
                "status",
                project,
                cancel_event=cancel_event,
                timeout_override=POST_INDEX_STATUS_TIMEOUT,
            )
        except (TimeoutError, RuntimeError) as exc:
            logger.warning(
                "CodeGraph status failed after %s for %s; "
                "index is valid but counts unavailable: %s",
                operation,
                project,
                exc,
            )
            return {"files": 0, "symbols": 0, "relationships": 0}
        return parse_status_counts(status.stdout)

    async def explore(
        self,
        project_path: Path,
        query: str,
        cancel_event: asyncio.Event,
    ) -> str:
        runtime = self._verified_runtime()
        result = await self._execute(
            runtime,
            "explore",
            Path(project_path).resolve(),
            query=query,
            cancel_event=cancel_event,
        )
        return result.stdout

    async def is_stale(
        self,
        project_path: Path,
        cancel_event: asyncio.Event,
    ) -> bool:
        runtime = self._verified_runtime()
        try:
            result = await self._execute(
                runtime,
                "status",
                Path(project_path).resolve(),
                cancel_event=cancel_event,
            )
        except TimeoutError:
            logger.warning(
                "CodeGraph status timed out for %s; assuming not stale",
                project_path,
            )
            return False
        try:
            pending = json.loads(result.stdout).get("pendingChanges", {})
        except (json.JSONDecodeError, AttributeError) as exc:
            raise RuntimeError("CodeGraph status returned invalid JSON") from exc
        return any(int(pending.get(key, 0)) > 0 for key in ("added", "modified", "removed"))

    async def _execute(
        self,
        runtime: ResolvedRuntime,
        operation: CodeGraphOperation,
        project_path: Path,
        *,
        cancel_event: asyncio.Event,
        query: str | None = None,
        on_stdout_line: Callable[[str], None] | None = None,
        timeout_override: float | None = None,
    ) -> ProcessResult:
        argv = self.build_argv(
            runtime,
            operation=operation,
            project_path=project_path,
            query=query,
        )
        for required in argv[:2]:
            if Path(required).is_absolute() and not Path(required).exists():
                raise RuntimeError(f"Verified CodeGraph runtime file is missing: {required}")
        if timeout_override is not None:
            timeout = timeout_override
        else:
            timeout = 600.0 if operation in {"init", "rebuild"} else 180.0 if operation == "sync" else 60.0
        env = os.environ.copy()
        env.update(
            {
                "CODEGRAPH_NO_DOWNLOAD": "1",
                "CODEGRAPH_NO_DAEMON": "1",
                "CODEGRAPH_NO_WATCH": "1",
                "CODEGRAPH_ASCII": "1",
                "NO_COLOR": "1",
            }
        )
        result = await run_process(
            argv,
            cwd=project_path,
            cancel_event=cancel_event,
            timeout_seconds=timeout,
            env=env,
            on_stdout_line=on_stdout_line,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[:4000]
            raise RuntimeError(
                f"CodeGraph {operation} failed with exit code {result.returncode}: {detail}"
            )
        return result

    def _verified_runtime(self) -> ResolvedRuntime:
        runtime = self.runtime_manager.resolve(download_approved=False)
        if runtime.source not in {"packaged", "cache"}:
            raise RuntimeError("CodeGraph runtime is not packaged or verified")
        return runtime

    @staticmethod
    def build_argv(
        runtime: ResolvedRuntime,
        *,
        operation: CodeGraphOperation,
        project_path: Path,
        query: str | None = None,
    ) -> list[str]:
        root = runtime.root.resolve()
        node = root / ("node.exe" if runtime.artifact.platform_key.startswith("win32-") else "node")
        cli = root / "lib" / "dist" / "bin" / "codegraph.js"
        base = [str(node), "--liftoff-only", str(cli)]
        project = str(Path(project_path).resolve())
        if operation == "init":
            return [*base, "init", project, "--index", "--verbose"]
        if operation == "sync":
            return [*base, "sync", project]
        if operation == "rebuild":
            return [*base, "index", project, "--force", "--verbose"]
        if operation == "status":
            return [*base, "status", project, "--json"]
        if operation == "explore":
            if query is None:
                raise ValueError("CodeGraph explore requires a query")
            return [
                *base,
                "context",
                query,
                "--path",
                project,
                "--max-nodes",
                "50",
                "--max-code",
                "10",
                "--format",
                "markdown",
            ]
        raise ValueError(f"Unsupported CodeGraph operation: {operation}")


async def run_process(
    argv: list[str],
    *,
    cwd: Path,
    cancel_event: asyncio.Event,
    timeout_seconds: float,
    env: dict[str, str] | None = None,
    on_stdout_line: Callable[[str], None] | None = None,
) -> ProcessResult:
    if os.name == "nt":
        return await _run_process_windows(
            argv,
            cwd=cwd,
            cancel_event=cancel_event,
            timeout_seconds=timeout_seconds,
            env=env,
            on_stdout_line=on_stdout_line,
        )

    kwargs: dict[str, object] = {
        "cwd": str(Path(cwd).resolve()),
        "stdin": asyncio.subprocess.DEVNULL,
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
        "env": env,
    }
    if os.name == "nt":
        kwargs["creationflags"] = 0x00000200  # CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    process = await asyncio.create_subprocess_exec(*argv, **kwargs)
    if on_stdout_line is None:
        communicate_task = asyncio.create_task(process.communicate())
    else:
        communicate_task = asyncio.create_task(
            _communicate_async(process, on_stdout_line)
        )
    cancel_task = asyncio.create_task(cancel_event.wait())
    try:
        done, _ = await asyncio.wait(
            {communicate_task, cancel_task},
            timeout=timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if communicate_task in done:
            stdout, stderr = await communicate_task
            return ProcessResult(
                process.returncode or 0,
                stdout.decode("utf-8", errors="replace")[:MAX_CAPTURE_CHARS],
                stderr.decode("utf-8", errors="replace")[:MAX_CAPTURE_CHARS],
            )

        await _terminate_process_tree(process)
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await communicate_task
        if cancel_task in done and cancel_event.is_set():
            raise asyncio.CancelledError
        raise TimeoutError(f"Process timed out after {timeout_seconds:g}s: {argv[0]}")
    finally:
        cancel_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cancel_task
        if not communicate_task.done():
            communicate_task.cancel()


async def _run_process_windows(
    argv: list[str],
    *,
    cwd: Path,
    cancel_event: asyncio.Event,
    timeout_seconds: float,
    env: dict[str, str] | None,
    on_stdout_line: Callable[[str], None] | None,
) -> ProcessResult:
    process = subprocess.Popen(
        argv,
        cwd=str(Path(cwd).resolve()),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
    )
    communicate_task = asyncio.create_task(
        asyncio.to_thread(_communicate_windows, process, on_stdout_line)
    )
    cancel_task = asyncio.create_task(cancel_event.wait())
    try:
        done, _ = await asyncio.wait(
            {communicate_task, cancel_task},
            timeout=timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if communicate_task in done:
            stdout, stderr = await communicate_task
            return ProcessResult(
                process.returncode or 0,
                stdout.decode("utf-8", errors="replace")[:MAX_CAPTURE_CHARS],
                stderr.decode("utf-8", errors="replace")[:MAX_CAPTURE_CHARS],
            )

        await asyncio.to_thread(_terminate_windows_process_tree, process)
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await communicate_task
        if cancel_task in done and cancel_event.is_set():
            raise asyncio.CancelledError
        raise TimeoutError(f"Process timed out after {timeout_seconds:g}s: {argv[0]}")
    finally:
        cancel_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cancel_task
        if not communicate_task.done():
            communicate_task.cancel()


async def _communicate_async(
    process: asyncio.subprocess.Process,
    on_stdout_line: Callable[[str], None],
) -> tuple[bytes, bytes]:
    if process.stdout is None or process.stderr is None:
        raise RuntimeError("Process output pipes are unavailable")

    async def drain(
        stream: asyncio.StreamReader,
        callback: Callable[[str], None] | None,
    ) -> bytes:
        captured = bytearray()
        while line := await stream.readline():
            _append_bounded(captured, line)
            if callback is not None:
                callback(line.decode("utf-8", errors="replace").rstrip("\r\n"))
        return bytes(captured)

    stdout, stderr = await asyncio.gather(
        drain(process.stdout, on_stdout_line),
        drain(process.stderr, None),
    )
    await process.wait()
    return stdout, stderr


def _communicate_windows(
    process: subprocess.Popen[bytes],
    on_stdout_line: Callable[[str], None] | None,
) -> tuple[bytes, bytes]:
    if process.stdout is None or process.stderr is None:
        raise RuntimeError("Process output pipes are unavailable")

    stdout = bytearray()
    stderr = bytearray()

    def drain(
        stream,
        captured: bytearray,
        callback: Callable[[str], None] | None,
    ) -> None:
        while line := stream.readline():
            _append_bounded(captured, line)
            if callback is not None:
                callback(line.decode("utf-8", errors="replace").rstrip("\r\n"))

    readers = (
        threading.Thread(
            target=drain,
            args=(process.stdout, stdout, on_stdout_line),
            daemon=True,
        ),
        threading.Thread(
            target=drain,
            args=(process.stderr, stderr, None),
            daemon=True,
        ),
    )
    for reader in readers:
        reader.start()
    process.wait()
    for reader in readers:
        reader.join()
    return bytes(stdout), bytes(stderr)


def _append_bounded(captured: bytearray, chunk: bytes) -> None:
    remaining = MAX_CAPTURE_CHARS - len(captured)
    if remaining > 0:
        captured.extend(chunk[:remaining])


def _terminate_windows_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    if process.poll() is None:
        process.kill()
    with contextlib.suppress(Exception):
        process.wait(timeout=5)


async def _terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt":
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(killer.wait(), timeout=2)
        except (OSError, TimeoutError):
            pass
        if process.returncode is None:
            process.kill()
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(Exception):
        await asyncio.wait_for(process.wait(), timeout=5)


def parse_status_counts(raw: str) -> dict[str, int]:
    try:
        status = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("CodeGraph status returned invalid JSON") from exc

    def bounded(name: str) -> int:
        value = int(status.get(name, 0))
        return max(0, min(value, 1_000_000_000))

    return {
        "files": bounded("fileCount"),
        "symbols": bounded("nodeCount"),
        "relationships": bounded("edgeCount"),
    }
