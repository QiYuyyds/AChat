"""Tests for conversation fork functionality.

Covers: fork_conversation service, worktree fork lifecycle, GitInitRequiredError,
delete cleanup, and the fork API endpoint.
"""

from __future__ import annotations

import os
import shutil

import pytest
import pytest_asyncio

from app.services import conversation_service as cs
from app.services import worktree_service as wt
from app.services.conversation_service import GitInitRequiredError


def _git_available() -> bool:
    return shutil.which("git") is not None


pytestmark = pytest.mark.skipif(not _git_available(), reason="git not installed")


# ─── Helpers ───────────────────────────────────────────────────────────────────
async def _init_git_repo(path: str) -> None:
    """Create a real git repo with an initial commit at *path*."""
    os.makedirs(path, exist_ok=True)
    await wt._run_git(path, "init")
    await wt._run_git(path, "config", "user.email", "test@local")
    await wt._run_git(path, "config", "user.name", "Test")
    with open(os.path.join(path, "README.md"), "w", encoding="utf-8") as f:
        f.write("init\n")
    await wt._run_git(path, "add", "-A")
    await wt._run_git(path, "commit", "-m", "init")


async def _add_message(conv_id: str, role: str, content: str, status: str = "complete", hidden: bool = False) -> str:
    """Insert a message directly into the DB and return its ID."""
    from app.db.engine import get_local_db
    from app.db.models import Message
    from app.utils.clock import now_ms
    from app.utils.ids import new_message_id

    msg_id = new_message_id()
    async with get_local_db() as db:
        msg = Message(
            id=msg_id,
            conversation_id=conv_id,
            role=role,
            agent_id=None,
            parts=[{"type": "text", "content": content}],
            status=status,
            created_at=now_ms(),
            hidden=hidden,
        )
        db.add(msg)
    return msg_id


async def _add_artifact(conv_id: str, agent_id: str) -> str:
    """Insert an artifact directly into the DB and return its ID."""
    from app.db.engine import get_local_db
    from app.db.models import Artifact
    from app.utils.clock import now_ms
    from app.utils.ids import new_artifact_id

    art_id = new_artifact_id()
    async with get_local_db() as db:
        art = Artifact(
            id=art_id,
            conversation_id=conv_id,
            type="document",
            title="test doc",
            content={"type": "document", "format": "markdown", "content": "hello"},
            version=1,
            parent_artifact_id=None,
            created_by_agent_id=agent_id,
            created_at=now_ms(),
        )
        db.add(art)
    return art_id


# ─── Worktree tests ────────────────────────────────────────────────────────────
class TestEnsureGitInitGitignore:
    async def test_writes_smart_gitignore_when_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "ws"))
        from app.config import get_settings
        get_settings.cache_clear()

        repo = str(tmp_path / "repo")
        os.makedirs(repo, exist_ok=True)
        await wt._run_git(repo, "init")
        await wt._run_git(repo, "config", "user.email", "t@l")
        await wt._run_git(repo, "config", "user.name", "T")

        await wt.ensure_git_init(repo)
        gi = os.path.join(repo, ".gitignore")
        assert os.path.exists(gi)
        content = open(gi, encoding="utf-8").read()
        assert "node_modules/" in content
        assert "__pycache__/" in content
        assert ".agenthub-data/" in content
        get_settings.cache_clear()

    async def test_appends_agenthub_data_when_gitignore_exists(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "ws"))
        from app.config import get_settings
        get_settings.cache_clear()

        repo = str(tmp_path / "repo")
        os.makedirs(repo, exist_ok=True)
        await wt._run_git(repo, "init")
        await wt._run_git(repo, "config", "user.email", "t@l")
        await wt._run_git(repo, "config", "user.name", "T")
        with open(os.path.join(repo, ".gitignore"), "w", encoding="utf-8") as f:
            f.write("*.tmp\n")

        await wt.ensure_git_init(repo)
        gi = os.path.join(repo, ".gitignore")
        content = open(gi, encoding="utf-8").read()
        assert "*.tmp" in content
        assert ".agenthub-data/" in content
        get_settings.cache_clear()


