"""Regression tests for ORM pollution fix in history compaction.

Covers:
- 3.1: build_history_for does not mutate persisted Message.parts in the DB
- 3.2: prune_old_tool_results on detached objects does not produce dirty state
- 3.3: new summarizers produce shorter output than the original for each tool type
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.db.engine import get_db, get_local_db
from app.db.models import Conversation, Message
from app.services.compact_pipeline import summarize_tool_result_full
from app.services.conversation_context import (
    BuildHistoryOptions,
    prune_old_tool_results,
)
from app.utils.clock import now_ms

# ─── helpers ────────────────────────────────────────────────────────────────


def _agent_turn(msg_id, call_id, tool_name, args, result, text=None, ts=0):
    """Build an agent Message with tool_use + tool_result parts (one turn)."""
    parts = []
    if text:
        parts.append({"type": "text", "content": text})
    parts.append({
        "type": "tool_use",
        "callId": call_id,
        "toolName": tool_name,
        "args": args,
    })
    parts.append({
        "type": "tool_result",
        "callId": call_id,
        "result": result,
        "isError": False,
    })
    return SimpleNamespace(
        id=msg_id, role="agent", agent_id="a1", parts_list=parts, created_at=ts,
    )


def _user_msg(msg_id, content, ts=0):
    return SimpleNamespace(
        id=msg_id,
        role="user",
        agent_id=None,
        parts_list=[{"type": "text", "content": content}],
        created_at=ts,
    )


async def _seed_conversation(agent_id: str) -> str:
    now = now_ms()
    conv_id = "conv_orm_test"
    async with get_db() as db:
        conv = Conversation(
            id=conv_id,
            user_id="test_user_1",
            title="orm safety test",
            mode="single",
            archived=False,
            fs_write_approval_mode="auto",
            created_at=now,
            updated_at=now,
        )
        conv.agent_ids_list = [agent_id]
        conv.pinned_message_ids_list = []
        db.add(conv)
    return conv_id


async def _add_message(
    msg_id: str,
    conv_id: str,
    role: str,
    parts: list[dict],
    created_at: int,
    *,
    agent_id: str | None = None,
    status: str = "complete",
) -> None:
    async with get_db() as db:
        m = Message(
            id=msg_id,
            conversation_id=conv_id,
            role=role,
            agent_id=agent_id,
            status=status,
            created_at=created_at,
        )
        m.parts_list = parts
        m.mentioned_agent_ids_list = []
        db.add(m)


def _large_tool_result(tool_name: str) -> dict:
    """Generate a large tool_result content for each tool type."""
    if tool_name == "write_artifact":
        return {
            "artifactId": "art_abc123",
            "title": "My Web App",
            "type": "web_app",
            "version": 1,
            "parentArtifactId": None,
        }
    if tool_name == "read_artifact":
        return {
            "id": "art_abc123",
            "type": "web_app",
            "title": "My Web App",
            "content": {"files": {"index.html": "x" * 5000}, "entry": "index.html"},
            "version": 1,
        }
    if tool_name == "fs_write":
        return {
            "path": "src/app.py",
            "absolutePath": "/workspace/src/app.py",
            "cwd": "/workspace",
            "bytes": 5000,
            "applied": "auto",
            "oldContent": None,
            "newContent": "x" * 5000,
        }
    if tool_name == "fs_glob":
        return {"files": [f"file_{i}.py" for i in range(200)], "truncated": True}
    if tool_name == "web_search":
        return {
            "answer": "x" * 500,
            "results": [
                {"title": f"Result {i}", "url": f"https://example.com/{i}", "content": "x" * 1000}
                for i in range(5)
            ],
        }
    if tool_name == "load_skill":
        return {"slug": "my_skill", "body": "x" * 5000}
    if tool_name == "bash":
        return "line\n" * 200
    if tool_name == "task_dispatch":
        return {"status": "completed", "summary": "x" * 2000, "stopReason": "done"}
    return {"data": "x" * 5000}


# ─── 3.1: DB immutability after build_history_for ──────────────────────────


@pytest.mark.asyncio
async def test_build_history_does_not_corrupt_db(db, agents):
    """build_history_for must not mutate persisted Message.parts in the DB.

    Seeds a conversation with 5+ tool-calling turns using various tool types,
    calls build_history_for (which internally calls prune_old_tool_results),
    then re-reads messages from DB and asserts original tool_result content
    is preserved (no compact markers in the DB).
    """
    alice = agents["alice"]
    conv_id = await _seed_conversation(alice)

    # Seed 5+ turns with different tool types
    tool_types = [
        ("bash", {"command": "ls -la"}, _large_tool_result("bash")),
        ("write_artifact", {"type": "web_app", "title": "My App", "content": {}}, _large_tool_result("write_artifact")),
        ("fs_write", {"path": "src/app.py", "content": "x" * 5000}, _large_tool_result("fs_write")),
        ("fs_glob", {"pattern": "**/*.py"}, _large_tool_result("fs_glob")),
        ("web_search", {"query": "python testing"}, _large_tool_result("web_search")),
    ]

    # User message + 5 agent turns (enough to trigger pruning of old turns)
    await _add_message("u0", conv_id, "user", [{"type": "text", "content": "do some work"}], 100)
    for i, (tool_name, args, result) in enumerate(tool_types):
        parts = [
            {"type": "text", "content": f"Turn {i}: using {tool_name}"},
            {"type": "tool_use", "callId": f"c{i}", "toolName": tool_name, "args": args},
            {"type": "tool_result", "callId": f"c{i}", "result": result, "isError": False},
        ]
        await _add_message(f"t{i}", conv_id, "agent", parts, 200 + i * 100, agent_id=alice)

    # Snapshot the original tool_result contents from DB BEFORE build_history_for
    original_results: dict[str, dict] = {}
    async with get_db() as db_read:
        for i in range(len(tool_types)):
            msg = await db_read.get(Message, f"t{i}")
            assert msg is not None
            for p in msg.parts_list:
                if p.get("type") == "tool_result":
                    original_results[f"t{i}"] = p["result"]

    # Call build_history_for (triggers prune_old_tool_results internally)
    history = await __import__(
        "app.services.conversation_context", fromlist=["build_history_for"]
    ).build_history_for(alice, conv_id, BuildHistoryOptions())

    # build_history_for should return non-empty history
    assert len(history) > 0

    # Re-read messages from DB and verify original tool_result content is preserved
    async with get_db() as db_read:
        for i in range(len(tool_types)):
            msg = await db_read.get(Message, f"t{i}")
            assert msg is not None
            tool_result_part = None
            for p in msg.parts_list:
                if p.get("type") == "tool_result":
                    tool_result_part = p
                    break
            assert tool_result_part is not None, f"t{i} should have a tool_result part"
            # The DB content must still be the original, not a compact marker
            assert tool_result_part["result"] == original_results[f"t{i}"], (
                f"t{i} tool_result was corrupted in DB after build_history_for"
            )
            # Verify no compact markers leaked into the DB
            result_str = json.dumps(tool_result_part["result"], ensure_ascii=False)
            assert "compacted" not in result_str, (
                f"t{i} DB content contains compact marker — ORM pollution detected"
            )


# ─── 3.2: prune_old_tool_results on detached objects ───────────────────────


@pytest.mark.asyncio
async def test_prune_on_detached_objects_no_dirty_state(db, agents):
    """prune_old_tool_results on detached ORM objects does not produce dirty state.

    Loads Message objects from DB, detaches them via expunge_all, then calls
    prune_old_tool_results. The session should not have any dirty objects.
    """
    alice = agents["alice"]
    conv_id = await _seed_conversation(alice)

    # Seed 4 tool-calling turns (enough to trigger pruning of old turns)
    await _add_message("u0", conv_id, "user", [{"type": "text", "content": "hi"}], 100)
    for i in range(4):
        parts = [
            {"type": "tool_use", "callId": f"c{i}", "toolName": "bash", "args": {"command": f"cmd{i}"}},
            {"type": "tool_result", "callId": f"c{i}", "result": f"output_{i}_" + "x" * 200, "isError": False},
        ]
        await _add_message(f"t{i}", conv_id, "agent", parts, 200 + i * 100, agent_id=alice)

    # Load messages in a session, then expunge all
    from sqlalchemy import select

    async with get_local_db() as session:
        result = await session.execute(
            select(Message)
            .where(Message.conversation_id == conv_id, Message.status == "complete")
            .order_by(Message.created_at)
        )
        messages = list(result.scalars().all())

        # Expunge all to detach from session
        session.expunge_all()

        # Now call prune_old_tool_results on detached objects
        pruned = prune_old_tool_results(messages, keep_recent_turns=2)

        # Verify no objects are dirty in the session
        dirty = list(session.dirty)
        assert len(dirty) == 0, (
            f"Session has {len(dirty)} dirty objects after prune on detached objects"
        )

        # Verify old turns were pruned (parts changed)
        # pruned[0] is the user message; old agent turns start at pruned[1]
        old_agent_msgs = [m for m in pruned if getattr(m, "role", "") == "agent"]
        # The first 2 agent turns are old (keep_recent_turns=2)
        assert len(old_agent_msgs) >= 2, "Expected at least 2 old agent turns"
        old_msg = old_agent_msgs[0]  # first old agent turn
        has_compact = any(
            p.get("type") == "text" and "compacted" in p.get("content", "")
            for p in old_msg.parts_list
        )
        assert has_compact, "Old turn should have been pruned with compact marker"


# ─── 3.3: New summarizers produce shorter output ───────────────────────────


@pytest.mark.parametrize(
    "tool_name,args,content",
    [
        ("load_skill", {"slug": "my_skill"}, json.dumps({"slug": "my_skill", "body": "x" * 5000})),
        ("write_artifact", {"type": "web_app", "title": "App"}, json.dumps({"artifactId": "art_1", "title": "App", "type": "web_app", "version": 1})),
        ("read_artifact", {"artifactId": "art_1"}, json.dumps({"id": "art_1", "type": "web_app", "title": "App", "content": "x" * 5000, "version": 1})),
        ("update_artifact", {"artifactId": "art_1"}, json.dumps({"artifactId": "art_1", "updatedFiles": ["a.py", "b.py", "c.py"]})),
        ("fs_write", {"path": "src/app.py", "content": "x" * 5000}, json.dumps({"path": "src/app.py", "absolutePath": "/w/src/app.py", "cwd": "/w", "bytes": 5000, "applied": "auto", "oldContent": None, "newContent": "x" * 5000})),
        ("fs_edit", {"path": "src/app.py", "oldContent": "", "newContent": "x" * 5000}, json.dumps({"path": "src/app.py", "absolutePath": "/w/src/app.py", "cwd": "/w", "bytes": 5000, "applied": "auto", "oldContent": "", "newContent": "x" * 5000})),
        ("fs_glob", {"pattern": "**/*.py"}, json.dumps({"files": [f"file_{i}.py" for i in range(200)], "truncated": True})),
        ("web_search", {"query": "python"}, json.dumps({"answer": "x" * 500, "results": [{"title": f"R{i}", "url": f"https://x.com/{i}", "content": "x" * 1000} for i in range(5)]})),
        ("read_attachment", {"fileName": "doc.pdf"}, json.dumps({"fileName": "doc.pdf", "content": "x" * 5000, "truncated": False})),
        ("deploy_artifact", {"artifactId": "art_1"}, json.dumps({"id": "dep_1", "artifactId": "art_1", "title": "App", "previewPath": "/dep/dep_1", "status": "ready", "sourceType": "artifact"})),
        ("deploy_workspace", {"workspacePath": "/w"}, json.dumps({"id": "dep_2", "artifactId": None, "title": "Workspace", "previewPath": "/dep/dep_2", "status": "ready", "sourceType": "workspace"})),
        ("task_dispatch", {"agentId": "ag_1", "task": "do work"}, json.dumps({"status": "completed", "summary": "x" * 2000})),
        ("dispatch_plan", {}, json.dumps({"tasks": {f"task_{i}": {"status": "completed", "summary": "x" * 500} for i in range(5)}})),
        ("create_plan", {"complexity": "medium"}, json.dumps({"planId": "plan_1", "stepCount": 5, "steps": [{"id": f"s{i}"} for i in range(5)]})),
        ("plan_step", {"planId": "plan_1", "stepId": "s0", "status": "done"}, json.dumps({"planId": "plan_1", "stepId": "s0", "status": "done"})),
        ("add_plan_steps", {"planId": "plan_1"}, json.dumps({"planId": "plan_1", "addedCount": 3, "totalSteps": 8})),
        ("manage_agents", {"action": "list"}, json.dumps({"items": [{"id": f"ag_{i}"} for i in range(10)], "total": 10})),
        ("manage_memory", {"action": "list"}, json.dumps({"items": [{"key": f"k{i}"} for i in range(10)], "total": 10})),
        ("ask_user", {"questions": [{"question": "Which option?", "header": "h", "options": [{"label": "A"}, {"label": "B"}]}]}, json.dumps({"answers": {"Which option?": "A, B ; note: " + "x" * 5000}})),
    ],
)
def test_new_summarizers_produce_shorter_output(tool_name, args, content):
    """Each new summarizer should produce shorter output than the original at stage 1."""
    new_content, summary, recover = summarize_tool_result_full(
        tool_name, args, content, stage=1,
    )
    # The summarized content must be shorter than the original
    assert len(new_content) < len(content), (
        f"{tool_name}: summarized ({len(new_content)}) should be shorter than "
        f"original ({len(content)})"
    )
    # The summary should be a non-empty string
    assert summary, f"{tool_name}: summary should not be empty"
    # The recover hint should be a non-empty string
    assert recover, f"{tool_name}: recover hint should not be empty"
