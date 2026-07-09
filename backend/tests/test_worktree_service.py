"""Unit tests for worktree_service: create / merge / cleanup / prune / locks / events.

Uses real git in a tmp_path so the worktree lifecycle is exercised end-to-end.
"""

from __future__ import annotations

import asyncio
import os
import shutil

import pytest
import pytest_asyncio

from app.services import worktree_service as wt
from app.services.event_bus import event_bus


def _git_available() -> bool:
    return shutil.which("git") is not None


pytestmark = pytest.mark.skipif(not _git_available(), reason="git not installed")


@pytest_asyncio.fixture
async def worktree_env(tmp_path, monkeypatch):
    """Point DATA_DIR + WORKSPACE_ROOT at tmp_path and reset settings cache."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    from app.config import get_settings

    get_settings.cache_clear()
    os.makedirs(tmp_path / "data", exist_ok=True)
    os.makedirs(tmp_path / "workspaces", exist_ok=True)
    yield tmp_path
    get_settings.cache_clear()


async def _init_repo(path: str) -> None:
    """Create a real git repo with an initial commit at *path*."""
    os.makedirs(path, exist_ok=True)
    await wt._run_git(path, "init")
    await wt._run_git(path, "config", "user.email", "test@local")
    await wt._run_git(path, "config", "user.name", "Test")
    with open(os.path.join(path, "README.md"), "w", encoding="utf-8") as f:
        f.write("init\n")
    await wt._run_git(path, "add", "-A")
    await wt._run_git(path, "commit", "-m", "init")


# ─── pure helpers ────────────────────────────────────────────────────────────
class TestIsGitRepo:
    def test_non_git_dir(self, tmp_path):
        assert wt.is_git_repo(str(tmp_path)) is False

    def test_git_dir(self, tmp_path):
        os.makedirs(tmp_path / ".git")
        assert wt.is_git_repo(str(tmp_path)) is True

    def test_git_file(self, tmp_path):
        (tmp_path / ".git").write_text("gitdir: /elsewhere")
        assert wt.is_git_repo(str(tmp_path)) is True


class TestSanitizeAgentName:
    def test_simple(self):
        assert wt.sanitize_agent_name("Code Writer") == "code-writer"

    def test_special_chars(self):
        assert wt.sanitize_agent_name("Agent #1!") == "agent-1"

    def test_chinese(self):
        # Chinese chars are non-alphanumeric -> stripped to dashes -> "agent"
        assert wt.sanitize_agent_name("代码助手") == "agent"

    def test_empty(self):
        assert wt.sanitize_agent_name("") == "agent"

    def test_truncation(self):
        assert wt.sanitize_agent_name("a" * 50) == "a" * 30


# ─── ensure_git_init ─────────────────────────────────────────────────────────
class TestEnsureGitInit:
    async def test_init_creates_repo_with_commit(self, worktree_env):
        ws = str(worktree_env / "ws1")
        os.makedirs(ws)
        ok = await wt.ensure_git_init(ws)
        assert ok is True
        assert wt.is_git_repo(ws)
        rc, out, _ = await wt._run_git(ws, "log", "--oneline")
        assert rc == 0  # has at least one commit

    async def test_idempotent_on_existing_repo(self, worktree_env):
        ws = str(worktree_env / "ws2")
        os.makedirs(ws)
        await _init_repo(ws)
        ok = await wt.ensure_git_init(ws)
        assert ok is True

    async def test_gitignore_excludes_agenthub_data(self, worktree_env):
        ws = str(worktree_env / "ws3")
        os.makedirs(ws)
        await wt.ensure_git_init(ws)
        gi = os.path.join(ws, ".gitignore")
        assert os.path.isfile(gi)
        with open(gi, encoding="utf-8") as f:
            assert ".agenthub-data/" in f.read()


# ─── create_worktree ─────────────────────────────────────────────────────────
class TestCreateWorktree:
    async def test_git_mode_creates_branch_and_dir(self, worktree_env):
        ws = str(worktree_env / "main_ws")
        await _init_repo(ws)
        ref = await wt.create_worktree(ws, "t1", "Code Writer", "conv1")
        assert ref is not None
        assert ref.is_git is True
        assert os.path.isdir(ref.path)
        assert "code-writer" in ref.branch_name
        # worktree path under worktrees root
        assert os.path.basename(os.path.dirname(ref.path)) == "conv1"

    async def test_non_git_fallback_copytree(self, worktree_env):
        ws = str(worktree_env / "plain_ws")
        os.makedirs(ws)
        with open(os.path.join(ws, "file.txt"), "w") as f:
            f.write("hello")
        ref = await wt.create_worktree(ws, "t2", "Agent", "conv2")
        assert ref is not None
        assert ref.is_git is False
        assert os.path.isfile(os.path.join(ref.path, "file.txt"))

    async def test_failure_returns_none(self, worktree_env):
        # non-existent main workspace -> copytree fails (non-git path)
        ref = await wt.create_worktree(
            str(worktree_env / "nope"), "t3", "Agent", "conv3"
        )
        assert ref is None


# ─── merge_worktree_back ─────────────────────────────────────────────────────
class TestMergeWorktreeBack:
    async def test_git_merge_success(self, worktree_env):
        ws = str(worktree_env / "merge_ws")
        await _init_repo(ws)
        ref = await wt.create_worktree(ws, "t1", "Writer", "conv_m")
        assert ref is not None
        # write a file in the worktree
        with open(os.path.join(ref.path, "new.txt"), "w") as f:
            f.write("new content")
        result = await wt.merge_worktree_back(ref)
        assert result.success is True
        # file merged back to main workspace
        assert os.path.isfile(os.path.join(ws, "new.txt"))

    async def test_merge_conflict_returns_conflict_files(self, worktree_env):
        ws = str(worktree_env / "conflict_ws")
        await _init_repo(ws)
        # create two worktrees that both modify the same file differently
        ref1 = await wt.create_worktree(ws, "t1", "A", "conv_c")
        ref2 = await wt.create_worktree(ws, "t2", "B", "conv_c")
        assert ref1 and ref2
        # ref1: modify README and merge
        with open(os.path.join(ref1.path, "README.md"), "w") as f:
            f.write("from t1\n")
        r1 = await wt.merge_worktree_back(ref1)
        assert r1.success
        await wt.cleanup_worktree(ref1)
        # ref2: modify same file differently -> conflict
        with open(os.path.join(ref2.path, "README.md"), "w") as f:
            f.write("from t2\n")
        r2 = await wt.merge_worktree_back(ref2)
        assert r2.success is False
        assert any("README.md" in f for f in r2.conflict_files)

    async def test_non_git_merge_copies_back(self, worktree_env):
        ws = str(worktree_env / "copy_ws")
        os.makedirs(ws)
        with open(os.path.join(ws, "base.txt"), "w") as f:
            f.write("base")
        ref = await wt.create_worktree(ws, "t1", "A", "conv_copy")
        assert ref is not None
        with open(os.path.join(ref.path, "base.txt"), "w") as f:
            f.write("changed")
        result = await wt.merge_worktree_back(ref)
        assert result.success is True
        with open(os.path.join(ws, "base.txt")) as f:
            assert f.read() == "changed"


# ─── cleanup_worktree ────────────────────────────────────────────────────────
class TestCleanupWorktree:
    async def test_cleanup_removes_dir(self, worktree_env):
        ws = str(worktree_env / "clean_ws")
        await _init_repo(ws)
        ref = await wt.create_worktree(ws, "t1", "A", "conv_clean")
        assert ref and os.path.isdir(ref.path)
        await wt.cleanup_worktree(ref)
        assert not os.path.isdir(ref.path)

    async def test_cleanup_idempotent(self, worktree_env):
        ws = str(worktree_env / "clean_ws2")
        await _init_repo(ws)
        ref = await wt.create_worktree(ws, "t1", "A", "conv_clean2")
        assert ref is not None
        await wt.cleanup_worktree(ref)
        # second call must not raise
        await wt.cleanup_worktree(ref)

    async def test_cleanup_does_not_affect_sibling(self, worktree_env):
        ws = str(worktree_env / "sib_ws")
        await _init_repo(ws)
        ref1 = await wt.create_worktree(ws, "t1", "A", "conv_sib")
        ref2 = await wt.create_worktree(ws, "t2", "B", "conv_sib")
        assert ref1 and ref2
        await wt.cleanup_worktree(ref1)
        assert os.path.isdir(ref2.path)
        await wt.cleanup_worktree(ref2)


# ─── prune_orphan_worktrees ──────────────────────────────────────────────────
class TestPruneOrphanWorktrees:
    async def test_cleans_orphans(self, worktree_env):
        root = wt.get_worktrees_root()
        os.makedirs(os.path.join(root, "conv_a", "t1"))
        os.makedirs(os.path.join(root, "conv_a", "t2"))
        cleaned = await wt.prune_orphan_worktrees(root)
        assert len(cleaned) == 2
        assert not os.path.isdir(os.path.join(root, "conv_a"))

    async def test_preserves_active(self, worktree_env):
        root = wt.get_worktrees_root()
        active = os.path.join(root, "conv_b", "t1")
        os.makedirs(active)
        os.makedirs(os.path.join(root, "conv_b", "t2"))
        cleaned = await wt.prune_orphan_worktrees(root, active_paths={active})
        assert len(cleaned) == 1
        assert os.path.isdir(active)

    async def test_no_orphans_noop(self, worktree_env):
        root = wt.get_worktrees_root()
        os.makedirs(root, exist_ok=True)
        cleaned = await wt.prune_orphan_worktrees(root)
        assert cleaned == []


# ─── lock manager ────────────────────────────────────────────────────────────
class TestWorktreeLockManager:
    async def test_same_workspace_serialized(self, worktree_env):
        mgr = wt._WorktreeLockManager()
        order: list[str] = []

        async def hold(name: str, delay: float):
            async with mgr.lock_for("/ws"):
                order.append(f"{name}-start")
                await asyncio.sleep(delay)
                order.append(f"{name}-end")

        await asyncio.gather(hold("a", 0.05), hold("b", 0.01))
        # a fully completes before b starts (or vice versa)
        assert order in (
            ["a-start", "a-end", "b-start", "b-end"],
            ["b-start", "b-end", "a-start", "a-end"],
        )

    async def test_different_workspace_parallel(self, worktree_env):
        mgr = wt._WorktreeLockManager()
        order: list[str] = []

        async def hold(name: str, ws: str, delay: float):
            async with mgr.lock_for(ws):
                order.append(f"{name}-start")
                await asyncio.sleep(delay)
                order.append(f"{name}-end")

        # different workspaces -> starts interleave (both start before either ends)
        await asyncio.gather(hold("a", "/ws1", 0.05), hold("b", "/ws2", 0.05))
        assert order[0].endswith("-start")
        assert order[1].endswith("-start")


# ─── WorktreeEvent publishing ────────────────────────────────────────────────
class TestWorktreeEvents:
    async def test_create_merge_cleanup_publish_events(self, worktree_env):
        ws = str(worktree_env / "evt_ws")
        await _init_repo(ws)
        events: list = []
        async with event_bus.subscribe() as queue:
            ref = await wt.create_worktree(ws, "t1", "Writer", "conv_evt")
            assert ref is not None
            await wt.merge_worktree_back(ref)
            await wt.cleanup_worktree(ref)

            # Drain all queued events (operations are done, events are buffered).
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=1.0)
                    events.append(ev)
                except TimeoutError:
                    break

        types = [getattr(e, "type", None) for e in events]
        assert "worktree.created" in types
        assert "worktree.merged" in types
        assert "worktree.cleaned" in types

        created = next(e for e in events if e.type == "worktree.created")
        assert created.task_id == "t1"
        assert created.branch_name is not None
        assert created.path is not None

        merged = next(e for e in events if e.type == "worktree.merged")
        assert merged.merge_status == "success"
