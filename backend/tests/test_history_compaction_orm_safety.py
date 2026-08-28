"""Regression tests for ORM pollution fix in history compaction.

Covers:
- 3.1: build_history_for does not mutate persisted Message.parts in the DB
- 3.2: to_compact_messages_orm on detached objects does not produce dirty state
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db.engine import get_db, get_local_db
from app.db.models import Conversation, Message
from app.services.compact_pipeline import to_compact_messages_orm
from app.services.conversation_context import BuildHistoryOptions
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
    if tool_name == "bash":
        return "line\n" * 200
    if tool_name == "web_search":
        return {
            "answer": "x" * 500,
            "results": [
                {"title": f"Result {i}", "url": f"https://example.com/{i}", "content": "x" * 1000}
                for i in range(5)
            ],
        }
    return {"data": "x" * 5000}


# ─── 3.1: DB immutability after build_history_for ──────────────────────────


@pytest.mark.asyncio
async def test_build_history_does_not_corrupt_db(db, agents):
    """build_history_for must not mutate persisted Message.parts in the DB.

    Seeds a conversation with 5+ tool-calling turns, calls build_history_for
    (which uses CompactMessage pipeline internally), then re-reads messages
    from DB and asserts original tool_result content is preserved.
    """
    alice = agents["alice"]
    conv_id = await _seed_conversation(alice)

    tool_types = [
        ("bash", {"command": "ls -la"}, _large_tool_result("bash")),
        ("fs_write", {"path": "src/app.py", "content": "x" * 5000}, _large_tool_result("fs_write")),
        ("web_search", {"query": "python testing"}, _large_tool_result("web_search")),
        ("bash", {"command": "echo hello"}, "output_3"),
        ("bash", {"command": "pwd"}, "output_4"),
    ]

    await _add_message("u0", conv_id, "user", [{"type": "text", "content": "do some work"}], 100)
    for i, (tool_name, args, result) in enumerate(tool_types):
        parts = [
            {"type": "text", "content": f"Turn {i}: using {tool_name}"},
            {"type": "tool_use", "callId": f"c{i}", "toolName": tool_name, "args": args},
            {"type": "tool_result", "callId": f"c{i}", "result": result, "isError": False},
        ]
        await _add_message(f"t{i}", conv_id, "agent", parts, 200 + i * 100, agent_id=alice)

    original_results: dict[str, dict] = {}
    async with get_db() as db_read:
        for i in range(len(tool_types)):
            msg = await db_read.get(Message, f"t{i}")
            assert msg is not None
            for p in msg.parts_list:
                if p.get("type") == "tool_result":
                    original_results[f"t{i}"] = p["result"]

    history = await __import__(
        "app.services.conversation_context", fromlist=["build_history_for"]
    ).build_history_for(alice, conv_id, BuildHistoryOptions())

    assert len(history) > 0

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
            assert tool_result_part["result"] == original_results[f"t{i}"], (
                f"t{i} tool_result was corrupted in DB after build_history_for"
            )
            result_str = json.dumps(tool_result_part["result"], ensure_ascii=False)
            assert "masked" not in result_str, (
                f"t{i} DB content contains mask marker — ORM pollution detected"
            )


# ─── 3.2: to_compact_messages_orm on detached objects ─────────────────────


@pytest.mark.asyncio
async def test_to_compact_on_detached_objects_no_dirty_state(db, agents):
    """to_compact_messages_orm on detached ORM objects does not produce dirty state."""
    alice = agents["alice"]
    conv_id = await _seed_conversation(alice)

    await _add_message("u0", conv_id, "user", [{"type": "text", "content": "hi"}], 100)
    for i in range(4):
        parts = [
            {"type": "tool_use", "callId": f"c{i}", "toolName": "bash", "args": {"command": f"cmd{i}"}},
            {"type": "tool_result", "callId": f"c{i}", "result": f"output_{i}_" + "x" * 200, "isError": False},
        ]
        await _add_message(f"t{i}", conv_id, "agent", parts, 200 + i * 100, agent_id=alice)

    async with get_local_db() as session:
        result = await session.execute(
            select(Message)
            .where(Message.conversation_id == conv_id, Message.status == "complete")
            .order_by(Message.created_at)
        )
        messages = list(result.scalars().all())
        session.expunge_all()

        compact_msgs = to_compact_messages_orm(messages)

        dirty = list(session.dirty)
        assert len(dirty) == 0, (
            f"Session has {len(dirty)} dirty objects after to_compact_messages_orm"
        )

        assert len(compact_msgs) == 5
        assert compact_msgs[0].role == "user"
        for i in range(1, 5):
            assert compact_msgs[i].role == "assistant"
            assert compact_msgs[i].tool_calls is not None
