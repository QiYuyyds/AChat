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
import json
import logging
import os
import re
import shutil
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field

from app.config import get_settings
from app.db.engine import get_local_db
from app.db.models import Artifact
from app.schemas.events import WorktreeEvent
from app.services.event_bus import event_bus
from app.utils.clock import now_ms
from app.utils.ids import new_artifact_id
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
    agent_id: str | None = None


@dataclass
class MergeResult:
    """Outcome of merging a worktree back to the main workspace."""

    success: bool
    conflict_files: list[str] = field(default_factory=list)
    error: str | None = None
    resolution_strategy: str = "auto"  # "auto" / "llm" / "manual" / "abandoned"
    resolved_files: list[str] = field(default_factory=list)


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


# ─── Layer 2: LLM-assisted conflict resolution ──────────────────────────────

def _extract_conflict_content(workspace_path: str, file_path: str) -> str:
    """Read a conflicted file and return its raw content (with conflict markers)."""
    abs_path = os.path.join(workspace_path, file_path)
    try:
        with open(abs_path, encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        logger.warning("Failed to read conflicted file %s: %s", file_path, exc)
        return ""


def _build_llm_merge_prompt(file_path: str, conflict_content: str) -> str:
    """Construct the LLM prompt for conflict resolution (only file path + content)."""
    return (
        "你是一个代码合并专家。以下文件存在 git 合并冲突，请综合两边的修改，"
        "生成一个语义正确的合并版本。只输出合并后的完整文件内容，不要解释。\n\n"
        f"文件路径: {file_path}\n\n"
        f"冲突内容:\n{conflict_content}"
    )


def _call_llm_merge(prompt: str) -> str:
    """Call an OpenAI-compatible LLM to resolve a merge conflict.

    Reuses the key-priority pattern from eval_judge.py:
    llm_api_key > openai_api_key > deepseek_api_key.
    """
    settings = get_settings()
    if (
        not settings.llm_api_key
        and not settings.openai_api_key
        and not settings.deepseek_api_key
    ):
        raise RuntimeError("No LLM API key configured for merge conflict resolution")

    import httpx

    if settings.llm_api_key:
        api_key = settings.llm_api_key
        api_url = settings.llm_api_url or "https://api.openai.com/v1"
        model = settings.llm_model or "gpt-4o-mini"
    elif settings.openai_api_key:
        api_key = settings.openai_api_key
        api_url = "https://api.openai.com/v1"
        model = "gpt-4o-mini"
    else:
        api_key = settings.deepseek_api_key
        api_url = "https://api.deepseek.com/v1"
        model = "deepseek-chat"

    client = httpx.Client(timeout=120.0)
    resp = client.post(
        f"{api_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "你是代码合并专家，只输出合并后的完整文件内容。",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.1,
        },
    )
    resp.raise_for_status()
    raw = resp.json()["choices"][0]["message"]["content"]
    # Strip markdown code fences if present
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```", 1)[-1]
        # skip language tag line
        if raw.startswith("\n"):
            raw = raw[1:]
        elif raw[:10].split("\n")[0].strip().isalpha():
            raw = raw.split("\n", 1)[-1] if "\n" in raw else raw
        if raw.endswith("```"):
            raw = raw[:-3]
        raw = raw.strip()
    return raw


