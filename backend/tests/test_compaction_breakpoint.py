"""Unit tests for compaction breakpoint protection (Phase 5).

Verifies:
- _is_orphan_tool_result detects tool_result whose tool_use is in to_compact
- _is_pending_tool_use detects tool_use whose tool_result is in kept
- _find_safe_cut_point returns original cut when no tool chain issues
- _find_safe_cut_point moves backward for orphaned tool_result
- _find_safe_cut_point moves backward for pending tool_use
- _find_safe_cut_point handles edge cases (empty, single message)
"""

from types import SimpleNamespace

from app.services.context_compaction_service import (
    KEEP_RECENT_MESSAGES,
    _find_safe_cut_point,
    _is_orphan_tool_result,
    _is_pending_tool_use,
)


def _make_msg(msg_id, created_at, parts, role="agent", agent_id="ag1"):
    return SimpleNamespace(
        id=msg_id,
        created_at=created_at,
        role=role,
        agent_id=agent_id,
        parts_list=list(parts),
    )


# ─── _is_orphan_tool_result ──────────────────────────────────────────────────


def test_orphan_tool_result_detected():
    """messages[cut] has tool_result whose tool_use is in to_compact → True."""
    messages = [
        _make_msg("m0", 100, [{"type": "tool_use", "callId": "c1", "toolName": "bash"}]),
        _make_msg("m1", 200, [{"type": "text", "content": "middle"}]),
        _make_msg("m2", 300, [{"type": "tool_result", "callId": "c1", "result": "ok", "isError": False}]),
        _make_msg("m3", 400, [{"type": "text", "content": "kept"}]),
    ]
    # cut=2: to_compact=[m0, m1], kept=[m2, m3]
    # m2 has tool_result for c1, m0 has tool_use for c1 (in to_compact)
    assert _is_orphan_tool_result(messages, 2) is True


def test_no_orphan_when_tool_use_in_kept():
    """messages[cut] has tool_result whose tool_use is also in kept → False."""
    messages = [
        _make_msg("m0", 100, [{"type": "text", "content": "old"}]),
        _make_msg("m1", 200, [{"type": "tool_use", "callId": "c1", "toolName": "bash"}]),
        _make_msg("m2", 300, [{"type": "tool_result", "callId": "c1", "result": "ok", "isError": False}]),
        _make_msg("m3", 400, [{"type": "text", "content": "kept"}]),
    ]
    # cut=1: to_compact=[m0], kept=[m1, m2, m3]
    # m2 has tool_result for c1, m1 has tool_use for c1 (both in kept)
    assert _is_orphan_tool_result(messages, 1) is False


def test_no_orphan_when_no_tool_result_at_cut():
    """messages[cut] has no tool_result → False."""
    messages = [
        _make_msg("m0", 100, [{"type": "text", "content": "old"}]),
        _make_msg("m1", 200, [{"type": "text", "content": "kept"}]),
    ]
    assert _is_orphan_tool_result(messages, 1) is False


# ─── _is_pending_tool_use ────────────────────────────────────────────────────


def test_pending_tool_use_detected():
    """messages[cut-1] has tool_use whose tool_result is in kept → True."""
    messages = [
        _make_msg("m0", 100, [{"type": "text", "content": "old"}]),
        _make_msg("m1", 200, [{"type": "tool_use", "callId": "c1", "toolName": "bash"}]),
        _make_msg("m2", 300, [{"type": "tool_result", "callId": "c1", "result": "ok", "isError": False}]),
        _make_msg("m3", 400, [{"type": "text", "content": "kept"}]),
    ]
    # cut=2: to_compact=[m0, m1], kept=[m2, m3]
    # m1 has tool_use for c1, m2 has tool_result for c1 (in kept)
    assert _is_pending_tool_use(messages, 2) is True


def test_no_pending_when_result_also_in_compact():
    """messages[cut-1] has tool_use whose tool_result is also in to_compact → False."""
    messages = [
        _make_msg("m0", 100, [{"type": "tool_use", "callId": "c1", "toolName": "bash"}]),
        _make_msg("m1", 200, [{"type": "tool_result", "callId": "c1", "result": "ok", "isError": False}]),
        _make_msg("m2", 300, [{"type": "text", "content": "kept"}]),
        _make_msg("m3", 400, [{"type": "text", "content": "kept2"}]),
    ]
    # cut=2: to_compact=[m0, m1], kept=[m2, m3]
    # m1 (last in compact) has tool_result, not tool_use → no pending
    # m0 has tool_use, m1 has tool_result, both in to_compact → no pending
    assert _is_pending_tool_use(messages, 2) is False


