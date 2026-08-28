"""Tests for unified cross-run compaction pipeline.

Covers the CompactMessage-based unified pipeline:
- ``to_compact_messages_orm`` converts ORM Message objects to CompactMessage
- ``run_compact_pipeline_unified`` with stage=1 (mask) and stage=3 (fold)
- ``from_compact_messages`` converts back to OpenAI dict format
- Pinned messages are protected from compaction
- Whitelisted tools (code_explore, fs_read outline/head) are preserved
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.services.compact_pipeline import (
    CompactMessage,
    from_compact_messages,
    run_compact_pipeline_unified,
    to_compact_messages_orm,
)
from app.services.transcript_renderer import estimate_dict_message_tokens
from app.utils.model_registry import estimate_tokens


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


def _agent_text_msg(msg_id, content, ts=0):
    return SimpleNamespace(
        id=msg_id,
        role="agent",
        agent_id="a1",
        parts_list=[{"type": "text", "content": content}],
        created_at=ts,
    )


# ─── to_compact_messages_orm ─────────────────────────────────────────────────


def test_to_compact_messages_orm_basic():
    """ORM Message with tool_use + tool_result → CompactMessage with tool_calls + tool result."""
    msg = _agent_turn("t0", "c0", "bash", {"command": "ls"}, "output", ts=0)
    result = to_compact_messages_orm([msg])
    # to_compact_messages_orm splits into assistant + tool messages
    assert len(result) == 2
    asst = result[0]
    tool = result[1]
    assert asst.role == "assistant"
    assert len(asst.tool_calls) == 1
    assert asst.tool_calls[0]["function"]["name"] == "bash"
    assert tool.role == "tool"
    assert tool.content == "output"


def test_to_compact_messages_orm_user_message():
    """User message → CompactMessage with role=user, content from text parts."""
    msg = _user_msg("u0", "hello world", ts=0)
    result = to_compact_messages_orm([msg])
    assert len(result) == 1
    cm = result[0]
    assert cm.role == "user"
    assert "hello world" in cm.content


def test_to_compact_messages_orm_multiple():
    """Multiple messages of different types convert correctly."""
    msgs = [
        _user_msg("u0", "question", ts=0),
        _agent_turn("t0", "c0", "bash", {"command": "ls"}, "out", ts=1),
        _agent_text_msg("a1", "thinking...", ts=2),
    ]
    result = to_compact_messages_orm(msgs)
    # user + assistant(tool_calls) + tool + assistant(text) = 4
    assert len(result) == 4
    assert result[0].role == "user"
    assert result[1].role == "assistant"
    assert result[2].role == "tool"
    assert result[3].role == "assistant"
    assert result[1].tool_calls is not None
    assert result[3].tool_calls is None or len(result[3].tool_calls) == 0


# ─── run_compact_pipeline_unified stage=1 (mask) ────────────────────────────


def test_stage1_mask_replaces_old_tool_results():
    """Stage 1: old tool_results are replaced with mask markers."""
    big_content = "x" * 10000
    msgs = to_compact_messages_orm([
        _agent_turn("t0", "c0", "bash", {"command": "ls"}, big_content, ts=0),
        _agent_turn("t1", "c1", "bash", {"command": "pwd"}, big_content, ts=1),
        _agent_turn("t2", "c2", "bash", {"command": "echo"}, big_content, ts=2),
        _agent_turn("t3", "c3", "bash", {"command": "cat"}, "small", ts=3),
        _agent_turn("t4", "c4", "bash", {"command": "mv"}, "small", ts=4),
        _agent_turn("t5", "c5", "bash", {"command": "cp"}, "small", ts=5),
    ])
    result = run_compact_pipeline_unified(msgs, stage=1)
    # Each turn generates assistant + tool message; find tool messages
    tool_msgs = [m for m in result if m.role == "tool"]
    assert len(tool_msgs) == 6
    # Old turns (t0, t1, t2) should have masked tool results (KEEP_RECENT_TURNS=3)
    assert "masked" in tool_msgs[0].content
    assert "masked" in tool_msgs[1].content
    assert "masked" in tool_msgs[2].content
    # Recent turns (t3, t4, t5) should have original tool results
    assert "masked" not in tool_msgs[3].content
    assert "masked" not in tool_msgs[4].content
    assert "masked" not in tool_msgs[5].content


def test_stage1_mask_preserves_code_explore():
    """code_explore results are never masked."""
    big_content = "x" * 10000
    msgs = to_compact_messages_orm([
        _agent_turn("t0", "c0", "code_explore", {}, big_content, ts=0),
        _agent_turn("t1", "c1", "bash", {"command": "ls"}, big_content, ts=1),
        _agent_turn("t2", "c2", "bash", {"command": "pwd"}, big_content, ts=2),
        _agent_turn("t3", "c3", "bash", {"command": "echo"}, "small", ts=3),
        _agent_turn("t4", "c4", "bash", {"command": "cat"}, "small", ts=4),
        _agent_turn("t5", "c5", "bash", {"command": "mv"}, "small", ts=5),
    ])
    result = run_compact_pipeline_unified(msgs, stage=1)
    tool_msgs = [m for m in result if m.role == "tool"]
    # code_explore result preserved (t0 is first tool message)
    assert "masked" not in tool_msgs[0].content
    assert "x" * 100 in tool_msgs[0].content


def test_stage1_mask_preserves_fs_read_outline():
    """fs_read(mode=outline) results are never masked."""
    big_content = "x" * 10000
    msgs = to_compact_messages_orm([
        _agent_turn("t0", "c0", "fs_read", {"path": "src/app.ts", "mode": "outline"}, big_content, ts=0),
        _agent_turn("t1", "c1", "bash", {"command": "ls"}, big_content, ts=1),
        _agent_turn("t2", "c2", "bash", {"command": "pwd"}, big_content, ts=2),
        _agent_turn("t3", "c3", "bash", {"command": "echo"}, "small", ts=3),
        _agent_turn("t4", "c4", "bash", {"command": "cat"}, "small", ts=4),
        _agent_turn("t5", "c5", "bash", {"command": "mv"}, "small", ts=5),
    ])
    result = run_compact_pipeline_unified(msgs, stage=1)
    tool_msgs = [m for m in result if m.role == "tool"]
    assert "masked" not in tool_msgs[0].content


def test_stage1_mask_preserves_pinned():
    """Pinned messages are never masked."""
    big_content = "x" * 10000
    msgs = to_compact_messages_orm([
        _agent_turn("t0", "c0", "bash", {"command": "ls"}, big_content, ts=0),
        _agent_turn("t1", "c1", "bash", {"command": "pwd"}, big_content, ts=1),
        _agent_turn("t2", "c2", "bash", {"command": "echo"}, big_content, ts=2),
        _agent_turn("t3", "c3", "bash", {"command": "cat"}, "small", ts=3),
        _agent_turn("t4", "c4", "bash", {"command": "mv"}, "small", ts=4),
        _agent_turn("t5", "c5", "bash", {"command": "cp"}, "small", ts=5),
    ])
    # Mark t0 as pinned
    msgs[0].is_pinned = True
    result = run_compact_pipeline_unified(msgs, stage=1, pinned_ids={"t0"})
    tool_msgs = [m for m in result if m.role == "tool"]
    # Pinned t0 preserved
    assert "masked" not in tool_msgs[0].content
    # t1 is old and not pinned → masked
    assert "masked" in tool_msgs[1].content


# ─── run_compact_pipeline_unified stage=3 (fold) ───────────────────────────


def test_stage3_fold_reduces_message_count():
    """Stage 3: old turns are folded into a single marker message."""
    msgs = to_compact_messages_orm([
        _user_msg("u0", "question", ts=0),
        _agent_turn("t0", "c0", "bash", {"command": "ls"}, "out", ts=1),
        _agent_turn("t1", "c1", "fs_read", {"path": "a"}, "content", ts=2),
        _agent_turn("t2", "c2", "bash", {"command": "pwd"}, "out", ts=3),
        _agent_turn("t3", "c3", "fs_list", {"path": "src"}, "out", ts=4),
        _agent_turn("t4", "c4", "bash", {"command": "echo"}, "out", ts=5),
        _agent_turn("t5", "c5", "bash", {"command": "cat"}, "out", ts=6),
    ])
    original_count = len(msgs)
    result = run_compact_pipeline_unified(msgs, stage=3)
    # Fold should reduce message count
    assert len(result) < original_count


def test_stage3_fold_preserves_recent_turns():
    """Stage 3: recent turns are preserved after fold."""
    msgs = to_compact_messages_orm([
        _user_msg("u0", "question", ts=0),
        _agent_turn("t0", "c0", "bash", {"command": "ls"}, "out", ts=1),
        _agent_turn("t1", "c1", "bash", {"command": "pwd"}, "out", ts=2),
        _agent_turn("t2", "c2", "bash", {"command": "echo"}, "out", ts=3),
        _agent_turn("t3", "c3", "bash", {"command": "cat"}, "out", ts=4),
        _agent_turn("t4", "c4", "bash", {"command": "mv"}, "out", ts=5),
        _agent_turn("t5", "c5", "bash", {"command": "cp"}, "out", ts=6),
    ])
    result = run_compact_pipeline_unified(msgs, stage=3)
    # Recent tool messages should have their content preserved
    tool_msgs = [m for m in result if m.role == "tool"]
    # At least the last turn's tool result should be in the result
    assert any("out" in c for c in (m.content for m in tool_msgs if m.content))


# ─── from_compact_messages ───────────────────────────────────────────────────


def test_from_compact_messages_basic():
    """CompactMessage → OpenAI dict format."""
    cm = CompactMessage(
        id="t0",
        role="user",
        content="hello world",
    )
    result = from_compact_messages([cm])
    assert len(result) == 1
    assert result[0]["role"] == "user"
    assert "hello world" in result[0]["content"]


def test_from_compact_messages_with_tool_calls():
    """CompactMessage with tool_calls → assistant message with tool_calls."""
    cms = [
        CompactMessage(
            id="t0",
            role="assistant",
            content="Let me run that.",
            tool_calls=[{
                "id": "c0",
                "type": "function",
                "function": {"name": "bash", "arguments": '{"command": "ls"}'},
            }],
        ),
        CompactMessage(
            id="t0_tr_c0",
            role="tool",
            content="file1\nfile2",
            tool_call_id="c0",
        ),
    ]
    result = from_compact_messages(cms)
    assert len(result) == 2  # assistant + tool
    assert result[0]["role"] == "assistant"
    assert result[0].get("tool_calls") is not None
    assert result[1]["role"] == "tool"


# ─── Round-trip: ORM → CompactMessage → compact → dict ─────────────────────


def test_round_trip_orm_to_dict():
    """Full round-trip: ORM → CompactMessage → dict (no compaction)."""
    msgs = [
        _user_msg("u0", "do work", ts=0),
        _agent_turn("t0", "c0", "bash", {"command": "ls"}, "output", ts=1),
    ]
    compact = to_compact_messages_orm(msgs)
    dicts = from_compact_messages(compact)
    # user + assistant + tool = 3
    assert len(dicts) >= 3
    assert dicts[0]["role"] == "user"
    assert "do work" in dicts[0]["content"]
    assert dicts[1]["role"] == "assistant"


# ─── Regression — token estimation ───────────────────────────────────────────


def test_token_estimate_unchanged_after_refactor():
    """Regression: estimate_dict_message_tokens matches old logic."""
    content = "hello world!"
    name = "bash"
    arguments = '{"command":"ls"}'
    msg = {
        "role": "assistant",
        "content": content,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        ],
    }
    s = content + name + arguments
    old_expected = estimate_tokens(s) + 4
    new_result = estimate_dict_message_tokens(msg, include_reasoning=False)
    assert new_result == old_expected
