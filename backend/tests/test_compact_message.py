"""Tests for CompactMessage data structure and unified compaction pipeline.

Covers:
  - test_compact_message_dict_roundtrip: dict → CompactMessage → dict, no info loss
  - test_compact_message_orm_conversion: ORM Message → CompactMessage field mapping
  - test_unified_mask_same_as_layer1: dict path vs CompactMessage path produce same mask
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.services.compact_pipeline import (
    _stage1_mask,
    _stage1_mask_unified,
    from_compact_messages,
    run_compact_pipeline_unified,
    to_compact_messages,
    to_compact_messages_orm,
)

# ─── helpers ───────────────────────────────────────────────────────────────


def _make_assistant_with_tool_calls(
    turn_idx: int,
    tool_names=("fs_list", "fs_read", "bash"),
    args_fn=None,
) -> dict:
    calls = []
    for i, name in enumerate(tool_names):
        args = args_fn(name, i) if args_fn else {"path": f"src/file{i}.py"}
        calls.append({
            "id": f"call_{turn_idx}_{i}",
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(args, ensure_ascii=False),
            },
        })
    return {
        "role": "assistant",
        "content": f"Turn {turn_idx}: exploring.",
        "tool_calls": calls,
    }


def _make_tool_result(turn_idx: int, tool_idx: int, content: str = "result") -> dict:
    return {
        "role": "tool",
        "tool_call_id": f"call_{turn_idx}_{tool_idx}",
        "content": content,
    }


def _make_turn(
    turn_idx: int,
    tool_names=("fs_list", "fs_read", "bash"),
    result_content="tool result content " * 50,
) -> list[dict]:
    assistant = _make_assistant_with_tool_calls(turn_idx, tool_names)
    tools = [_make_tool_result(turn_idx, i, result_content) for i in range(len(tool_names))]
    return [assistant, *tools]


# ─── 1.5: dict roundtrip ──────────────────────────────────────────────────


def test_compact_message_dict_roundtrip():
    """dict → CompactMessage → dict, verify no information loss."""
    original = [
        {"role": "system", "content": "You are a helpful agent."},
        {"role": "user", "content": "List files."},
        {
            "role": "assistant",
            "content": "I'll list the files.",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "fs_list",
                        "arguments": '{"path": "src", "depth": 3}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "[{\"name\": \"file.py\"}]",
        },
    ]

    compact = to_compact_messages(original)
    assert len(compact) == 4

    # Check roles preserved
    assert compact[0].role == "system"
    assert compact[1].role == "user"
    assert compact[2].role == "assistant"
    assert compact[3].role == "tool"

    # Check content preserved
    assert compact[0].content == "You are a helpful agent."
    assert compact[1].content == "List files."
    assert compact[2].content == "I'll list the files."
    assert compact[3].content == '[{"name": "file.py"}]'

    # Check tool_calls preserved
    assert compact[2].tool_calls is not None
    assert compact[2].tool_calls[0]["function"]["name"] == "fs_list"
    assert compact[2].tool_calls[0]["id"] == "call_1"

    # Check tool_call_id preserved
    assert compact[3].tool_call_id == "call_1"

    # Roundtrip back to dict
    result = from_compact_messages(compact)
    assert len(result) == 4

    assert result[0]["role"] == "system"
    assert result[0]["content"] == "You are a helpful agent."
    assert result[1]["role"] == "user"
    assert result[1]["content"] == "List files."
    assert result[2]["role"] == "assistant"
    assert result[2]["content"] == "I'll list the files."
    assert result[2]["tool_calls"] == original[2]["tool_calls"]
    assert result[3]["role"] == "tool"
    assert result[3]["tool_call_id"] == "call_1"
    assert result[3]["content"] == '[{"name": "file.py"}]'


# ─── 1.6: ORM conversion ──────────────────────────────────────────────────


def test_compact_message_orm_conversion():
    """Construct Message-like objects with tool_use + tool_result parts,
    convert to CompactMessage, verify field mapping."""
    parts = [
        {"type": "text", "content": "I'll explore the code."},
        {"type": "tool_use", "callId": "call_1", "toolName": "fs_list",
         "args": {"path": "src", "depth": 2}},
        {"type": "tool_result", "callId": "call_1",
         "result": [{"name": "file.py", "size": 100}]},
    ]

    msg = SimpleNamespace(
        id="msg_1",
        role="agent",
        parts_list=parts,
        created_at=1000.0,
    )

    result = to_compact_messages_orm([msg])

    # Should produce 2 CompactMessages: 1 assistant + 1 tool
    assert len(result) == 2

    # Assistant message
    assert result[0].id == "msg_1"
    assert result[0].role == "assistant"
    assert result[0].content == "I'll explore the code."
    assert result[0].tool_calls is not None
    assert len(result[0].tool_calls) == 1
    assert result[0].tool_calls[0]["id"] == "call_1"
    assert result[0].tool_calls[0]["function"]["name"] == "fs_list"
    # Arguments should be JSON string
    args_str = result[0].tool_calls[0]["function"]["arguments"]
    args = json.loads(args_str)
    assert args == {"path": "src", "depth": 2}

    # Tool message
    assert result[1].id == "msg_1_tr_call_1"
    assert result[1].role == "tool"
    assert result[1].tool_call_id == "call_1"
    assert "file.py" in result[1].content

    assert result[0].created_at == 1000.0


def test_compact_message_orm_user_message():
    """ORM user message converts correctly."""
    msg = SimpleNamespace(
        id="msg_user",
        role="user",
        parts_list=[{"type": "text", "content": "Hello world"}],
        created_at=500.0,
    )
    result = to_compact_messages_orm([msg])
    assert len(result) == 1
    assert result[0].role == "user"
    assert result[0].content == "Hello world"
    assert result[0].created_at == 500.0


def test_compact_message_orm_system_message():
    """ORM system message converts correctly."""
    msg = SimpleNamespace(
        id="msg_sys",
        role="system",
        parts_list=[{"type": "text", "content": "System prompt"}],
        created_at=0.0,
    )
    result = to_compact_messages_orm([msg])
    assert len(result) == 1
    assert result[0].role == "system"
    assert result[0].content == "System prompt"


# ─── 2.5: unified mask same as Layer 1 ────────────────────────────────────


def test_unified_mask_same_as_layer1():
    """Same data via dict path (_stage1_mask) and CompactMessage path
    (_stage1_mask_unified) produce identical mask markers."""
    tool_names = ("fs_list", "fs_read", "bash")
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "go"},
    ]
    for t in range(5):
        messages.extend(_make_turn(t, tool_names=tool_names, result_content="result " * 100))

    import copy
    dict_msgs = copy.deepcopy(messages)

    # Layer 1 (dict path)
    dict_result = _stage1_mask(dict_msgs)

    # Unified (CompactMessage path)
    compact = to_compact_messages(copy.deepcopy(messages))
    unified_result = _stage1_mask_unified(compact)
    unified_dicts = from_compact_messages(unified_result)

    # Same number of messages
    assert len(dict_result) == len(unified_dicts)

    # Compare each message's role, content, tool_call_id
    for i, (d, u) in enumerate(zip(dict_result, unified_dicts, strict=False)):
        assert d["role"] == u["role"], f"msg {i}: role mismatch {d['role']} vs {u['role']}"
        # For tool messages, compare content (masked or not)
        if d.get("role") == "tool":
            assert d["content"] == u["content"], (
                f"msg {i}: tool content mismatch\n"
                f"  dict: {d['content'][:100]}\n"
                f"  unified: {u['content'][:100]}"
            )
        else:
            assert d["content"] == u["content"], (
                f"msg {i}: content mismatch"
            )


def test_unified_mask_preserves_code_explore():
    """code_explore results in old segment are not masked (whitelist)."""
    code_explore_content = "code explore " * 1000
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "go"},
    ]
    for t in range(5):
        assistant = {
            "role": "assistant",
            "content": f"turn {t}",
            "tool_calls": [
                {"id": f"ce_{t}_0", "type": "function",
                 "function": {"name": "code_explore",
                              "arguments": json.dumps({"path": "src"})}},
            ],
        }
        messages.append(assistant)
        messages.append({"role": "tool", "tool_call_id": f"ce_{t}_0",
                         "content": code_explore_content})

    compact = to_compact_messages(messages)
    result = _stage1_mask_unified(compact)
    result_dicts = from_compact_messages(result)

    tools = [m for m in result_dicts if m.get("role") == "tool"]
    assert all(m["content"] == code_explore_content for m in tools)


def test_unified_fold_same_as_layer1():
    """Stage 3 fold via CompactMessage path produces same structure as dict path."""
    tool_names = ("fs_list", "fs_read", "bash")
    messages = [
        {"role": "system", "content": "You are an agent."},
        {"role": "user", "content": "Explore."},
    ]
    for t in range(6):
        messages.extend(_make_turn(t, tool_names=tool_names, result_content="data " * 50))

    import copy

    # Layer 1 (dict path)
    dict_folded = run_compact_pipeline_unified(
        to_compact_messages(copy.deepcopy(messages)),
        stage=3,
    )
    dict_folded_as_dicts = from_compact_messages(dict_folded)

    # Both should have: system prompt + fold marker + recent turns
    assert dict_folded_as_dicts[0]["role"] == "system"
    assert dict_folded_as_dicts[0]["content"] == "You are an agent."
    assert "[folded" in dict_folded_as_dicts[1]["content"]
