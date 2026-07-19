"""Tests for Tier 4 cross-run compaction (fix-tier4-cross-run-compaction change).

Covers:
- `_find_turn_boundaries_messages` / `_keep_recent_turns_messages`
- `prune_old_tool_results` (turn boundary + per-tool strategy + structured marker)
- `fold_old_messages` (turn boundary + structured fold marker + pinned protection)

Uses ``SimpleNamespace`` to mock DB Message objects — the compaction functions
only access ``.role``, ``.id``, ``.parts_list``, and ``.created_at``.
"""

from types import SimpleNamespace

from app.services.compact_markers import MAX_MARKER_CHARS
from app.services.conversation_context import (
    _find_turn_boundaries_messages,
    _keep_recent_turns_messages,
    fold_old_messages,
    prune_old_tool_results,
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
    """Agent message with only text parts (no tool_use) — not a turn."""
    return SimpleNamespace(
        id=msg_id,
        role="agent",
        agent_id="a1",
        parts_list=[{"type": "text", "content": content}],
        created_at=ts,
    )


# ─── 1. _find_turn_boundaries_messages ──────────────────────────────────────


def test_find_turn_boundaries_messages_basic():
    """4 agent messages with tool_use → 4 (start, end) tuples."""
    msgs = [
        _agent_turn(f"t{i}", f"c{i}", "bash", {"command": "ls"}, "out", ts=i)
        for i in range(4)
    ]
    boundaries = _find_turn_boundaries_messages(msgs)
    assert len(boundaries) == 4
    assert boundaries == [(0, 0), (1, 1), (2, 2), (3, 3)]


def test_find_turn_boundaries_messages_no_tool_use():
    """All text-only agent messages → empty list."""
    msgs = [
        _agent_text_msg("a1", "hello", ts=1),
        _user_msg("u1", "hi", ts=2),
        _agent_text_msg("a2", "world", ts=3),
    ]
    boundaries = _find_turn_boundaries_messages(msgs)
    assert boundaries == []


def test_find_turn_boundaries_messages_mixed():
    """Only agent messages with tool_use count as turns; user/text msgs skipped."""
    msgs = [
        _user_msg("u1", "question", ts=0),
        _agent_turn("t1", "c1", "bash", {"command": "ls"}, "out", ts=1),
        _agent_text_msg("a1", "thinking...", ts=2),
        _agent_turn("t2", "c2", "fs_read", {"path": "x"}, "content", ts=3),
    ]
    boundaries = _find_turn_boundaries_messages(msgs)
    assert len(boundaries) == 2
    assert boundaries == [(1, 1), (3, 3)]


# ─── 1. _keep_recent_turns_messages ─────────────────────────────────────────


def test_keep_recent_turns_messages_split():
    """6 turns, k=2 → recent has last 2 turns, old has first 4."""
    msgs = [_agent_turn(f"t{i}", f"c{i}", "bash", {"command": "ls"}, "out", ts=i) for i in range(6)]
    recent, old = _keep_recent_turns_messages(msgs, k=2)
    assert len(recent) == 2
    assert len(old) == 4
    assert recent[0].id == "t4"
    assert recent[1].id == "t5"
    assert old[0].id == "t0"


def test_keep_recent_turns_messages_few_turns():
    """When turns <= k, returns (messages, [])."""
    msgs = [_agent_turn(f"t{i}", f"c{i}", "bash", {}, "out", ts=i) for i in range(2)]
    recent, old = _keep_recent_turns_messages(msgs, k=2)
    assert len(recent) == 2
    assert old == []


# ─── 2. prune_old_tool_results ──────────────────────────────────────────────


def test_prune_uses_turn_boundary():
    """4 turns, keep_recent_turns=2 → last 2 turns preserved, first 2 pruned."""
    msgs = [
        _agent_turn(f"t{i}", f"c{i}", "bash", {"command": f"cmd{i}"}, f"output_{i}_long_enough", ts=i)
        for i in range(4)
    ]
    result = prune_old_tool_results(msgs, keep_recent_turns=2)

    # t0, t1 are old — tool_result replaced with marker
    assert not any(p.get("type") == "tool_result" for p in result[0].parts_list)
    assert not any(p.get("type") == "tool_result" for p in result[1].parts_list)

    # t2, t3 are recent — tool_result preserved
    assert any(p.get("type") == "tool_result" for p in result[2].parts_list)
    assert any(p.get("type") == "tool_result" for p in result[3].parts_list)


def test_prune_marker_has_recover_hint():
    """fs_list tool_result in old segment → marker includes recover hint with args."""
    fs_list_result = [{"name": "a.ts", "relativePath": "a.ts", "isDirectory": False}] * 10
    msgs = [
        _agent_turn("t0", "c0", "fs_list", {"path": "src", "depth": 3}, fs_list_result, ts=0),
        _agent_turn("t1", "c1", "bash", {"command": "ls"}, "out", ts=1),
        _agent_turn("t2", "c2", "bash", {"command": "pwd"}, "out", ts=2),
    ]
    result = prune_old_tool_results(msgs, keep_recent_turns=2)

    old_parts = result[0].parts_list
    marker = next(
        p["content"] for p in old_parts
        if p.get("type") == "text" and "compacted" in p.get("content", "")
    )
    assert "recover" in marker
    assert "fs_list" in marker
    assert "src" in marker
    assert "depth=3" in marker or "3" in marker


def test_prune_preserves_code_explore():
    """code_explore tool_result in old segment is never pruned."""
    code_explore_result = {"summary": "This module does X", "key_funcs": ["a", "b"]}
    msgs = [
        _agent_turn("t0", "c0", "code_explore", {}, code_explore_result, ts=0),
        _agent_turn("t1", "c1", "bash", {"command": "ls"}, "out", ts=1),
        _agent_turn("t2", "c2", "bash", {"command": "pwd"}, "out", ts=2),
    ]
    result = prune_old_tool_results(msgs, keep_recent_turns=2)

    # code_explore result preserved verbatim
    assert any(
        p.get("type") == "tool_result" for p in result[0].parts_list
    )


def test_prune_preserves_fs_read_outline():
    """fs_read(mode=outline) tool_result in old segment is never pruned."""
    msgs = [
        _agent_turn("t0", "c0", "fs_read", {"path": "src/app.ts", "mode": "outline"}, "outline_data", ts=0),
        _agent_turn("t1", "c1", "bash", {"command": "ls"}, "out", ts=1),
        _agent_turn("t2", "c2", "bash", {"command": "pwd"}, "out", ts=2),
    ]
    result = prune_old_tool_results(msgs, keep_recent_turns=2)

    assert any(
        p.get("type") == "tool_result" for p in result[0].parts_list
    )


def test_prune_marker_under_500_chars():
    """All generated markers must be ≤ 500 characters."""
    big_content = "x" * 50000
    msgs = [
        _agent_turn("t0", "c0", "bash", {"command": "ls -la"}, big_content, ts=0),
        _agent_turn("t0b", "c0b", "fs_list", {"path": "src", "depth": 3}, big_content, ts=0),
        _agent_turn("t1", "c1", "bash", {"command": "echo"}, "out", ts=1),
        _agent_turn("t2", "c2", "bash", {"command": "pwd"}, "out", ts=2),
    ]
    result = prune_old_tool_results(msgs, keep_recent_turns=2)

    for msg in result[:2]:  # old segment
        for p in msg.parts_list:
            if p.get("type") == "text" and "compacted" in p.get("content", ""):
                assert len(p["content"]) <= MAX_MARKER_CHARS


# ─── 3. fold_old_messages ───────────────────────────────────────────────────


def test_fold_uses_turn_boundary():
    """6 turns → fold marker + last 2 turns."""
    msgs = [
        _user_msg("u0", "first question", ts=0),
        _agent_turn("t0", "c0", "bash", {"command": "ls"}, "out", ts=1),
        _agent_turn("t1", "c1", "fs_read", {"path": "a"}, "content", ts=2),
        _agent_turn("t2", "c2", "bash", {"command": "pwd"}, "out", ts=3),
        _agent_turn("t3", "c3", "fs_list", {"path": "src"}, [], ts=4),
        _agent_turn("t4", "c4", "bash", {"command": "echo"}, "out", ts=5),
        _agent_turn("t5", "c5", "bash", {"command": "cat"}, "out", ts=6),
    ]
    result = fold_old_messages(msgs)

    # Should have: fold_marker + recent 2 turns (+ any user msgs in recent)
    fold_markers = [m for m in result if "folded" in str(m.id)]
    assert len(fold_markers) == 1

    # Last 2 turns (t4, t5) should be in the result
    ids = [m.id for m in result]
    assert "t4" in ids
    assert "t5" in ids
    # Old turns should be folded away
    assert "t0" not in ids
    assert "t1" not in ids


def test_fold_marker_has_tools_used():
    """Fold marker includes top 5 tools with counts."""
    msgs = [
        _agent_turn("t0", "c0", "fs_list", {"path": "src"}, [], ts=0),
        _agent_turn("t1", "c1", "fs_list", {"path": "lib"}, [], ts=1),
        _agent_turn("t2", "c2", "fs_read", {"path": "a"}, "x", ts=2),
        _agent_turn("t3", "c3", "fs_read", {"path": "b"}, "x", ts=3),
        _agent_turn("t4", "c4", "fs_read", {"path": "c"}, "x", ts=4),
        _agent_turn("t5", "c5", "fs_read", {"path": "d"}, "x", ts=5),
        _agent_turn("t6", "c6", "fs_read", {"path": "e"}, "x", ts=6),
        _agent_turn("t7", "c7", "bash", {"command": "ls"}, "x", ts=7),
        _agent_turn("t8", "c8", "bash", {"command": "pwd"}, "x", ts=8),
        _agent_turn("t9", "c9", "bash", {"command": "echo"}, "x", ts=9),
    ]
    result = fold_old_messages(msgs)

    fold_msg = next(m for m in result if "folded" in str(m.id))
    marker_text = fold_msg.parts_list[0]["content"]
    assert "fs_read" in marker_text
    assert "fs_list" in marker_text
    assert "bash" in marker_text
    # Check count format: tool×N
    assert "fs_read×5" in marker_text
    assert "fs_list×2" in marker_text


def test_fold_marker_has_summary():
    """Fold marker includes a summary field ≤ 200 chars."""
    msgs = [_user_msg("u0", "explore the codebase", ts=0)]
    msgs.extend(
        _agent_turn(f"t{i}", f"c{i}", "bash", {"command": "ls"}, "out", ts=i + 1)
        for i in range(6)
    )
    result = fold_old_messages(msgs)

    fold_msg = next(m for m in result if "folded" in str(m.id))
    marker_text = fold_msg.parts_list[0]["content"]
    assert "summary" in marker_text
    # Extract the summary content and check length
    summary_line = next(line for line in marker_text.split("\n") if "summary" in line)
    assert len(summary_line) <= 250  # generous bound for "[summary: ...]" wrapper


def test_fold_preserves_pinned():
    """Pinned messages in the old segment are preserved, not folded."""
    msgs = [_user_msg("u0", "pinned question", ts=0)]
    msgs.extend(
        _agent_turn(f"t{i}", f"c{i}", "bash", {"command": "ls"}, "out", ts=i + 1)
        for i in range(6)
    )
    result = fold_old_messages(msgs, pinned_ids={"u0"})

    ids = [m.id for m in result]
    assert "u0" in ids
    # Fold marker still present
    assert any("folded" in str(m.id) for m in result)


def test_fold_fallback_when_no_turns():
    """All text messages (no tool_use) → fallback to LEGACY_RECENT_KEEP."""
    msgs = [
        _user_msg(f"u{i}", f"message {i}", ts=i)
        for i in range(10)
    ]
    result = fold_old_messages(msgs)

    # Should keep last 6 (LEGACY_RECENT_KEEP) and fold the rest
    ids = [m.id for m in result]
    # Recent 6 preserved
    for i in range(4, 10):
        assert f"u{i}" in ids
    # Fold marker present
    assert any("folded" in str(m.id) for m in result)


def test_fold_no_op_when_turns_below_threshold():
    """3 turns (< FOLD_TURN_THRESHOLD=4) → no fold, returns original list."""
    msgs = [
        _agent_turn(f"t{i}", f"c{i}", "bash", {"command": "ls"}, "out", ts=i)
        for i in range(3)
    ]
    result = fold_old_messages(msgs)
    assert result is msgs or [m.id for m in result] == [m.id for m in msgs]


def test_fold_marker_under_500_chars():
    """Fold marker must not exceed MAX_MARKER_CHARS."""
    msgs = [_user_msg("u0", "x" * 200, ts=0)]
    msgs.extend(
        _agent_turn(f"t{i}", f"c{i}", "bash", {"command": "ls"}, "x" * 200, ts=i + 1)
        for i in range(8)
    )
    result = fold_old_messages(msgs)

    fold_msg = next(m for m in result if "folded" in str(m.id))
    marker_text = fold_msg.parts_list[0]["content"]
    assert len(marker_text) <= MAX_MARKER_CHARS


# ─── 4. Regression — Tier 4 token estimation refactor ─────────────────────────


def test_tier4_token_estimate_unchanged_after_refactor():
    """Regression: estimate_dict_message_tokens matches old _estimate_chat_message_tokens.

    The old Tier 4 function concatenated content + tool_calls.function fields
    into one string then estimated. The new shared function estimates
    per-field. For messages where field lengths don't create ceil rounding
    gaps, the results are identical.
    """
    # Field lengths chosen so per-field ceil sums == ceil of concatenated total:
    #   content (12 chars) → 3 tokens
    #   name (4 chars) → 1 token
    #   arguments (17 chars) → 5 tokens
    #   per-field: 3 + 1 + 5 + 4 = 13
    #   concatenated: 33 chars → 9 + 4 = 13
    content = "hello world!"  # 12 chars
    name = "bash"  # 4 chars
    arguments = '{"command":"ls"}'  # 17 chars
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

    # Old _estimate_chat_message_tokens logic (concatenated estimation)
    s = content + name + arguments
    old_expected = estimate_tokens(s) + 4

    # New shared function (per-field estimation, include_reasoning=False)
    new_result = estimate_dict_message_tokens(msg, include_reasoning=False)

    assert new_result == old_expected