class TestCreateForkWorktree:
    async def test_creates_worktree_under_correct_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "ws"))
        from app.config import get_settings
        get_settings.cache_clear()

        source = str(tmp_path / "source")
        await _init_git_repo(source)

        fork_conv_id = "conv_test_fork_1"
        ref = await wt.create_fork_worktree(source, fork_conv_id, "test_user")
        assert ref is not None
        assert os.path.isdir(ref.path)
        assert fork_conv_id in ref.path
        assert ref.branch_name == f"fork/{fork_conv_id}"
        get_settings.cache_clear()


class TestCleanupForkWorktree:
    async def test_cleanup_removes_worktree(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "ws"))
        from app.config import get_settings
        get_settings.cache_clear()

        source = str(tmp_path / "source")
        await _init_git_repo(source)

        fork_conv_id = "conv_test_fork_2"
        ref = await wt.create_fork_worktree(source, fork_conv_id, "test_user")
        assert ref is not None
        assert os.path.isdir(ref.path)

        await wt.cleanup_fork_worktree(ref.path, ref.branch_name)
        assert not os.path.isdir(ref.path)
        get_settings.cache_clear()


# ─── Service-level fork tests ─────────────────────────────────────────────────
class TestForkConversation:
    async def test_deep_copies_messages_and_artifacts(self, db, agents, test_user, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "ws"))
        from app.config import get_settings
        get_settings.cache_clear()

        # Create a source conversation with git-init'd sandbox workspace
        conv = await cs.create_conversation(
            mode="single",
            agent_ids=[agents["alice"]],
            user_id=test_user["id"],
        )
        # Send a user message
        msg_id = await _add_message(conv.id, "user", "hello")
        # Add an artifact
        await _add_artifact(conv.id, agents["alice"])

        # Fork from the user message
        new_conv = await cs.fork_conversation(
            source_conv_id=conv.id,
            fork_point_message_id=msg_id,
            user_id=test_user["id"],
        )
        assert new_conv.parent_conversation_id == conv.id
        assert new_conv.fork_point_message_id == msg_id
        assert new_conv.title.endswith("(分支)")

        # Verify messages were deep-copied
        new_msgs = await cs.list_messages(new_conv.id)
        assert len(new_msgs) == 1
        assert new_msgs[0].parts == [{"type": "text", "content": "hello"}]

        # Verify artifacts were deep-copied
        from app.db.engine import get_local_db
        from app.db.models import Artifact
        async with get_local_db() as session:
            from sqlalchemy import select
            result = await session.execute(
                select(Artifact).where(Artifact.conversation_id == new_conv.id)
            )
            arts = result.scalars().all()
            assert len(arts) == 1
            assert arts[0].type == "document"
        get_settings.cache_clear()

    async def test_hidden_messages_excluded(self, db, agents, test_user, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "ws"))
        from app.config import get_settings
        get_settings.cache_clear()

        conv = await cs.create_conversation(
            mode="single",
            agent_ids=[agents["alice"]],
            user_id=test_user["id"],
        )
        visible_msg = await _add_message(conv.id, "user", "visible")
        await _add_message(conv.id, "agent", "hidden", hidden=True)
        fork_point = await _add_message(conv.id, "agent", "reply")

        new_conv = await cs.fork_conversation(
            source_conv_id=conv.id,
            fork_point_message_id=fork_point,
            user_id=test_user["id"],
        )
        new_msgs = await cs.list_messages(new_conv.id)
        # Should have: visible user msg + visible agent reply (NOT the hidden one)
        assert len(new_msgs) == 2
        for m in new_msgs:
            assert not m.hidden
        get_settings.cache_clear()

    async def test_fork_from_streaming_returns_error(self, db, agents, test_user, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "ws"))
        from app.config import get_settings
        get_settings.cache_clear()

        conv = await cs.create_conversation(
            mode="single",
            agent_ids=[agents["alice"]],
            user_id=test_user["id"],
        )
        streaming_msg = await _add_message(conv.id, "agent", "streaming...", status="streaming")

        with pytest.raises(ValueError, match="streaming"):
            await cs.fork_conversation(
                source_conv_id=conv.id,
                fork_point_message_id=streaming_msg,
                user_id=test_user["id"],
            )
        get_settings.cache_clear()

    async def test_preserves_agent_ids_mode_dispatch_mode(self, db, agents, test_user, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "ws"))
        from app.config import get_settings
        get_settings.cache_clear()

        conv = await cs.create_conversation(
            mode="group",
            agent_ids=[agents["alice"], agents["orch"]],
            dispatch_mode="orchestrated",
            user_id=test_user["id"],
        )
        msg_id = await _add_message(conv.id, "user", "hello")

        new_conv = await cs.fork_conversation(
            source_conv_id=conv.id,
            fork_point_message_id=msg_id,
            user_id=test_user["id"],
        )
        assert set(new_conv.agent_ids) == {agents["alice"], agents["orch"]}
        assert new_conv.mode == "group"
        assert new_conv.dispatch_mode == "orchestrated"
        get_settings.cache_clear()

    async def test_git_init_required_when_non_git(self, db, agents, test_user, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "ws"))
        from app.config import get_settings
        get_settings.cache_clear()

        # Create conversation with local workspace pointing to non-git dir
        non_git = str(tmp_path / "non_git_project")
        os.makedirs(non_git, exist_ok=True)

        conv = await cs.create_conversation(
            mode="single",
            agent_ids=[agents["alice"]],
            bound_path=non_git,
            user_id=test_user["id"],
        )
        msg_id = await _add_message(conv.id, "user", "hello")

        with pytest.raises(GitInitRequiredError) as exc_info:
            await cs.fork_conversation(
                source_conv_id=conv.id,
                fork_point_message_id=msg_id,
                user_id=test_user["id"],
                confirm_git_init=False,
            )
        assert exc_info.value.source_path == non_git
        get_settings.cache_clear()


