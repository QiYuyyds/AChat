"""Tests for Session Note whitelist extraction and mask preservation.

Covers:
  - _extract_whitelist parses paths from files_touched entries
  - _stage1_mask_unified preserves whitelisted fs_read results
  - _extract_whitelist returns None when note is absent or empty
"""

from __future__ import annotations

import json

from app.memory.session_note import SessionNote
from app.services.compact_pipeline import (
    KEEP_RECENT_TURNS,
    CompactMessage,
    _stage1_mask_unified,
)
from app.services.conversation_context import _extract_whitelist


def test_extract_whitelist_parses_paths():
    note = SessionNote(
        title="test",
        files_touched=[
            "app.py (已读, 100 行)",
            "lib.py (已改, 50 行)",
            "config.yml",
        ],
    )
    result = _extract_whitelist(note)
    assert result is not None
    assert result == {"app.py", "lib.py", "config.yml"}


def test_extract_whitelist_none_when_note_absent():
    assert _extract_whitelist(None) is None


def test_extract_whitelist_none_when_files_touched_empty():
    note = SessionNote(title="test", files_touched=[])
    assert _extract_whitelist(note) is None


def _make_turn(turn_idx: int, tool_name: str, path: str, content: str) -> list[CompactMessage]:
    """Build one complete turn: assistant + tool result."""
    call_id = f"call_{turn_idx}"
    assistant = CompactMessage(
        id=f"asst_{turn_idx}",
        role="assistant",
        content=f"Turn {turn_idx}",
        tool_calls=[{
            "id": call_id,
            "type": "function",
            "function": {
                "name": tool_name,
                "arguments": json.dumps({"path": path}, ensure_ascii=False),
            },
        }],
    )
    tool = CompactMessage(
        id=f"tool_{turn_idx}",
        role="tool",
        content=content,
        tool_call_id=call_id,
    )
    return [assistant, tool]


def _make_messages_with_turns(
    n_turns: int,
    tool_name: str = "fs_read",
    path: str = "app.py",
) -> list[CompactMessage]:
    """Build messages with system + user + n_turns complete turns."""
    messages: list[CompactMessage] = [
        CompactMessage(id="sys", role="system", content="system prompt"),
        CompactMessage(id="user", role="user", content="explore"),
    ]
    for t in range(n_turns):
        content = "x" * 500
        messages.extend(_make_turn(t, tool_name, path, content))
    return messages


def test_mask_preserves_whitelist_files():
    """fs_read results for files in the whitelist are preserved verbatim."""
    n_turns = KEEP_RECENT_TURNS + 4
    messages = _make_messages_with_turns(n_turns, "fs_read", "app.py")
    whitelist = {"app.py"}

    result = _stage1_mask_unified(messages, note_whitelist=whitelist)

    old_tool_msgs = [m for m in result if m.role == "tool"]
    masked = [m for m in old_tool_msgs if m.content.startswith("[masked")]

    assert len(masked) == 0, (
        f"no tool results should be masked when all are whitelisted, got {len(masked)}"
    )


def test_mask_preserves_only_whitelisted_files():
    """Only files in the whitelist are preserved; others are masked."""
    n_old = KEEP_RECENT_TURNS + 4
    messages: list[CompactMessage] = [
        CompactMessage(id="sys", role="system", content="system prompt"),
        CompactMessage(id="user", role="user", content="explore"),
    ]
    for t in range(n_old):
        path = "app.py" if t % 2 == 0 else "other.py"
        messages.extend(_make_turn(t, "fs_read", path, "x" * 500))

    whitelist = {"app.py"}
    result = _stage1_mask_unified(messages, note_whitelist=whitelist)

    tool_msgs = [m for m in result if m.role == "tool"]
    masked = [m for m in tool_msgs if m.content.startswith("[masked")]
    preserved = [m for m in tool_msgs if not m.content.startswith("[masked")]

    n_old_turns = n_old - KEEP_RECENT_TURNS
    assert len(preserved) == n_old_turns // 2 + KEEP_RECENT_TURNS, (
        f"expected preserved count mismatch: preserved={len(preserved)}"
    )
    assert len(masked) == n_old_turns // 2, (
        f"expected masked count mismatch: masked={len(masked)}"
    )


def test_no_whitelist_when_note_absent():
    """When note_whitelist is None, all fs_read(full) results in old are masked."""
    n_turns = KEEP_RECENT_TURNS + 4
    messages = _make_messages_with_turns(n_turns, "fs_read", "app.py")

    result = _stage1_mask_unified(messages, note_whitelist=None)

    old_tool_msgs = [m for m in result if m.role == "tool"]
    masked = [m for m in old_tool_msgs if m.content.startswith("[masked")]
    preserved = [m for m in old_tool_msgs if not m.content.startswith("[masked")]

    n_old_turns = n_turns - KEEP_RECENT_TURNS
    assert len(masked) == n_old_turns, (
        f"all {n_old_turns} old turns should be masked, got {len(masked)}"
    )
    assert len(preserved) == KEEP_RECENT_TURNS, (
        f"recent {KEEP_RECENT_TURNS} turns should be preserved, got {len(preserved)}"
    )
