"""Git worktree isolation for parallel dispatch tasks.

Manages the worktree lifecycle bound to DAG waves: create -> merge-back -> cleanup.
Supports git repos (true ``git worktree``) and non-git dirs (directory copy fallback).

Design (see openspec/changes/add-worktree-isolation/):
  - Worktree lifetime == DAG wave. Same task's harness-loop attempts share one worktree.
  - Branch naming: ``agent/{sanitized-agent-name}/{short-task-id}``.
  - Merge: git merge in main workspace; conflict -> ``merge_conflict`` status.
  - Per-workspace mutex lock serialises git mutations on the same repo.
  - Worktrees live under ``<data_dir>/worktrees/<conv_id>/<task_id>/``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field

from app.config import get_settings
from app.schemas.events import WorktreeEvent
from app.services.event_bus import event_bus
from app.utils.clock import now_ms
from app.utils.workspace_utils import is_path_within

logger = logging.getLogger(__name__)

WORKTREES_DIR_NAME = "worktrees"

# task_id is truncated to keep Windows path lengths manageable.
_SHORT_TASK_ID_LEN = 8


def get_worktrees_root(user_id: str | None = None) -> str:
    """Root dir holding per-conversation worktrees.

    When ``user_id`` is given, returns a user-scoped path:
    ``<data_dir>/worktrees/users/<user_id>/``.
    """
    base = str(get_settings().data_path / WORKTREES_DIR_NAME)
    if user_id is not None:
        return os.path.join(base, "users", user_id)
    return base


# ─── dataclasses ────────────────────────────────────────────────────────────
@dataclass
class WorktreeRef:
    """A handle to a created worktree for a dispatch task."""

    task_id: str
    branch_name: str
    path: str
    main_workspace_path: str
    is_git: bool
    conversation_id: str
    user_id: str | None = None


@dataclass
class MergeResult:
    """Outcome of merging a worktree back to the main workspace."""

    success: bool
    conflict_files: list[str] = field(default_factory=list)
    error: str | None = None


# ─── per-workspace mutex lock ───────────────────────────────────────────────
class _WorktreeLockManager:
    """Per-workspace-path ``asyncio.Lock``: different workspaces parallel, same serial."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    @asynccontextmanager
    async def lock_for(self, workspace_path: str) -> AsyncIterator[None]:
        key = os.path.abspath(workspace_path)
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        async with lock:
            yield


_lock_manager = _WorktreeLockManager()