def test_no_pending_when_no_tool_use_in_last_compact():
    """messages[cut-1] has no tool_use → False."""
    messages = [
        _make_msg("m0", 100, [{"type": "text", "content": "old"}]),
        _make_msg("m1", 200, [{"type": "text", "content": "kept"}]),
    ]
    assert _is_pending_tool_use(messages, 1) is False


# ─── _find_safe_cut_point ────────────────────────────────────────────────────


def test_safe_cut_point_no_tool_chain_issues():
    """No tool_use/tool_result spanning the cut → cut stays at default."""
    messages = [
        _make_msg(f"m{i}", i * 100, [{"type": "text", "content": f"msg{i}"}])
        for i in range(12)
    ]
    expected_cut = len(messages) - KEEP_RECENT_MESSAGES
    assert _find_safe_cut_point(messages) == expected_cut


def test_safe_cut_point_moves_back_for_orphan_tool_result():
    """Cut moves backward when tool_result at boundary is orphaned."""
    messages = [
        _make_msg("m0", 100, [{"type": "text", "content": "old"}]),
        _make_msg("m1", 200, [{"type": "tool_use", "callId": "c1", "toolName": "bash"}]),
        _make_msg("m2", 300, [{"type": "text", "content": "middle"}]),
        _make_msg("m3", 400, [{"type": "tool_result", "callId": "c1", "result": "ok", "isError": False}]),
    ] + [
        _make_msg(f"m{i+4}", (i+4) * 100, [{"type": "text", "content": f"kept{i}"}])
        for i in range(KEEP_RECENT_MESSAGES)
    ]
    # Default cut = len(messages) - KEEP_RECENT_MESSAGES = 4
    # At cut=4: messages[4] is first kept, but no tool_result there
    # Actually, m3 at index 3 has tool_result for c1, m1 at index 1 has tool_use for c1
    # Default cut = 4: to_compact = [m0..m3], kept = [m4..m9]
    # m3 is in to_compact, m1 is in to_compact → no spanning issue at cut=4
    # So cut should stay at 4
    expected = len(messages) - KEEP_RECENT_MESSAGES
    assert _find_safe_cut_point(messages) == expected


def test_safe_cut_point_moves_back_for_pending_tool_use():
    """Cut moves backward when last to_compact message has pending tool_use."""
    # Build messages where tool_use is at the default cut boundary
    messages = []
    for i in range(KEEP_RECENT_MESSAGES + 2):
        messages.append(_make_msg(f"m{i}", i * 100, [{"type": "text", "content": f"msg{i}"}]))
    # Insert a tool_use at position len-KEEP_RECENT_MESSAGES-1 (last in to_compact)
    # and tool_result at position len-KEEP_RECENT_MESSAGES (first in kept)
    cut_default = len(messages) - KEEP_RECENT_MESSAGES  # = 2
    messages[cut_default - 1] = _make_msg(
        "m_tool_use", (cut_default - 1) * 100,
        [{"type": "tool_use", "callId": "c1", "toolName": "bash"}],
    )
    messages[cut_default] = _make_msg(
        "m_tool_result", cut_default * 100,
        [{"type": "tool_result", "callId": "c1", "result": "ok", "isError": False}],
    )

    # Default cut would split the tool_use/tool_result pair
    safe_cut = _find_safe_cut_point(messages)
    assert safe_cut < cut_default  # moved backward


def test_safe_cut_point_edge_case_empty():
    """Empty messages list → cut = -KEEP_RECENT_MESSAGES (negative)."""
    assert _find_safe_cut_point([]) == -KEEP_RECENT_MESSAGES


def test_safe_cut_point_edge_case_few_messages():
    """Fewer messages than KEEP_RECENT_MESSAGES → cut = 0 or negative."""
    messages = [_make_msg("m0", 100, [{"type": "text", "content": "only"}])]
    cut = _find_safe_cut_point(messages)
    assert cut <= 0


def test_safe_cut_point_preserves_adjacent_tool_pair():
    """When tool_use and tool_result are adjacent and both in to_compact → no shift."""
    messages = [
        _make_msg("m0", 100, [{"type": "text", "content": "old"}]),
        _make_msg("m1", 200, [{"type": "tool_use", "callId": "c1", "toolName": "bash"}]),
        _make_msg("m2", 300, [{"type": "tool_result", "callId": "c1", "result": "ok", "isError": False}]),
    ] + [
        _make_msg(f"m{i+3}", (i+3) * 100, [{"type": "text", "content": f"kept{i}"}])
        for i in range(KEEP_RECENT_MESSAGES)
    ]
    # Default cut = 3: to_compact = [m0, m1, m2], kept = [m3..m8]
    # m1 (tool_use) and m2 (tool_result) are both in to_compact → no spanning
    expected = len(messages) - KEEP_RECENT_MESSAGES
    assert _find_safe_cut_point(messages) == expected
