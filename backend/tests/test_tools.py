"""DB-backed tests for the tool system (phase 3).

Exercises the registry plus the artifact / fs / bash / approval / deploy /
report tools against an isolated SQLite DB and a real on-disk sandbox workspace.
"""

import asyncio
import os

import pytest
import pytest_asyncio

from app.tools.base import ToolContext


@pytest_asyncio.fixture
async def conversation(db, agents, tmp_path):
    """Create a conversation + on-disk sandbox workspace; return ids and paths."""
    from app.db.engine import get_db
    from app.db.models import Conversation, Workspace
    from app.utils.clock import now_ms
    from app.utils.ids import new_conversation_id, new_workspace_id

    ws_root = tmp_path / "ws"
    ws_root.mkdir(parents=True, exist_ok=True)
    conv_id = new_conversation_id()
    now = now_ms()

    async with get_db() as session:
        conv = Conversation(
            id=conv_id,
            title="T",
            mode="single",
            archived=False,
            fs_write_approval_mode="auto",
            created_at=now,
            updated_at=now,
        )
        conv.agent_ids_list = [agents["alice"]]
        conv.pinned_message_ids_list = []
        conv.bookmarked_message_ids_list = []
        session.add(conv)
        session.add(
            Workspace(
                id=new_workspace_id(),
                conversation_id=conv_id,
                root_path=str(ws_root),
                mode="sandbox",
                bound_path=None,
                created_at=now,
            )
        )

    return {
        "conversation_id": conv_id,
        "agent_id": agents["alice"],
        "workspace_root": str(ws_root),
    }


def _ctx(conversation) -> ToolContext:
    return ToolContext(
        conversation_id=conversation["conversation_id"],
        workspace_path=conversation["workspace_root"],
        agent_id=conversation["agent_id"],
        run_id="run_test",
        cancel_event=asyncio.Event(),
    )


async def _set_approval_mode(conversation_id: str, mode: str) -> None:
    from sqlalchemy import select

    from app.db.engine import get_db
    from app.db.models import Conversation

    async with get_db() as session:
        result = await session.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        conv = result.scalar_one()
        conv.fs_write_approval_mode = mode


# ─── registry ───────────────────────────────────────────────────────────────
async def test_registry_unknown_tool(conversation):
    from app.tools.registry import tool_registry

    result = await tool_registry.execute("does_not_exist", {}, _ctx(conversation))
    assert result.ok is False
    assert "Unknown tool" in result.error


async def test_registry_resolve_and_count():
    from app.tools.registry import tool_registry

    resolved = tool_registry.resolve(["bash", "fs_read"])
    assert [t.name for t in resolved] == ["bash", "fs_read"]
    with pytest.raises(ValueError):
        tool_registry.resolve(["nope"])


# ─── write/read artifact ─────────────────────────────────────────────────────
async def test_write_then_read_artifact(conversation):
    from app.tools.registry import tool_registry

    ctx = _ctx(conversation)
    write = await tool_registry.execute(
        "write_artifact",
        {"type": "document", "title": "Doc", "content": {"markdown": "# Hello"}},
        ctx,
    )
    assert write.ok, write.error
    artifact_id = write.value["artifactId"]
    assert write.value["version"] == 1

    read = await tool_registry.execute("read_artifact", {"artifactId": artifact_id}, ctx)
    assert read.ok, read.error
    assert read.value["content"]["content"] == "# Hello"
    assert read.value["type"] == "document"


async def test_write_artifact_version_chain(conversation):
    from app.tools.registry import tool_registry

    ctx = _ctx(conversation)
    v1 = await tool_registry.execute(
        "write_artifact",
        {"type": "document", "title": "Doc", "content": "v1"},
        ctx,
    )
    v2 = await tool_registry.execute(
        "write_artifact",
        {
            "type": "document",
            "title": "Doc",
            "content": "v2",
            "parentArtifactId": v1.value["artifactId"],
        },
        ctx,
    )
    assert v2.ok, v2.error
    assert v2.value["version"] == 2
    assert v2.value["parentArtifactId"] == v1.value["artifactId"]


async def test_write_artifact_invalid_content(conversation):
    from app.tools.registry import tool_registry

    result = await tool_registry.execute(
        "write_artifact",
        {"type": "diagram", "title": "D", "content": {"source": "garbage"}},
        _ctx(conversation),
    )
    assert result.ok is False
    assert "Mermaid" in result.error


# ─── fs tools ────────────────────────────────────────────────────────────────
async def test_fs_write_auto_then_read_and_list(conversation):
    from app.tools.registry import tool_registry

    ctx = _ctx(conversation)
    write = await tool_registry.execute(
        "fs_write", {"path": "sub/hello.txt", "content": "hi there"}, ctx
    )
    assert write.ok, write.error
    assert write.value["applied"] == "auto"
    assert os.path.isfile(os.path.join(conversation["workspace_root"], "sub", "hello.txt"))

    read = await tool_registry.execute("fs_read", {"path": "sub/hello.txt"}, ctx)
    assert read.ok and read.value["content"] == "hi there"
    assert "absolutePath" in read.value

    listing = await tool_registry.execute("fs_list", {"path": ""}, ctx)
    assert listing.ok
    names = {e["name"] for e in listing.value["entries"]}
    assert "sub" in names