def _validate_syntax(file_path: str, content: str) -> bool:
    """Validate syntax of merged content by file extension.

    - .py: compile()
    - .json: json.loads()
    - .ts/.tsx/.js: bracket-pair matching (simple regex)
    - .md and others: always pass (skip)
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".py":
        try:
            compile(content, file_path, "exec")
            return True
        except SyntaxError:
            return False
    if ext == ".json":
        try:
            json.loads(content)
            return True
        except (json.JSONDecodeError, ValueError):
            return False
    if ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
        return _check_bracket_pairs(content)
    # .md and other text files: skip validation
    return True


def _check_bracket_pairs(content: str) -> bool:
    """Simple bracket-pair matching for JS/TS files (ignores strings/comments)."""
    # Remove string literals and comments to avoid false positives
    cleaned = re.sub(r'"(?:[^"\\]|\\.)*"', '""', content)
    cleaned = re.sub(r"'(?:[^'\\]|\\.)*'", "''", cleaned)
    cleaned = re.sub(r"`(?:[^`\\]|\\.)*`", "``", cleaned)
    cleaned = re.sub(r"//.*", "", cleaned)
    cleaned = re.sub(r"/\\*.*?\\*/", "", cleaned, flags=re.DOTALL)

    stack: list[str] = []
    pairs = {")": "(", "]": "[", "}": "{"}
    for ch in cleaned:
        if ch in "([{":
            stack.append(ch)
        elif ch in ")]}":
            if not stack or stack[-1] != pairs[ch]:
                return False
            stack.pop()
    return len(stack) == 0


async def _llm_resolve_conflicts(
    workspace_path: str,
    conflict_files: list[str],
) -> tuple[bool, list[str]]:
    """Attempt to resolve all conflict files via LLM.

    Returns (all_resolved, resolved_file_list).
    If any file fails, returns (False, []) without partial writes.
    """
    if not conflict_files:
        return True, []

    resolved: list[str] = []
    merged_contents: dict[str, str] = {}

    for file_path in conflict_files:
        conflict_content = _extract_conflict_content(workspace_path, file_path)
        if not conflict_content:
            logger.warning(
                "[worktree] no conflict content for %s, skipping LLM resolve",
                file_path,
            )
            return False, []

        prompt = _build_llm_merge_prompt(file_path, conflict_content)
        try:
            merged = await asyncio.to_thread(_call_llm_merge, prompt)
        except Exception as exc:  # noqa: BLE001 - LLM call failure
            logger.warning("[worktree] LLM merge failed for %s: %s", file_path, exc)
            return False, []

        if not _validate_syntax(file_path, merged):
            logger.warning(
                "[worktree] LLM merged content failed syntax check for %s",
                file_path,
            )
            return False, []

        merged_contents[file_path] = merged
        resolved.append(file_path)

    # All files passed: write merged content and git add
    for file_path, content in merged_contents.items():
        abs_path = os.path.join(workspace_path, file_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as fh:
            fh.write(content)
        await _run_git(workspace_path, "add", file_path)

    return True, resolved


# ─── Layer 3: human approval + data snapshots ───────────────────────────────

async def _save_conflict_snapshots(
    wt: WorktreeRef,
    conflict_files: list[str],
) -> list[str]:
    """Save base/ours/theirs snapshots for each conflicted file as Artifacts.

    Uses ``git show :1:<file>`` (base), ``:2:<file>`` (ours), ``:3:<file>`` (theirs).
    Returns the list of created artifact IDs.
    """
    if not wt.agent_id:
        logger.warning(
            "[worktree] cannot save conflict snapshots without agent_id for task %s",
            wt.task_id,
        )
        return []

    artifact_ids: list[str] = []
    async with get_local_db() as db:
        for file_path in conflict_files:
            base_content = await _git_show_stage(wt.main_workspace_path, 1, file_path)
            ours_content = await _git_show_stage(wt.main_workspace_path, 2, file_path)
            theirs_content = await _git_show_stage(wt.main_workspace_path, 3, file_path)
            conflict_markers = _extract_conflict_content(
                wt.main_workspace_path, file_path
            )

            filename = os.path.basename(file_path)
            artifact = Artifact(
                id=new_artifact_id(),
                conversation_id=wt.conversation_id,
                type="diff",
                title=f"合并冲突: {filename}",
                version=1,
                created_by_agent_id=wt.agent_id,
                created_at=now_ms(),
            )
            artifact.content_dict = {
                "type": "conflict_snapshot",
                "file_path": file_path,
                "base": base_content,
                "ours": ours_content,
                "theirs": theirs_content,
                "conflict_markers": conflict_markers,
            }
            db.add(artifact)
            artifact_ids.append(artifact.id)
        await db.flush()

    return artifact_ids


async def _git_show_stage(
    workspace_path: str,
    stage: int,
    file_path: str,
) -> str:
    """Read a file from a specific git merge stage (1=base, 2=ours, 3=theirs)."""
    rc, out, _err = await _run_git(
        workspace_path, "show", f":{stage}:{file_path}"
    )
    if rc != 0:
        return ""
    return out


async def _human_resolve_conflicts(
    wt: WorktreeRef,
    conflict_files: list[str],
) -> MergeResult:
    """Layer 3: save snapshots, register pending conflict, block for user decision."""
    from app.services.pending_merge_conflicts import pending_merge_conflicts
    from app.utils.approval import await_pending_decision

    # Save conflict snapshots as artifacts (data preservation)
    await _save_conflict_snapshots(wt, conflict_files)

    # Register pending conflict for user approval
    conflict = pending_merge_conflicts.register(
        conversation_id=wt.conversation_id,
        task_id=wt.task_id,
        conflict_files=conflict_files,
        workspace_path=wt.main_workspace_path,
        user_id=wt.user_id,
    )

    # Block until user resolves or run is cancelled
    # No timeout — user explicitly requested "block indefinitely"
    # Use a dummy cancel_event that never fires (no cancellation in merge context)
    dummy_cancel = asyncio.Event()
    decision = await await_pending_decision(
        attach_resolver=lambda r: pending_merge_conflicts.attach_resolver(
            conflict.id, r
        ),
        cancel=lambda: pending_merge_conflicts.cancel(conflict.id),
        cancel_event=dummy_cancel,
        cancelled_value={"action": "abandon", "resolution_strategy": "abandoned"},
    )

    if not isinstance(decision, dict):
        decision = {"action": "abandon", "resolution_strategy": "abandoned"}

    action = decision.get("action", "abandon")
    resolved_files: list[str] = []

    if action == "abandon":
        await _run_git(wt.main_workspace_path, "merge", "--abort")
        return MergeResult(
            success=False,
            conflict_files=conflict_files,
            error="User abandoned the merge conflict",
            resolution_strategy="abandoned",
        )

    # Process per-file decisions
    for file_path in conflict_files:
        if action == "ours":
            await _run_git(wt.main_workspace_path, "checkout", "--ours", file_path)
        elif action == "theirs":
            await _run_git(wt.main_workspace_path, "checkout", "--theirs", file_path)
        elif action == "edit":
            file_contents = decision.get("file_contents") or {}
            if file_path in file_contents:
                abs_path = os.path.join(wt.main_workspace_path, file_path)
                with open(abs_path, "w", encoding="utf-8") as fh:
                    fh.write(file_contents[file_path])
        await _run_git(wt.main_workspace_path, "add", file_path)
        resolved_files.append(file_path)

    # All conflicts resolved — complete the merge
    await _run_git(wt.main_workspace_path, "commit", "--no-edit")
    return MergeResult(
        success=True,
        resolution_strategy="manual",
        resolved_files=resolved_files,
    )


# ─── Smart .gitignore patterns for ensure_git_init ─────────────────────────
_SMART_GITIGNORE_PATTERNS = """\
node_modules/
__pycache__/
*.pyc
dist/
build/
.next/
out/
target/
.env
.env.*
!.env.example
.vscode/
.idea/
.DS_Store
Thumbs.db
*.log
.agenthub-data/
"""


def _ensure_gitignore(workspace_path: str) -> None:
    """Write a smart .gitignore when none exists, or append .agenthub-data/ if missing.

    - No .gitignore: write the full smart template (node_modules, __pycache__, etc.)
    - Existing .gitignore: only append .agenthub-data/ if not already present
    """
    gitignore = os.path.join(workspace_path, ".gitignore")
    if not os.path.exists(gitignore):
        try:
            with open(gitignore, "w", encoding="utf-8") as fh:
                fh.write(_SMART_GITIGNORE_PATTERNS)
        except OSError as exc:
            logger.warning("write .gitignore failed: %s", exc)
        return

    # Existing .gitignore — append .agenthub-data/ if not already present.
    try:
        with open(gitignore, encoding="utf-8") as fh:
            existing = fh.read()
        if ".agenthub-data/" not in existing:
            with open(gitignore, "a", encoding="utf-8") as fh:
                prefix = "" if existing.endswith("\n") else "\n"
                fh.write(f"{prefix}.agenthub-data/\n")
    except OSError as exc:
        logger.warning("append .gitignore failed: %s", exc)


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

    _ensure_gitignore(workspace_path)

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
    agent_id: str | None = None,
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
        agent_id=agent_id,
    )
    _publish_worktree_event("worktree.created", ref)
    return ref


async def merge_worktree_back(wt: WorktreeRef) -> MergeResult:
    """Merge *wt*'s changes back into the main workspace.

    Git mode: commit in worktree, then ``git merge --no-edit <branch>`` in main.
    Non-git fallback: copy worktree files over main workspace (overwrite).

    On git merge conflict, three-layer progressive resolution is attempted:
    Layer 1 (auto merge) → Layer 2 (LLM-assisted) → Layer 3 (human approval).
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
                # Layer 2: LLM-assisted conflict resolution
                llm_ok, resolved = await _llm_resolve_conflicts(
                    wt.main_workspace_path, conflict_files
                )
                if llm_ok:
                    await _run_git(wt.main_workspace_path, "commit", "--no-edit")
                    result = MergeResult(
                        success=True,
                        resolution_strategy="llm",
                        resolved_files=resolved,
                    )
                else:
                    # Layer 3: human approval
                    result = await _human_resolve_conflicts(wt, conflict_files)
            else:
                result = MergeResult(success=True, resolution_strategy="auto")
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
    conflict_files: list[str] | None = None
    resolution_status: str | None = None
    if event_type == "worktree.merged":
        if merge_result and merge_result.success:
            merge_status = "success"
            strategy = merge_result.resolution_strategy
            if strategy == "auto":
                resolution_status = "success"
            elif strategy == "llm":
                resolution_status = "llm_resolved"
            elif strategy == "manual":
                resolution_status = "manual_resolved"
            else:
                resolution_status = "success"
        else:
            merge_status = "conflict"
            conflict_files = merge_result.conflict_files if merge_result else None
            if merge_result and merge_result.resolution_strategy == "abandoned":
                resolution_status = "abandoned"
            else:
                resolution_status = "conflict"
    event = WorktreeEvent(
        conversation_id=wt.conversation_id,
        timestamp=now_ms(),
        type=event_type,  # type: ignore[arg-type]
        task_id=wt.task_id,
        branch_name=wt.branch_name if event_type == "worktree.created" else None,
        path=wt.path if event_type == "worktree.created" else None,
        merge_status=merge_status,  # type: ignore[arg-type]
        conflict_files=conflict_files,
        resolution_status=resolution_status,  # type: ignore[arg-type]
    )
    event_bus.publish(event, user_id=wt.user_id)


