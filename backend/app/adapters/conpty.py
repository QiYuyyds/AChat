"""Windows ConPTY (Pseudo Console) process spawning.

When a process's stdout is a pipe (not a TTY), the C runtime switches from
line-buffered to block-buffered output (~4KB chunks). On Windows there is no
``stdbuf`` equivalent, so the only reliable way to get line-buffered output
from a subprocess is to give it a pseudo-terminal — i.e. ConPTY.

This module provides :func:`spawn_conpty` — a drop-in replacement for
``asyncio.create_subprocess_exec`` on Windows that routes the child's stdio
through a ConPTY. The returned object quacks like
``asyncio.subprocess.Process`` so the rest of the codebase doesn't change.

Reference:
  https://learn.microsoft.com/en-us/windows/console/creating-a-pseudoconsole-session

Requirements: Windows 10 1809+ (build 17763). The caller must guard with
``sys.platform == "win32"`` before importing or calling this module.
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import logging
import os
import subprocess
import sys
from ctypes import wintypes
from ctypes.wintypes import BOOL, DWORD, HANDLE

logger = logging.getLogger(__name__)

# ── Windows API bindings ──────────────────────────────────────────

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

HRESULT = ctypes.c_long
HPCON = HANDLE  # pseudo console handle (opaque pointer-sized value)


class COORD(ctypes.Structure):
    """Windows COORD structure for terminal dimensions."""
    _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]


# CreatePseudoConsole / ClosePseudoConsole (kernel32, Win10 1809+)

kernel32.CreatePseudoConsole.argtypes = [
    COORD,                    # size
    HANDLE,                   # hInput  — read end of input pipe
    HANDLE,                   # hOutput — write end of output pipe
    DWORD,                    # dwFlags (reserved, 0)
    ctypes.POINTER(HPCON),    # *phPC
]
kernel32.CreatePseudoConsole.restype = HRESULT

kernel32.ClosePseudoConsole.argtypes = [HPCON]
kernel32.ClosePseudoConsole.restype = None

# ── Constants ──────────────────────────────────────────────────────

_PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
_EXTENDED_STARTUPINFO_PRESENT      = 0x00080000
_STARTF_USESTDHANDLES              = 0x00000100
_STILL_ACTIVE                      = 259
_ATTR_LIST_COUNT                   = 1  # we only need one attribute slot


# ── STARTUPINFOEX for CreateProcessW ───────────────────────────────

class _STARTUPINFOW(ctypes.Structure):
    """Minimal STARTUPINFOW for ConPTY. Field order matches Windows SDK."""
    _fields_ = [
        ("cb",              DWORD),
        ("lpReserved",      wintypes.LPWSTR),
        ("lpDesktop",       wintypes.LPWSTR),
        ("lpTitle",         wintypes.LPWSTR),
        ("dwX",             DWORD),
        ("dwY",             DWORD),
        ("dwXSize",         DWORD),
        ("dwYSize",         DWORD),
        ("dwXCountChars",   DWORD),
        ("dwYCountChars",   DWORD),
        ("dwFillAttribute", DWORD),
        ("dwFlags",         DWORD),
        ("wShowWindow",     wintypes.WORD),
        ("cbReserved2",     wintypes.WORD),
        ("lpReserved2",     ctypes.POINTER(wintypes.BYTE)),
        ("hStdInput",       HANDLE),
        ("hStdOutput",      HANDLE),
        ("hStdError",       HANDLE),
    ]


class _STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [
        ("StartupInfo",     _STARTUPINFOW),
        ("lpAttributeList", ctypes.c_void_p),
    ]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess",    HANDLE),
        ("hThread",     HANDLE),
        ("dwProcessId", DWORD),
        ("dwThreadId",  DWORD),
    ]


# ── CreateProcessW ─────────────────────────────────────────────────

kernel32.CreateProcessW.argtypes = [
    wintypes.LPCWSTR,                        # lpApplicationName
    wintypes.LPWSTR,                         # lpCommandLine
    ctypes.c_void_p,                         # lpProcessAttributes
    ctypes.c_void_p,                         # lpThreadAttributes
    BOOL,                                    # bInheritHandles
    DWORD,                                   # dwCreationFlags
    ctypes.c_void_p,                         # lpEnvironment
    wintypes.LPCWSTR,                        # lpCurrentDirectory
    ctypes.POINTER(_STARTUPINFOEXW),         # lpStartupInfo
    ctypes.POINTER(_PROCESS_INFORMATION),    # lpProcessInformation
]
kernel32.CreateProcessW.restype = BOOL

# Proc thread attribute list API
kernel32.InitializeProcThreadAttributeList.argtypes = [
    ctypes.c_void_p, ctypes.c_uint, DWORD, ctypes.POINTER(ctypes.c_size_t),
]
kernel32.InitializeProcThreadAttributeList.restype = BOOL

kernel32.UpdateProcThreadAttribute.argtypes = [
    ctypes.c_void_p, DWORD, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p,
]
kernel32.UpdateProcThreadAttribute.restype = BOOL

kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
kernel32.DeleteProcThreadAttributeList.restype = None

# Exit code polling
kernel32.GetExitCodeProcess.argtypes = [HANDLE, ctypes.POINTER(DWORD)]
kernel32.GetExitCodeProcess.restype = BOOL

# Wait / terminate
kernel32.WaitForSingleObject.argtypes = [HANDLE, DWORD]
kernel32.WaitForSingleObject.restype = DWORD
kernel32.TerminateProcess.argtypes = [HANDLE, DWORD]
kernel32.TerminateProcess.restype = BOOL


# ── Helpers ────────────────────────────────────────────────────────

def _check_win32(ok, operation: str) -> None:
    if not ok:
        err = ctypes.get_last_error()
        raise OSError(f"{operation} failed: error {err}")


def _check_hresult(hr: int, operation: str) -> None:
    if hr != 0:
        raise OSError(f"{operation} failed: HRESULT 0x{hr & 0xFFFFFFFF:08X}")


def _build_env_block(env: dict[str, str]) -> str:
    """Windows unicode environment block: key=val\\0key=val\\0\\0."""
    return "\0".join(f"{k}={v}" for k, v in env.items()) + "\0\0"


# ── Proc thread attribute list (1-slot: ConPTY handle) ────────────

def _create_attr_list(hpc_value: int):
    """Allocate + initialise attribute list and attach the ConPTY handle."""
    # Query required size
    size = ctypes.c_size_t(0)
    kernel32.InitializeProcThreadAttributeList(None, _ATTR_LIST_COUNT, 0, ctypes.byref(size))
    buf = (ctypes.c_char * size.value)()
    lp = ctypes.cast(buf, ctypes.c_void_p)
    _check_win32(
        kernel32.InitializeProcThreadAttributeList(lp, _ATTR_LIST_COUNT, 0, ctypes.byref(size)),
        "InitializeProcThreadAttributeList",
    )
    # Attach the pseudo console
    attr_id = ctypes.c_void_p(_PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE)
    hpc_ptr = ctypes.c_void_p(hpc_value)
    _check_win32(
        kernel32.UpdateProcThreadAttribute(
            lp, 0, attr_id, hpc_ptr,
            ctypes.sizeof(HANDLE), None, None,
        ),
        "UpdateProcThreadAttribute(PSEUDOCONSOLE)",
    )
    return lp, buf


def _destroy_attr_list(lp) -> None:
    kernel32.DeleteProcThreadAttributeList(lp)


# ── Pipe helpers ──────────────────────────────────────────────────

def _create_pipe() -> tuple[int, int]:
    """Create a Windows anonymous pipe → (read_handle, write_handle)."""
    import _winapi
    return _winapi.CreatePipe(None, 0)


# ── Asyncio stream wrappers ───────────────────────────────────────

class _AsyncStreamReader:
    """Read lines from a pipe handle via thread-pool blocking I/O.

    Uses ``msvcrt.open_osfhandle`` + ``os.fdopen`` so that ``readline()``
    (which blocks until a newline or EOF) runs in the thread pool. The
    ConPTY pseudo-terminal ensures the child process line-buffers its
    output, so ``readline()`` returns promptly for each JSON event.
    """

    def __init__(self, pipe_handle: int, loop: asyncio.AbstractEventLoop) -> None:
        import msvcrt
        # Convert the Windows pipe handle to a CRT file descriptor so we
        # can use fdopen / readline.  closefd=False because we close the
        # underlying handle ourselves via os.close.
        fd = msvcrt.open_osfhandle(pipe_handle, os.O_RDONLY)
        self._fd = fd
        self._file = os.fdopen(fd, "rb", buffering=0, closefd=False)
        self._loop = loop
        self._eof = False

    async def readline(self) -> bytes:
        if self._eof:
            return b""
        line = await self._loop.run_in_executor(None, self._file.readline)
        if not line:
            self._eof = True
        return line

    def at_eof(self) -> bool:
        return self._eof

    def feed_eof(self) -> None:
        self._eof = True

    def close(self) -> None:
        self._eof = True
        with contextlib.suppress(OSError):
            self._file.close()
        with contextlib.suppress(OSError):
            os.close(self._fd)


class _AsyncStreamWriter:
    """Write to a pipe handle (synchronous — small JSON payloads)."""

    def __init__(self, pipe_handle: int) -> None:
        self._handle = pipe_handle
        self._closed = False

    def write(self, data: bytes) -> None:
        if self._closed:
            return
        os.write(self._handle, data)

    async def drain(self) -> None:
        pass  # os.write is synchronous; small payloads never need flushing

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            with contextlib.suppress(OSError):
                os.close(self._handle)

    def is_closing(self) -> bool:
        return self._closed


# ── Public API ──────────────────────────────────────────────────────

class ConPTYProcess:
    """A subprocess spawned inside a Windows ConPTY pseudo-console.

    Surface-compatible with ``asyncio.subprocess.Process``:
    ``.stdin``, ``.stdout``, ``.pid``, ``.returncode``, ``.wait()``,
    ``.terminate()``, ``.kill()``.

    **ConPTY merges stdout + stderr** (like a real terminal). ``.stderr``
    is always ``None``.  Non-JSON lines (stderr noise) are harmlessly
    skipped by the JSONL parser in the adapter.
    """

    def __init__(
        self,
        h_process: int,
        hpc: int,
        pid: int,
        stdin_handle: int,
        stdout_handle: int,
        attr_buf,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._h_process = h_process
        self._hpc = hpc
        self._pid = pid
        self._attr_buf = attr_buf  # keep alive until cleanup
        self._loop = loop
        self._returncode: int | None = None

        self.stdin: _AsyncStreamWriter = _AsyncStreamWriter(stdin_handle)
        self.stdout: _AsyncStreamReader = _AsyncStreamReader(stdout_handle, loop)
        self.stderr = None  # merged into stdout by ConPTY

    @property
    def pid(self) -> int:
        return self._pid

    @property
    def returncode(self) -> int | None:
        if self._returncode is not None:
            return self._returncode
        code = DWORD(0)
        if kernel32.GetExitCodeProcess(HANDLE(self._h_process), ctypes.byref(code)):
            if code.value != _STILL_ACTIVE:
                self._returncode = code.value
        return self._returncode

    async def wait(self) -> int:
        if self._returncode is not None:
            return self._returncode
        await self._loop.run_in_executor(
            None,
            kernel32.WaitForSingleObject,
            HANDLE(self._h_process),
            0xFFFFFFFF,  # INFINITE
        )
        code = DWORD(0)
        kernel32.GetExitCodeProcess(HANDLE(self._h_process), ctypes.byref(code))
        self._returncode = code.value
        return self._returncode

    def terminate(self) -> None:
        kernel32.TerminateProcess(HANDLE(self._h_process), 1)

    def kill(self) -> None:
        kernel32.TerminateProcess(HANDLE(self._h_process), 9)

    def cleanup(self) -> None:
        """Release OS resources. Idempotent; safe to call multiple times."""
        if self._hpc:
            with contextlib.suppress(OSError):
                kernel32.ClosePseudoConsole(HPCON(self._hpc))
            self._hpc = 0
        if self._h_process:
            with contextlib.suppress(OSError):
                kernel32.CloseHandle(HANDLE(self._h_process))
            self._h_process = 0
        if self._attr_buf is not None:
            lp = ctypes.cast(self._attr_buf, ctypes.c_void_p)
            with contextlib.suppress(OSError):
                _destroy_attr_list(lp)
            self._attr_buf = None


async def spawn_conpty(
    exec_path: str,
    *args: str,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    creationflags: int = 0,
    loop: asyncio.AbstractEventLoop | None = None,
) -> ConPTYProcess:
    """Spawn a process inside a Windows ConPTY pseudo-console.

    Returns :class:`ConPTYProcess` with ``.stdin`` / ``.stdout`` /
    ``.pid`` / ``.returncode`` / ``.wait()`` / ``.terminate()`` /
    ``.kill()``.
    """
    if loop is None:
        loop = asyncio.get_running_loop()

    # 1. Create OS pipes for ConPTY I/O
    conin_read, conin_write = _create_pipe()
    conout_read, conout_write = _create_pipe()

    # 2. Create the pseudo console
    size = COORD(120, 40)
    hpc = HPCON()
    hr = kernel32.CreatePseudoConsole(
        size,
        HANDLE(conin_read),    # hInput  — child reads from this
        HANDLE(conout_write),  # hOutput — child writes to this
        0,
        ctypes.byref(hpc),
    )
    if hr != 0:
        # Clean up pipes on failure
        for h in (conin_read, conin_write, conout_read, conout_write):
            with contextlib.suppress(OSError):
                os.close(h)
        _check_hresult(hr, "CreatePseudoConsole")

    # ConPTY now owns conin_read and conout_write — do NOT close them.
    # We keep conin_write (to send stdin) and conout_read (to receive output).
    # If anything fails from here on, we must close the ConPTY handle.

    # 3. Create + populate attribute list
    try:
        lp, attr_buf = _create_attr_list(hpc.value)
    except Exception:
        kernel32.ClosePseudoConsole(hpc)
        for h in (conin_write, conout_read):
            with contextlib.suppress(OSError):
                os.close(h)
        raise

    # 4. Set up STARTUPINFOEXW
    si = _STARTUPINFOEXW()
    si.StartupInfo.cb = ctypes.sizeof(_STARTUPINFOEXW)
    si.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
    si.StartupInfo.hStdInput = HANDLE(conin_read)
    si.StartupInfo.hStdOutput = HANDLE(conout_write)
    si.StartupInfo.hStdError = HANDLE(conout_write)
    # lpAttributeList is a void* inside the attr_buf allocation; pass the
    # address of the FIRST byte of buf so CreateProcessW sees it.
    si.lpAttributeList = ctypes.cast(attr_buf, ctypes.c_void_p)

    # 5. Build command line + env
    cmd_line = subprocess.list2cmdline([exec_path] + list(args))
    env_block = _build_env_block(env or {})
    env_ptr = ctypes.create_unicode_buffer(env_block)
    cwd_ptr = ctypes.create_unicode_buffer(cwd) if cwd else None
    dw_flags = DWORD(creationflags | _EXTENDED_STARTUPINFO_PRESENT)

    proc_info = _PROCESS_INFORMATION()

    try:
        _check_win32(
            kernel32.CreateProcessW(
                None, cmd_line, None, None, True,
                dw_flags, env_ptr, cwd_ptr,
                ctypes.byref(si), ctypes.byref(proc_info),
            ),
            f"CreateProcessW: {exec_path}",
        )
    except Exception:
        # Process creation failed — clean up everything we allocated.
        _destroy_attr_list(ctypes.cast(attr_buf, ctypes.c_void_p))
        kernel32.ClosePseudoConsole(hpc)
        for h in (conin_write, conout_read):
            with contextlib.suppress(OSError):
                os.close(h)
        raise

    # 6. Close the thread handle (not needed)
    kernel32.CloseHandle(proc_info.hThread)

    logger.info(
        "[conpty] spawned PID=%d: %s %s",
        proc_info.dwProcessId, exec_path, " ".join(args),
    )

    return ConPTYProcess(
        h_process=proc_info.hProcess.value,
        hpc=hpc.value,
        pid=proc_info.dwProcessId,
        stdin_handle=conin_write,
        stdout_handle=conout_read,
        attr_buf=attr_buf,
        loop=loop,
    )