# ─── Delete cleanup test ──────────────────────────────────────────────────────
class TestDeleteForkCleanup:
    async def test_deleting_forked_conversation_cleans_worktree(self, db, agents, test_user, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "ws"))
        from app.config import get_settings
        get_settings.cache_clear()

        # Create source conversation
        conv = await cs.create_conversation(
            mode="single",
            agent_ids=[agents["alice"]],
            user_id=test_user["id"],
        )
        msg_id = await _add_message(conv.id, "user", "hello")

        # Fork it
        new_conv = await cs.fork_conversation(
            source_conv_id=conv.id,
            fork_point_message_id=msg_id,
            user_id=test_user["id"],
        )
        assert new_conv.parent_conversation_id is not None

        # Get the workspace path before deleting
        from app.db.engine import get_local_db
        from app.db.models import Workspace
        from sqlalchemy import select
        async with get_local_db() as session:
            ws_result = await session.execute(
                select(Workspace.root_path).where(Workspace.conversation_id == new_conv.id)
            )
            ws_path = ws_result.first()[0]

        assert os.path.isdir(ws_path)

        # Delete the forked conversation
        await cs.delete_conversation(new_conv.id)

        # Worktree directory should be cleaned up
        assert not os.path.isdir(ws_path)
        get_settings.cache_clear()


# ─── API-level test ───────────────────────────────────────────────────────────
class TestForkAPI:
    async def test_returns_409_requires_git_init(self, db, agents, test_user, api_client, tmp_path, monkeypatch):
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "ws"))
        from app.config import get_settings
        get_settings.cache_clear()

        non_git = str(tmp_path / "non_git_api")
        os.makedirs(non_git, exist_ok=True)

        # Create conversation with local non-git workspace via API
        resp = await api_client.post("/api/conversations", json={
            "mode": "single",
            "agentIds": [agents["alice"]],
            "boundPath": non_git,
        })
        assert resp.status_code == 201
        conv_data = resp.json()["conversation"]
        conv_id = conv_data["id"]

        # Add a message via API
        resp = await api_client.post(f"/api/conversations/{conv_id}/messages", json={
            "content": "hello",
        })
        msg_id = resp.json()["messageId"]

        # Attempt fork without confirmGitInit → 409
        resp = await api_client.post(f"/api/conversations/{conv_id}/fork", json={
            "forkPointMessageId": msg_id,
        })
        assert resp.status_code == 409
        body = resp.json()
        assert body["requiresGitInit"] is True
        assert body["sourcePath"] is not None
        get_settings.cache_clear()