# ─── git helpers ────────────────────────────────────────────────────────────
async def _run_git(cwd: str, *args: str) -> tuple[int, str, str]:
    """Run a git command, returning (returncode, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        logger.warning("git executable not found when running: git %s", " ".join(args))
        return 127, "", "git not found"
    stdout, stderr = await proc.communicate()
    return (
        proc.returncode or 0,
        stdout.decode("utf-8", "replace"),
        stderr.decode("utf-8", "replace"),
    )


# ─── pure helpers ───────────────────────────────────────────────────────────
def is_git_repo(path: str) -> bool:
    """True if *path* is inside a git repository (``.git`` dir or file present)."""
    git_marker = os.path.join(path, ".git")
    return os.path.isdir(git_marker) or os.path.isfile(git_marker)


def sanitize_agent_name(name: str) -> str:
    """Lowercase kebab-case, strip non-alphanumerics, truncate to 30 chars."""
    lowered = name.lower().strip()
    sanitized = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    if not sanitized:
        sanitized = "agent"
    return sanitized[:30]


def _branch_name(agent_name: str, task_id: str) -> str:
    return f"agent/{sanitize_agent_name(agent_name)}/{task_id[:_SHORT_TASK_ID_LEN]}"


def _worktree_path_for(conversation_id: str, task_id: str, user_id: str | None = None) -> str:
    return os.path.join(get_worktrees_root(user_id), conversation_id, task_id)


def _validate_worktree_path(path: str) -> bool:
    """Ensure the worktree path is within the designated worktrees root (no escape)."""
    root = os.path.abspath(get_worktrees_root())
    return is_path_within(os.path.abspath(path), root)


def _parse_conflict_files(workspace_path: str) -> list[str]:
    """Return unmerged file paths from the main workspace after a failed merge."""
    import subprocess

    try:
        out = subprocess.check_output(
            ["git", "diff", "--name-only", "--diff-filter=U"],
            cwd=workspace_path,
            stderr=subprocess.DEVNULL,
        )
    except Exception:  # noqa: BLE001 - best-effort conflict listing
        return []
    return [
        line.decode("utf-8", "replace").strip()
        for line in out.splitlines()
        if line.strip()
    ]


def _copy_dir_contents(src: str, dst: str) -> None:
    """Copy all files/dirs from *src* into *dst* (overwrite existing)."""
    for entry in os.listdir(src):
        if entry == ".git":
            continue
        s = os.path.join(src, entry)
        d = os.path.join(dst, entry)
        if os.path.isdir(s):
            shutil.copytree(s, d, dirs_exist_ok=True)
        else:
            shutil.copy2(s, d)


# ─── lifecycle ──────────────────────────────────────────────────────────────
async def ensure_git_init(workspace_path: str) -> bool:
    """Initialise *workspace_path* as a git repo with an empty commit + .gitignore.

    Returns True on success (or if already a repo). AChat-managed sandbox dirs use
    this so they support true git worktree isolation.
    """
    if is_git_repo(workspace_path):
        return True

    rc, _, err = await _run_git(workspace_path, "init")
    if rc != 0:
        logger.warning("git init failed in %s: %s", workspace_path, err)
        return False

    # Local identity so commits don't fail for unconfigured sandbox repos.
    await _run_git(workspace_path, "config", "user.email", "achat@local")
    await _run_git(workspace_path, "config", "user.name", "AChat")

    gitignore = os.path.join(workspace_path, ".gitignore")
    if not os.path.exists(gitignore):
        try:
            with open(gitignore, "w", encoding="utf-8") as fh:
                fh.write(".agenthub-data/\n")
        except OSError as exc:
            logger.warning("write .gitignore failed: %s", exc)

    # Initial commit to establish a valid HEAD (required for worktree creation).
    await _run_git(workspace_path, "add", "-A")
    rc, _, err = await _run_git(
        workspace_path, "commit", "--allow-empty", "-m", "achat sandbox init"
    )
    if rc != 0:
        logger.warning("initial git commit failed in %s: %s", workspace_path, err)
        return False
    return True


async def create_worktree(
    main_workspace: str,
    task_id: str,
    agent_name: str,
    conversation_id: str,
    user_id: str | None = None,
) -> WorktreeRef | None:
    """Create an isolated worktree for *task_id*.

    Git mode: ``git worktree add -b <branch> <path> HEAD``.
    Non-git fallback: ``shutil.copytree``.

    Returns ``None`` on failure (caller degrades to no-worktree mode).
    """
    wt_path = _worktree_path_for(conversation_id, task_id, user_id)
    if not _validate_worktree_path(wt_path):
        logger.warning("worktree path escapes worktrees root: %s", wt_path)
        return None

    is_git = is_git_repo(main_workspace)
    branch = _branch_name(agent_name, task_id)

    async with _lock_manager.lock_for(main_workspace):
        if os.path.exists(wt_path):
            shutil.rmtree(wt_path, ignore_errors=True)
        os.makedirs(os.path.dirname(wt_path), exist_ok=True)

        if is_git:
            rc, _, err = await _run_git(
                main_workspace, "worktree", "add", "-b", branch, wt_path, "HEAD"
            )
            if rc != 0:
                logger.warning(
                    "git worktree add failed for task %s: %s", task_id, err
                )
                shutil.rmtree(wt_path, ignore_errors=True)
                return None
        else:
            try:
                shutil.copytree(main_workspace, wt_path)
            except OSError as exc:
                logger.warning("copytree worktree failed for task %s: %s", task_id, exc)
                return None

    ref = WorktreeRef(
        task_id=task_id,
        branch_name=branch,
        path=wt_path,
        main_workspace_path=main_workspace,
        is_git=is_git,
        conversation_id=conversation_id,
        user_id=user_id,
    )
    _publish_worktree_event("worktree.created", ref)
    return ref


async def merge_worktree_back(wt: WorktreeRef) -> MergeResult:
    """Merge *wt*'s changes back into the main workspace.

    Git mode: commit in worktree, then ``git merge --no-edit <branch>`` in main.
    Non-git fallback: copy worktree files over main workspace (overwrite).

    On git merge conflict the merge is aborted (main left clean) and the
    conflicting file list is returned.
    """
    async with _lock_manager.lock_for(wt.main_workspace_path):
        if wt.is_git:
            # Commit any changes in the worktree branch first.
            await _run_git(wt.path, "add", "-A")
            await _run_git(
                wt.path, "commit", "-m", f"task {wt.task_id}", "--allow-empty"
            )
            rc, _out, err = await _run_git(
                wt.main_workspace_path, "merge", "--no-edit", wt.branch_name
            )
            if rc != 0:
                conflict_files = _parse_conflict_files(wt.main_workspace_path)
                await _run_git(wt.main_workspace_path, "merge", "--abort")
                result = MergeResult(
                    success=False,
                    conflict_files=conflict_files,
                    error=err or "git merge conflict",
                )
            else:
                result = MergeResult(success=True)
        else:
            try:
                _copy_dir_contents(wt.path, wt.main_workspace_path)
                result = MergeResult(success=True)
            except OSError as exc:
                result = MergeResult(success=False, error=str(exc))

    _publish_worktree_event("worktree.merged", wt, result)
    return result


async def cleanup_worktree(wt: WorktreeRef) -> None:
    """Remove the worktree dir and its branch. Idempotent and safe."""
    async with _lock_manager.lock_for(wt.main_workspace_path):
        if wt.is_git:
            if os.path.isdir(wt.path):
                await _run_git(
                    wt.main_workspace_path, "worktree", "remove", "--force", wt.path
                )
            await _run_git(wt.main_workspace_path, "branch", "-D", wt.branch_name)
        else:
            shutil.rmtree(wt.path, ignore_errors=True)

    # Remove empty conversation-level dir if this was the last worktree.
    conv_dir = os.path.dirname(wt.path)
    with suppress(OSError):
        os.rmdir(conv_dir)

    _publish_worktree_event("worktree.cleaned", wt)


async def prune_orphan_worktrees(
    worktrees_root: str,
    active_paths: set[str] | None = None,
) -> list[str]:
    """Remove worktree dirs under *worktrees_root* with no active run.

    On startup *active_paths* is empty (no dispatch is running), so every
    leftover worktree dir is treated as an orphan. Returns the cleaned paths.
    """
    cleaned: list[str] = []
    if not os.path.isdir(worktrees_root):
        return cleaned

    active_norm = {os.path.abspath(p) for p in (active_paths or ())}

    for conv_name in os.listdir(worktrees_root):
        conv_dir = os.path.join(worktrees_root, conv_name)
        if not os.path.isdir(conv_dir):
            continue
        for task_name in os.listdir(conv_dir):
            task_path = os.path.join(conv_dir, task_name)
            if not os.path.isdir(task_path):
                continue
            if os.path.abspath(task_path) in active_norm:
                continue
            shutil.rmtree(task_path, ignore_errors=True)
            cleaned.append(task_path)
        with suppress(OSError):
            os.rmdir(conv_dir)

    return cleaned


async def git_worktree_prune(workspace_path: str) -> None:
    """Run ``git worktree prune`` to clean stale git worktree metadata."""
    if is_git_repo(workspace_path):
        await _run_git(workspace_path, "worktree", "prune")


# ─── event publishing ───────────────────────────────────────────────────────
def _publish_worktree_event(
    event_type: str,
    wt: WorktreeRef,
    merge_result: MergeResult | None = None,
) -> None:
    """Publish a WorktreeEvent on the EventBus."""
    merge_status: str | None = None
    if event_type == "worktree.merged":
        merge_status = "success" if (merge_result and merge_result.success) else "conflict"
    event = WorktreeEvent(
        conversation_id=wt.conversation_id,
        timestamp=now_ms(),
        type=event_type,  # type: ignore[arg-type]
        task_id=wt.task_id,
        branch_name=wt.branch_name if event_type == "worktree.created" else None,
        path=wt.path if event_type == "worktree.created" else None,
        merge_status=merge_status,  # type: ignore[arg-type]
    )
    event_bus.publish(event, user_id=wt.user_id)