# ─── Fork worktree lifecycle (persistent, no merge-back) ────────────────────
def _fork_worktree_path(fork_conv_id: str, user_id: str | None = None) -> str:
    """Path for a fork conversation's worktree under .agenthub-data/workspaces/users/{uid}/{conv_id}/."""
    base = get_worktrees_root(user_id)
    return os.path.join(base, fork_conv_id)


async def create_fork_worktree(
    source_workspace_path: str,
    fork_conv_id: str,
    user_id: str | None = None,
) -> WorktreeRef | None:
    """Create a persistent git worktree for a forked conversation.

    Unlike DAG worktrees, this worktree persists for the lifetime of the
    forked conversation and is NOT merged back. Branch name: ``fork/{conv_id}``.
    """
    wt_path = _fork_worktree_path(fork_conv_id, user_id)
    branch_name = f"fork/{fork_conv_id}"

    async with _lock_manager.lock_for(source_workspace_path):
        if os.path.exists(wt_path):
            shutil.rmtree(wt_path, ignore_errors=True)
        os.makedirs(os.path.dirname(wt_path), exist_ok=True)

        is_git = is_git_repo(source_workspace_path)
        if is_git:
            rc, _, err = await _run_git(
                source_workspace_path, "worktree", "add", "-b", branch_name, wt_path, "HEAD"
            )
            if rc != 0:
                logger.warning(
                    "git worktree add failed for fork %s: %s", fork_conv_id, err
                )
                shutil.rmtree(wt_path, ignore_errors=True)
                return None
        else:
            try:
                shutil.copytree(source_workspace_path, wt_path)
            except OSError as exc:
                logger.warning(
                    "copytree fork worktree failed for %s: %s", fork_conv_id, exc
                )
                return None

    ref = WorktreeRef(
        task_id=fork_conv_id,
        branch_name=branch_name,
        path=wt_path,
        main_workspace_path=source_workspace_path,
        is_git=is_git,
        conversation_id=fork_conv_id,
        user_id=user_id,
    )
    return ref