async def test_fs_write_escape_rejected(conversation):
    from app.tools.registry import tool_registry

    result = await tool_registry.execute(
        "fs_write", {"path": "../escape.txt", "content": "x"}, _ctx(conversation)
    )
    assert result.ok is False
    assert "outside workspace" in result.error


async def test_fs_write_review_rejected(conversation):
    from app.services.pending_writes import pending_writes
    from app.tools.registry import tool_registry

    await _set_approval_mode(conversation["conversation_id"], "review")
    ctx = _ctx(conversation)

    task = asyncio.ensure_future(
        tool_registry.execute("fs_write", {"path": "r.txt", "content": "x"}, ctx)
    )
    # Wait for the pending write to register, then reject it.
    for _ in range(50):
        pendings = pending_writes.list_by_conversation(conversation["conversation_id"])
        if pendings:
            break
        await asyncio.sleep(0.01)
    assert pendings, "pending write never registered"
    assert pending_writes.reject(pendings[0].id)

    result = await asyncio.wait_for(task, timeout=2)
    assert result.ok is False
    assert "rejected" in result.error


async def test_fs_write_review_aborted_by_cancel(conversation):
    from app.services.pending_writes import pending_writes
    from app.tools.registry import tool_registry

    await _set_approval_mode(conversation["conversation_id"], "review")
    ctx = _ctx(conversation)
    task = asyncio.ensure_future(
        tool_registry.execute("fs_write", {"path": "r2.txt", "content": "x"}, ctx)
    )
    for _ in range(50):
        if pending_writes.list_by_conversation(conversation["conversation_id"]):
            break
        await asyncio.sleep(0.01)
    ctx.cancel_event.set()
    result = await asyncio.wait_for(task, timeout=2)
    assert result.ok is False


# ─── bash ────────────────────────────────────────────────────────────────────
async def test_bash_echo(conversation):
    from app.tools.registry import tool_registry

    ctx = _ctx(conversation)
    # 'echo' works in both PowerShell and POSIX shells.
    result = await tool_registry.execute("bash", {"command": "echo hello123"}, ctx)
    assert result.ok, result.error
    assert "hello123" in result.value["output"]
    assert result.value["exitCode"] == 0


async def test_bash_blocked_command(conversation):
    from app.tools.registry import tool_registry
    from app.utils.platform import IS_WINDOWS

    cmd = "Remove-Item -Recurse -Force C:/data" if IS_WINDOWS else "rm -rf /"
    result = await tool_registry.execute("bash", {"command": cmd}, _ctx(conversation))
    assert result.ok is False
    assert "safety policy" in result.error


# ─── report_task_result / plan_tasks ────────────────────────────────────────
async def test_report_task_result(conversation):
    from app.tools.registry import tool_registry

    result = await tool_registry.execute(
        "report_task_result",
        {"status": "complete", "summary": "  done  ", "blockers": ["", "  "]},
        _ctx(conversation),
    )
    assert result.ok, result.error
    assert result.value["status"] == "complete"
    assert result.value["summary"] == "done"
    assert "blockers" not in result.value  # empty strings filtered out


async def test_plan_tasks_ack(conversation):
    from app.tools.registry import tool_registry

    result = await tool_registry.execute(
        "plan_tasks",
        {
            "reasoning": "split work",
            "tasks": [
                {"id": "t1", "agentId": "ag_alice", "task": "do a"},
                {"id": "t2", "agentId": "ag_alice", "task": "do b", "dependsOn": ["t1"]},
            ],
        },
        _ctx(conversation),
    )
    assert result.ok, result.error
    assert result.value == {"acknowledged": True, "taskCount": 2}


# ─── deploy_artifact ─────────────────────────────────────────────────────────
async def test_deploy_artifact(conversation, tmp_path, monkeypatch):
    from app.tools.registry import tool_registry

    monkeypatch.setenv("AGENTHUB_DATA_DIR", str(tmp_path / "data"))
    ctx = _ctx(conversation)
    write = await tool_registry.execute(
        "write_artifact",
        {
            "type": "web_app",
            "title": "Site",
            "content": {"files": {"index.html": "<h1>Hi</h1>"}, "entry": "index.html"},
        },
        ctx,
    )
    assert write.ok, write.error

    deploy = await tool_registry.execute(
        "deploy_artifact", {"artifactId": write.value["artifactId"]}, ctx
    )
    assert deploy.ok, deploy.error
    assert deploy.value["status"] == "ready"
    assert deploy.value["previewPath"].startswith("/deployments/")
    assert deploy.value["sourceType"] == "artifact"


async def test_deploy_artifact_non_web_app(conversation, tmp_path, monkeypatch):
    from app.tools.registry import tool_registry

    monkeypatch.setenv("AGENTHUB_DATA_DIR", str(tmp_path / "data2"))
    ctx = _ctx(conversation)
    write = await tool_registry.execute(
        "write_artifact", {"type": "document", "title": "D", "content": "hi"}, ctx
    )
    deploy = await tool_registry.execute(
        "deploy_artifact", {"artifactId": write.value["artifactId"]}, ctx
    )
    assert deploy.ok, deploy.error
    assert deploy.value["status"] == "failed"
    assert "cannot be deployed" in deploy.value["error"]
