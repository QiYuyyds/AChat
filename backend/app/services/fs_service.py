"""Workspace filesystem helpers shared by the fs tools and the file-browser API.

Port of src/server/fs-service.ts. All paths go through the workspace sandbox
(``assert_path_within_workspace``); sandbox-mode workspaces additionally enforce
a total quota.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass

from sqlalchemy import select

from app.db.engine import get_db
from app.db.models import Workspace
from app.utils.workspace_utils import assert_path_within_workspace, get_effective_cwd

MAX_READ_BYTES = 1_048_576  # 1 MB
MAX_READ_CHARS = 50_000
MAX_WRITE_BYTES = 100 * 1024  # 100 KB
SANDBOX_TOTAL_BYTES = 100 * 1024 * 1024  # 100 MB
SANDBOX_TOTAL_FILES = 1000


async def get_workspace_for_conversation(conversation_id: str) -> Workspace | None:
    async with get_db() as db:
        result = await db.execute(
            select(Workspace).where(Workspace.conversation_id == conversation_id)
        )
        return result.scalar_one_or_none()


def read_if_exists(workspace: Workspace, target: str) -> str | None:
    """Read a workspace file, returning None if missing/too large (for diffs)."""
    try:
        abs_path = assert_path_within_workspace(workspace, target)
    except ValueError:
        return None
    try:
        if not os.path.exists(abs_path) or not os.path.isfile(abs_path):
            return None
        if os.path.getsize(abs_path) > MAX_READ_BYTES:
            return None
        with open(abs_path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


@dataclass
class ReadResult:
    path: str
    absolute_path: str
    cwd: str
    size: int
    content: str
    truncated: bool


def read_file_in_workspace(workspace: Workspace, target: str) -> ReadResult:
    abs_path = assert_path_within_workspace(workspace, target)
    if not os.path.isfile(abs_path):
        raise ValueError(f"Not a file: {target}")
    size = os.path.getsize(abs_path)
    if size > MAX_READ_BYTES:
        raise ValueError(f"File too large ({size / 1024 / 1024:.2f} MB > 1 MB limit)")
    with open(abs_path, encoding="utf-8") as f:
        raw = f.read()
    truncated = len(raw) > MAX_READ_CHARS
    content = (
        raw[:MAX_READ_CHARS] + f"\n\n[TRUNCATED at {MAX_READ_CHARS} chars]"
        if truncated
        else raw
    )
    return ReadResult(
        path=target,
        absolute_path=abs_path,
        cwd=get_effective_cwd(workspace),
        size=size,
        content=content,
        truncated=truncated,
    )


@dataclass
class WriteResult:
    path: str
    absolute_path: str
    cwd: str
    bytes: int


def write_file_in_workspace(workspace: Workspace, target: str, content: str) -> WriteResult:
    byte_len = len(content.encode("utf-8"))
    if byte_len > MAX_WRITE_BYTES:
        raise ValueError(f"Content too large ({byte_len / 1024:.1f} KB > 100 KB limit)")
    abs_path = assert_path_within_workspace(workspace, target)

    if workspace.mode == "sandbox":
        used_bytes, used_files = _scan_workspace_usage(workspace.root_path)
        if used_bytes + byte_len > SANDBOX_TOTAL_BYTES:
            raise ValueError(
                f"Workspace quota exceeded ({used_bytes / 1024 / 1024:.1f} MB used "
                f"+ {byte_len / 1024:.1f} KB > 100 MB cap)"
            )
        if used_files + 1 > SANDBOX_TOTAL_FILES:
            raise ValueError(f"Workspace file count exceeded ({used_files} + 1 > 1000 cap)")

    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        f.write(content)
    return WriteResult(
        path=target,
        absolute_path=abs_path,
        cwd=get_effective_cwd(workspace),
        bytes=byte_len,
    )


@dataclass
class ListEntry:
    name: str
    is_directory: bool
    size: int | None = None


@dataclass
class ListResult:
    rel_path: str
    absolute_path: str
    parent: str | None
    entries: list[ListEntry]


def list_dir_in_workspace(workspace: Workspace, target: str) -> ListResult:
    rel_path = target if target else ""
    abs_path = (
        get_effective_cwd(workspace)
        if rel_path == ""
        else assert_path_within_workspace(workspace, target)
    )

    if not os.path.isdir(abs_path):
        raise ValueError(f"Not a directory: {target or '(root)'}")

    entries: list[ListEntry] = []
    with os.scandir(abs_path) as it:
        for e in it:
            if e.name.startswith("."):  # hide dotfiles by default
                continue
            is_dir = e.is_dir()
            entry = ListEntry(name=e.name, is_directory=is_dir)
            if e.is_file():
                with contextlib.suppress(OSError):
                    entry.size = e.stat().st_size
            entries.append(entry)

    # directories first, then case-sensitive name order (matches localeCompare-ish)
    entries.sort(key=lambda x: (not x.is_directory, x.name))

    parent: str | None
    if rel_path == "":
        parent = None
    else:
        p = os.path.dirname(rel_path.replace("\\", "/"))
        parent = "" if p in (".", "") else p

    return ListResult(
        rel_path=rel_path, absolute_path=abs_path, parent=parent, entries=entries
    )


def _scan_workspace_usage(root_path: str) -> tuple[int, int]:
    """(bytes, files) under root_path; realpath-dedup guards against symlink cycles."""
    total_bytes = 0
    total_files = 0
    if not os.path.exists(root_path):
        return total_bytes, total_files
    visited: set[str] = set()
    stack = [root_path]
    while stack:
        directory = stack.pop()
        try:
            real = os.path.realpath(directory)
        except OSError:
            continue
        if real in visited:
            continue
        visited.add(real)
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for e in entries:
            full = os.path.join(directory, e.name)
            if e.is_dir():
                stack.append(full)
            elif e.is_file():
                try:
                    total_bytes += e.stat().st_size
                    total_files += 1
                except OSError:
                    pass
    return total_bytes, total_files
