"""Tests for worktree merge conflict resolution (Layer 2 LLM + Layer 3 human).

Covers tasks 7.1-7.6 from the fix-worktree-merge-conflict change:
- 7.1: _extract_conflict_content()
- 7.2: _validate_syntax()
- 7.3: _llm_resolve_conflicts() with mocked LLM
- 7.4: merge_worktree_back() Layer 1 success (no conflict)
- 7.5: merge_worktree_back() Layer 2 LLM success
- 7.6: merge_worktree_back() Layer 3 human approval
"""

from __future__ import annotations

import os

import pytest
import pytest_asyncio

from app.services import worktree_service as wt


def _git_available() -> bool:
    import shutil

    return shutil.which("git") is not None


pytestmark = pytest.mark.skipif(not _git_available(), reason="git not installed")


@pytest_asyncio.fixture
async def worktree_env(tmp_path, monkeypatch):
    """Point DATA_DIR at tmp_path and reset settings cache."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspaces"))
    from app.config import get_settings

    get_settings.cache_clear()
    os.makedirs(tmp_path / "data", exist_ok=True)
    os.makedirs(tmp_path / "workspaces", exist_ok=True)
    yield tmp_path
    get_settings.cache_clear()


async def _init_repo(path: str) -> None:
    os.makedirs(path, exist_ok=True)
    await wt._run_git(path, "init")
    await wt._run_git(path, "config", "user.email", "test@local")
    await wt._run_git(path, "config", "user.name", "Test")
    with open(os.path.join(path, "README.md"), "w", encoding="utf-8") as f:
        f.write("init\n")
    await wt._run_git(path, "add", "-A")
    await wt._run_git(path, "commit", "-m", "init")


# ─── 7.1: _extract_conflict_content ──────────────────────────────────────────
class TestExtractConflictContent:
    def test_reads_file_content(self, tmp_path):
        file_path = "src/app.py"
        abs_path = os.path.join(str(tmp_path), file_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write("<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> branch\n")
        content = wt._extract_conflict_content(str(tmp_path), file_path)
        assert "<<<<<<<" in content
        assert "=======" in content
        assert ">>>>>>>" in content
        assert "ours" in content
        assert "theirs" in content

    def test_returns_empty_on_missing_file(self, tmp_path):
        content = wt._extract_conflict_content(str(tmp_path), "nonexistent.py")
        assert content == ""


# ─── 7.2: _validate_syntax ───────────────────────────────────────────────────
class TestValidateSyntax:
    def test_py_valid(self):
        assert wt._validate_syntax("test.py", "x = 1\nprint(x)\n") is True

    def test_py_invalid(self):
        assert wt._validate_syntax("test.py", "def (\n") is False

    def test_json_valid(self):
        assert wt._validate_syntax("config.json", '{"key": "value"}') is True

    def test_json_invalid(self):
        assert wt._validate_syntax("config.json", '{"key": value}') is False

    def test_ts_valid(self):
        code = "function hello() {\n  return 'world'\n}\n"
        assert wt._validate_syntax("app.ts", code) is True

    def test_ts_invalid_unbalanced(self):
        code = "function hello() {\n  return 'world'\n"
        assert wt._validate_syntax("app.ts", code) is False

    def test_md_skips_validation(self):
        assert wt._validate_syntax("readme.md", "# Hello\n<<<<< broken\n") is True

    def test_txt_skips_validation(self):
        assert wt._validate_syntax("notes.txt", "anything goes }}}}") is True


# ─── 7.3: _llm_resolve_conflicts with mocked LLM ─────────────────────────────
class TestLlmResolveConflicts:
    async def test_llm_success_all_files(self, tmp_path, monkeypatch):
        """Mock LLM returns valid merged content that passes syntax check."""
        file_path = "app.py"
        abs_path = os.path.join(str(tmp_path), file_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write("<<<<<<< HEAD\nx = 1\n=======\nx = 2\n>>>>>>> branch\n")

        def _mock_call_llm(prompt: str) -> str:
            return "x = 2\n"

        monkeypatch.setattr(wt, "_call_llm_merge", _mock_call_llm)

        ok, resolved = await wt._llm_resolve_conflicts(str(tmp_path), [file_path])
        assert ok is True
        assert file_path in resolved
        # file was written with merged content
        with open(abs_path) as f:
            assert f.read() == "x = 2\n"

    async def test_llm_fails_syntax_check(self, tmp_path, monkeypatch):
        """Mock LLM returns content that fails syntax check."""
        file_path = "app.py"
        abs_path = os.path.join(str(tmp_path), file_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write("<<<<<<< HEAD\nx = 1\n=======\nx = 2\n>>>>>>> branch\n")

        def _mock_call_llm(prompt: str) -> str:
            return "def broken(\n"  # invalid Python

        monkeypatch.setattr(wt, "_call_llm_merge", _mock_call_llm)

        ok, resolved = await wt._llm_resolve_conflicts(str(tmp_path), [file_path])
        assert ok is False
        assert resolved == []

    async def test_llm_empty_conflict_files(self):
        ok, resolved = await wt._llm_resolve_conflicts("/tmp", [])
        assert ok is True
        assert resolved == []

    async def test_llm_exception_returns_failure(self, tmp_path, monkeypatch):
        file_path = "app.py"
        abs_path = os.path.join(str(tmp_path), file_path)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write("<<<<<<< HEAD\nx = 1\n=======\nx = 2\n>>>>>>> branch\n")

        def _mock_call_llm(prompt: str) -> str:
            raise RuntimeError("LLM API error")

        monkeypatch.setattr(wt, "_call_llm_merge", _mock_call_llm)

        ok, resolved = await wt._llm_resolve_conflicts(str(tmp_path), [file_path])
        assert ok is False


# ─── 7.4: merge_worktree_back Layer 1 success ────────────────────────────────
class TestMergeLayer1Success:
    async def test_layer1_no_conflict(self, worktree_env):
        ws = str(worktree_env / "layer1_ws")
        await _init_repo(ws)
        ref = await wt.create_worktree(ws, "t1", "Writer", "conv_l1")
        assert ref is not None
        with open(os.path.join(ref.path, "new.txt"), "w") as f:
            f.write("new content")
        result = await wt.merge_worktree_back(ref)
        assert result.success is True
        assert result.resolution_strategy == "auto"
        assert result.resolved_files == []
        assert os.path.isfile(os.path.join(ws, "new.txt"))


# ─── 7.5: merge_worktree_back Layer 2 LLM success ────────────────────────────
class TestMergeLayer2LlmSuccess:
    async def test_layer2_llm_resolves_conflict(self, worktree_env, monkeypatch):
        ws = str(worktree_env / "layer2_ws")
        await _init_repo(ws)
        ref1 = await wt.create_worktree(ws, "t1", "A", "conv_l2")
        ref2 = await wt.create_worktree(ws, "t2", "B", "conv_l2")
        assert ref1 and ref2

        with open(os.path.join(ref1.path, "README.md"), "w") as f:
            f.write("from t1\n")
        r1 = await wt.merge_worktree_back(ref1)
        assert r1.success
        await wt.cleanup_worktree(ref1)

        with open(os.path.join(ref2.path, "README.md"), "w") as f:
            f.write("from t2\n")

        # Mock LLM to return valid merged content
        def _mock_call_llm(prompt: str) -> str:
            return "from t1 and t2 merged\n"

        monkeypatch.setattr(wt, "_call_llm_merge", _mock_call_llm)

        r2 = await wt.merge_worktree_back(ref2)
        assert r2.success is True
        assert r2.resolution_strategy == "llm"
        assert "README.md" in r2.resolved_files


# ─── 7.6: merge_worktree_back Layer 3 human approval ─────────────────────────
class TestMergeLayer3HumanApproval:
    async def test_layer3_user_keeps_ours(self, worktree_env, monkeypatch):
        ws = str(worktree_env / "layer3_ours_ws")
        await _init_repo(ws)
        ref1 = await wt.create_worktree(ws, "t1", "A", "conv_l3o")
        ref2 = await wt.create_worktree(ws, "t2", "B", "conv_l3o")
        assert ref1 and ref2

        with open(os.path.join(ref1.path, "README.md"), "w") as f:
            f.write("from t1\n")
        r1 = await wt.merge_worktree_back(ref1)
        assert r1.success
        await wt.cleanup_worktree(ref1)

        with open(os.path.join(ref2.path, "README.md"), "w") as f:
            f.write("from t2\n")

        # Mock LLM to fail (force Layer 3)
        async def _mock_llm_fail(*args, **kwargs):
            return False, []

        monkeypatch.setattr(wt, "_llm_resolve_conflicts", _mock_llm_fail)

        # Mock _human_resolve_conflicts to simulate "keep ours"
        async def _mock_human_ours(wt_ref, conflict_files):
            for file_path in conflict_files:
                await wt._run_git(
                    wt_ref.main_workspace_path, "checkout", "--ours", file_path
                )
                await wt._run_git(wt_ref.main_workspace_path, "add", file_path)
            await wt._run_git(wt_ref.main_workspace_path, "commit", "--no-edit")
            return wt.MergeResult(
                success=True,
                resolution_strategy="manual",
                resolved_files=conflict_files,
            )

        monkeypatch.setattr(wt, "_human_resolve_conflicts", _mock_human_ours)

        r2 = await wt.merge_worktree_back(ref2)
        assert r2.success is True
        assert r2.resolution_strategy == "manual"
        assert "README.md" in r2.resolved_files

    async def test_layer3_user_abandons(self, worktree_env, monkeypatch):
        ws = str(worktree_env / "layer3_abandon_ws")
        await _init_repo(ws)
        ref1 = await wt.create_worktree(ws, "t1", "A", "conv_l3a")
        ref2 = await wt.create_worktree(ws, "t2", "B", "conv_l3a")
        assert ref1 and ref2

        with open(os.path.join(ref1.path, "README.md"), "w") as f:
            f.write("from t1\n")
        r1 = await wt.merge_worktree_back(ref1)
        assert r1.success
        await wt.cleanup_worktree(ref1)

        with open(os.path.join(ref2.path, "README.md"), "w") as f:
            f.write("from t2\n")

        async def _mock_llm_fail(*args, **kwargs):
            return False, []

        monkeypatch.setattr(wt, "_llm_resolve_conflicts", _mock_llm_fail)

        async def _mock_human_abandon(wt_ref, conflict_files):
            await wt._run_git(wt_ref.main_workspace_path, "merge", "--abort")
            return wt.MergeResult(
                success=False,
                conflict_files=conflict_files,
                error="User abandoned",
                resolution_strategy="abandoned",
            )

        monkeypatch.setattr(wt, "_human_resolve_conflicts", _mock_human_abandon)

        r2 = await wt.merge_worktree_back(ref2)
        assert r2.success is False
        assert r2.resolution_strategy == "abandoned"
