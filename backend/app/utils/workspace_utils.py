"""Workspace path helpers.

Port of src/server/workspace-utils.ts. See specs/11-platform.md.

  - get_effective_cwd:          bash / fs tool cwd (local → boundPath, sandbox → rootPath)
  - is_path_within:             cross-platform containment (Windows case-insensitive)
  - resolve_safe_path:          resolve a tool path into the workspace subtree, else None
  - assert_path_within_workspace: same, raising on escape
  - is_path_safe:               reject obviously sensitive system / privacy directories

is_path_safe is "soft security": it does not stop a malicious path (the user can
edit the DB directly), it just blocks the accidental footgun of binding a
conversation workspace to ``~/.ssh`` or ``C:\\Windows``.
"""

import os
import re
from typing import Protocol

from app.utils.platform import IS_WINDOWS


class _WorkspaceLike(Protocol):
    """Minimal shape needed from a Workspace row."""

    mode: str
    bound_path: str | None
    root_path: str


def get_effective_cwd(workspace: _WorkspaceLike) -> str:
    """Local mode runs in the user-bound path; sandbox mode in the internal root."""
    if workspace.mode == "local" and workspace.bound_path:
        return workspace.bound_path
    return workspace.root_path


def _norm(p: str) -> str:
    resolved = os.path.abspath(p)
    return resolved.lower() if IS_WINDOWS else resolved


def is_path_within(child: str, parent: str) -> bool:
    """Subtree containment. Windows is case-insensitive, POSIX case-sensitive."""
    c = _norm(child)
    p = _norm(parent)
    return c == p or c.startswith(p + os.sep)


def resolve_safe_path(workspace: _WorkspaceLike, target: str) -> str | None:
    """Resolve ``target`` (relative or absolute) and force it inside the effective cwd.

    Returns the absolute path, or None on escape (caller decides the response).
    """
    cwd = get_effective_cwd(workspace)
    abs_path = (
        os.path.abspath(target)
        if os.path.isabs(target)
        else os.path.abspath(os.path.join(cwd, target))
    )
    if not is_path_within(abs_path, cwd):
        return None
    return abs_path


def assert_path_within_workspace(workspace: _WorkspaceLike, target: str) -> str:
    """resolve_safe_path that raises instead of returning None."""
    resolved = resolve_safe_path(workspace, target)
    if resolved is None:
        raise ValueError(f'Path "{target}" is outside workspace')
    return resolved


# Windows drive list, cached at module level to avoid repeated stat calls.
_cached_drives: list[str] | None = None


def _get_available_drives() -> list[str]:
    global _cached_drives
    if _cached_drives is not None:
        return _cached_drives
    if not IS_WINDOWS:
        _cached_drives = []
        return _cached_drives
    drives: list[str] = []
    for i in range(ord("A"), ord("Z") + 1):
        root = f"{chr(i)}:\\"
        if os.path.exists(root):
            drives.append(root)
    _cached_drives = drives
    return drives


def _get_system_roots() -> list[str]:
    if not IS_WINDOWS:
        return ["/etc", "/System", "/usr", "/bin", "/sbin", "/var", "/private", "/Library/Keychains"]
    roots: list[str] = []
    for drive in _get_available_drives():
        roots.extend(
            [
                os.path.join(drive, "Windows"),
                os.path.join(drive, "Program Files"),
                os.path.join(drive, "Program Files (x86)"),
                os.path.join(drive, "$Recycle.Bin"),
                os.path.join(drive, "System Volume Information"),
                os.path.join(drive, "Recovery"),
            ]
        )
    sys_drive = os.environ.get("SYSTEMDRIVE", "C:")
    roots.append(os.path.join(sys_drive + "\\", "ProgramData"))
    return roots


def _get_sensitive_segments() -> list[str]:
    shared = [".ssh", ".aws", ".gcloud", ".kube", ".gnupg", ".docker", ".azure"]
    if IS_WINDOWS:
        return [
            *shared,
            r"AppData\Roaming\Microsoft\Credentials",
            r"AppData\Local\Microsoft\Credentials",
            r"AppData\Roaming\Microsoft\Protect",
            r"AppData\Roaming\gh",
            r"AppData\Roaming\Claude",
        ]
    return [
        *shared,
        ".config/gh",
        "Library/Keychains",
        "Library/Application Support/Code/User",
    ]


def is_path_safe(abs_path: str) -> bool:
    """Reject a few classes of obviously sensitive directories.

      - the user's ssh / aws / gcloud / Windows credential dirs, etc.
      - system roots (POSIX: /etc, /usr...; Windows: per-drive \\Windows, \\Program Files...)
      - UNC device paths (\\\\?\\ / \\\\.\\) and plain UNC network shares
      - the user home itself (force at least one level in)
    """
    home = os.path.abspath(os.path.expanduser("~"))
    normalized = os.path.abspath(abs_path)

    # UNC device paths and network shares: reject outright on Windows.
    if IS_WINDOWS and re.match(r"^\\\\[?.]\\", normalized):
        return False
    if IS_WINDOWS and normalized.startswith("\\\\"):
        return False

    # The home directory itself is disallowed (equality only, subdirs are fine).
    home_key = home.lower() if IS_WINDOWS else home
    normalized_key = normalized.lower() if IS_WINDOWS else normalized
    if normalized_key == home_key:
        return False

    # Sensitive sub-paths relative to home.
    for seg in _get_sensitive_segments():
        sensitive = os.path.abspath(os.path.join(home, seg))
        if is_path_within(normalized, sensitive):
            return False

    # System roots.
    return all(not is_path_within(normalized, root) for root in _get_system_roots())
