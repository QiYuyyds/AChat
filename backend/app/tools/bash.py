"""bash tool — run a shell command inside the workspace.

Port of src/server/tools/bash.ts. See specs/07-tools.md, specs/11-platform.md.

Differences from the Node version, by necessity:
  - Node child_process → :mod:`asyncio` subprocess. Process-group kill uses
    ``start_new_session`` + ``os.killpg`` on POSIX and ``taskkill /F /T`` on
    Windows.
  - stdout + stderr are merged (``stderr=STDOUT``) and truncated to 10k chars.
  - After the shell exits, a short grace waits for the output pipe to close;
    if a backgrounded child still holds it open we kill the group and note it.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import subprocess
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from app.db.engine import get_db
from app.db.models import Workspace
from app.services.bash_command_approval import (
    classify_bash_approval,
    wait_for_bash_approval,
)
from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok
from app.utils.dispatch_run_evidence import RunCommandEvidence, record_run_command
from app.utils.platform import IS_WINDOWS
from app.utils.security import find_banned_pattern
from app.utils.workspace_utils import assert_path_within_workspace, get_effective_cwd

_PLATFORM = "windows" if IS_WINDOWS else "posix"

DEFAULT_TIMEOUT_MS = 30_000
MIN_TIMEOUT_MS = 1_000
MAX_TIMEOUT_MS = 15 * 60_000
MAX_OUTPUT_CHARS = 10_000
_ORPHANED_STDIO_GRACE_S = 0.5
_POSIX_LOGIN_INTERACTIVE_SHELLS = {"bash", "zsh"}


@dataclass
class BashExecutionArgs:
    command: str
    cwd: str | None = None
    timeout_ms: int | None = None
    evidence_kind: str | None = None  # 'prepare' | 'verification'


class _Args(BaseModel):
    command: str = Field(min_length=1)
    cwd: str | None = Field(default=None, min_length=1)
    timeout_ms: int | None = Field(default=None, alias="timeoutMs", gt=0)

    model_config = {"populate_by_name": True}


_DESCRIPTION_POSIX = (
    "Run a shell command inside the workspace. Optional cwd must stay inside the "
    "workspace. POSIX uses the user login shell for zsh/bash ($SHELL -l -i -c) when "
    "available, otherwise sh -c. Use POSIX syntax: ls, grep, cat, git, npm, python, "
    "etc. Output is stdout + stderr combined, truncated to 10000 chars, 30s timeout. "
    "Destructive commands (rm -rf /, sudo, fork bombs, curl | sh) are blocked. No "
    "interactive stdin. Do not leave persistent background servers running; start "
    "test servers only inside a command that cleans them up."
)

_DESCRIPTION_WINDOWS = (
    "Run a Windows PowerShell 5.1 command inside the workspace. Optional cwd must "
    "stay inside the workspace. CRITICAL: this is Windows, not Linux/macOS. You MUST "
    "use PowerShell syntax; POSIX flags like `-la`, `-rf` do not work. Examples of "
    "correct commands: `Get-ChildItem -Force` (NOT `ls -la`), `Get-Content file.txt` "
    "(NOT `cat`), `Select-String pattern file.txt` (NOT `grep`), `Remove-Item path` "
    "(NOT `rm`), `New-Item -ItemType Directory` (NOT `mkdir -p`), `Copy-Item src dst` "
    "(NOT `cp`), `Move-Item src dst` (NOT `mv`). git/npm/python/node work as usual. "
    "Output is UTF-8, stdout + stderr combined, truncated to 10000 chars. Destructive "
    "commands (Remove-Item -Recurse -Force, format, shutdown, iex(iwr ...), reg "
    "delete, Set-ExecutionPolicy Unrestricted) are blocked. No interactive stdin. Do "
    "not leave persistent background servers running; start test servers only inside "
    "a command that cleans them up."
)

_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["command"],
    "properties": {
        "command": {
            "type": "string",
            "description": (
                "PowerShell command to execute. Use cwd instead of Set-Location."
                if IS_WINDOWS
                else "Shell command to execute. Use cwd instead of cd."
            ),
        },
        "cwd": {
            "type": "string",
            "description": (
                'Optional workspace-relative directory to run from, such as "frontend" '
                'or "backend". It must resolve inside the workspace.'
            ),
        },
        "timeoutMs": {
            "type": "number",
            "description": (
                "Optional timeout in milliseconds. Values are clamped to AChat "
                "safety bounds."
            ),
        },
    },
}


async def _handler(args: Any, ctx: ToolContext) -> ToolResult:
    try:
        parsed = _Args.model_validate(args)
    except ValidationError as e:
        return err(f"Invalid args: {e}")
    return await execute_bash_command(
        BashExecutionArgs(
            command=parsed.command, cwd=parsed.cwd, timeout_ms=parsed.timeout_ms
        ),
        ctx,
    )


async def execute_bash_command(args: BashExecutionArgs, ctx: ToolContext) -> ToolResult:
    banned = find_banned_pattern(args.command, _PLATFORM)
    if banned:
        return err(f"Command rejected by safety policy: {banned}")

    async with get_db() as db:
        result = await db.execute(
            select(Workspace).where(Workspace.conversation_id == ctx.conversation_id)
        )
        workspace = result.scalar_one_or_none()
    if workspace is None:
        return err("Workspace not found")

    cwd = get_effective_cwd(workspace)
    if args.cwd:
        try:
            cwd = assert_path_within_workspace(workspace, args.cwd)
        except ValueError as e:
            return err(str(e))
        if not os.path.isdir(cwd):
            return err(f"cwd is not a directory: {args.cwd}")

    approval = classify_bash_approval(args.command, _PLATFORM)
    if approval.required:
        approved = await wait_for_bash_approval(
            conversation_id=ctx.conversation_id,
            agent_id=ctx.agent_id,
            run_id=ctx.run_id,
            command=args.command,
            cwd=cwd,
            reason=approval.reason,
            cancel_event=ctx.cancel_event,
        )
        if not approved:
            return err(f"User rejected command execution: {approval.reason}")

    return await _run_shell_command(args, cwd, ctx)


def _clamp_timeout(timeout_ms: int | None) -> int:
    if timeout_ms is None:
        return DEFAULT_TIMEOUT_MS
    return max(MIN_TIMEOUT_MS, min(timeout_ms, MAX_TIMEOUT_MS))


def _resolve_posix_user_shell() -> str | None:
    candidate = os.environ.get("SHELL")
    if candidate and candidate.startswith("/") and os.path.exists(candidate):
        return candidate
    return None


def _build_shell_invocation(command: str) -> tuple[str, list[str]]:
    if IS_WINDOWS:
        preamble = (
            "$OutputEncoding = [Console]::OutputEncoding = "
            "[System.Text.UTF8Encoding]::new();"
        )
        powershell = shutil.which("powershell.exe") or "powershell.exe"
        return powershell, ["-NoProfile", "-NonInteractive", "-Command", f"{preamble} {command}"]

    user_shell = _resolve_posix_user_shell()
    if user_shell and os.path.basename(user_shell) in _POSIX_LOGIN_INTERACTIVE_SHELLS:
        return user_shell, ["-l", "-i", "-c", command]
    return "sh", ["-c", command]


def _kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    if proc.pid is None:
        return
    if IS_WINDOWS:
        try:
            killer = subprocess.Popen(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            killer.wait(timeout=5)
        except Exception:  # noqa: BLE001 - best-effort kill
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        with contextlib.suppress(ProcessLookupError):
            proc.kill()


async def _run_shell_command(
    args: BashExecutionArgs, cwd: str, ctx: ToolContext
) -> ToolResult:
    timeout_ms = _clamp_timeout(args.timeout_ms)
    cmd, cmd_args = _build_shell_invocation(args.command)

    kwargs: dict[str, Any] = {
        "cwd": cwd,
        "env": os.environ.copy(),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.STDOUT,
    }
    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    try:
        proc = await asyncio.create_subprocess_exec(cmd, *cmd_args, **kwargs)
    except (OSError, ValueError) as e:
        error = f"Spawn failed: {e}"
        record_run_command(
            ctx.run_id,
            RunCommandEvidence(
                command=args.command,
                cwd=cwd,
                exit_code=None,
                timed_out=False,
                is_error=True,
                prepare=args.evidence_kind == "prepare",
                error=error,
            ),
        )
        return err(error)

    buffer = ""
    truncated = False

    async def _read_output() -> None:
        nonlocal buffer, truncated
        assert proc.stdout is not None
        while True:
            chunk = await proc.stdout.read(4096)
            if not chunk:
                break
            if truncated:
                continue
            text = chunk.decode("utf-8", errors="replace")
            if len(buffer) + len(text) <= MAX_OUTPUT_CHARS:
                buffer += text
            else:
                buffer = (buffer + text)[:MAX_OUTPUT_CHARS]
                truncated = True

    reader = asyncio.ensure_future(_read_output())
    wait_task = asyncio.ensure_future(proc.wait())
    cancel_task = asyncio.ensure_future(ctx.cancel_event.wait())

    timed_out = False
    aborted = False
    orphaned_stdio = False

    done, _ = await asyncio.wait(
        {wait_task, cancel_task},
        timeout=timeout_ms / 1000,
        return_when=asyncio.FIRST_COMPLETED,
    )

    if not done:
        timed_out = True
        _kill_process_tree(proc)
    elif cancel_task in done:
        aborted = True
        _kill_process_tree(proc)

    cancel_task.cancel()

    # Ensure the process is reaped, then drain the reader with a grace window.
    try:
        await asyncio.wait_for(wait_task, timeout=5)
    except TimeoutError:
        _kill_process_tree(proc)
    exit_code = proc.returncode

    try:
        await asyncio.wait_for(asyncio.shield(reader), timeout=_ORPHANED_STDIO_GRACE_S)
    except TimeoutError:
        orphaned_stdio = True
        _kill_process_tree(proc)
        reader.cancel()

    note = ""
    if timed_out:
        note = f"\n\n[KILLED after {timeout_ms / 1000}s timeout]"
    elif aborted:
        note = "\n\n[KILLED after run abort]"
    orphan_note = (
        "\n\n[STOPPED background processes after shell exit to close inherited stdio]"
        if orphaned_stdio
        else ""
    )
    trunc_note = f"\n\n[TRUNCATED at {MAX_OUTPUT_CHARS} chars]" if truncated else ""

    record_run_command(
        ctx.run_id,
        RunCommandEvidence(
            command=args.command,
            cwd=cwd,
            exit_code=exit_code,
            timed_out=timed_out,
            is_error=False,
            prepare=args.evidence_kind == "prepare",
        ),
    )

    return ok(
        {
            "cwd": cwd,
            "command": args.command,
            "exitCode": exit_code,
            "output": buffer + trunc_note + note + orphan_note,
            "truncated": truncated,
            "timedOut": timed_out,
        }
    )


bash_tool = ToolDef(
    name="bash",
    description=_DESCRIPTION_WINDOWS if IS_WINDOWS else _DESCRIPTION_POSIX,
    parameters=_PARAMETERS,
    handler=_handler,
)