async def cleanup_fork_worktree(workspace_path: str, branch_name: str) -> None:
    """Remove a fork conversation's worktree and branch.

    Called when a forked conversation is deleted. The source workspace
    (main_workspace_path) is NOT affected.
    """
    if not workspace_path or not os.path.isdir(workspace_path):
        return

    # Determine the source git repo from the worktree's own .git pointer
    # (git worktree creates a .git file pointing to the main repo).
    source_repo = None
    git_file = os.path.join(workspace_path, ".git")
    if os.path.isfile(git_file):
        try:
            with open(git_file, encoding="utf-8") as fh:
                content = fh.read().strip()
                if content.startswith("gitdir:"):
                    # Path like: /path/to/main/.git/worktrees/<wt_id>
                    gitdir = content.split("gitdir:", 1)[1].strip()
                    source_repo = os.path.dirname(
                        os.path.dirname(os.path.abspath(gitdir))
                    )
        except OSError:
            pass

    if source_repo and is_git_repo(source_repo):
        async with _lock_manager.lock_for(source_repo):
            if os.path.isdir(workspace_path):
                await _run_git(
                    source_repo, "worktree", "remove", "--force", workspace_path
                )
            await _run_git(source_repo, "branch", "-D", branch_name)
    else:
        shutil.rmtree(workspace_path, ignore_errors=True)
