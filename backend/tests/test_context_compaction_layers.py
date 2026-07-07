"""Unit tests for O1 context compaction layers (tool_result pruning + message folding).

Verifies:
- Old large tool_result parts are pruned to a marker
- Old small tool_result parts are preserved
- Recent N turns of tool_results are kept in full
- Message folding triggers when count exceeds threshold
- Pinned messages are never folded
- Fold marker includes time range
"""

from types import SimpleNamespace

from app.services.conversation_context import fold_old_messages, prune_old_tool_results


def _make_msg(msg_id: str, created_at: int, parts: list[dict], role: str = "agent") -> SimpleNamespace:
    return SimpleNamespace(
        id=msg_id,
        created_at=created_at,
        role=role,
        agent_id="ag_test",
        parts_list=list(parts),
    )


# ─── prune_old_tool_results ──────────────────────────────────────────────────


def test_old_large_tool_result_is_pruned():
    """tool_result exceeding threshold in an old message is replaced with a marker."""
    big_content = "x" * 10_000  # ~2500 tokens, above default 2000 threshold
    messages = [
        _make_msg("m1", 100, [{"type": "tool_result", "content": big_content}]),
        _make_msg("m2", 200, [{"type": "text", "content": "recent"}]),
        _make_msg("m3", 300, [{"type": "text", "content": "recent2"}]),
        _make_msg("m4", 400, [{"type": "text", "content": "recent3"}]),
    ]

    prune_old_tool_results(messages, recent_turns=3)

    # m1 is old (beyond last 3), its tool_result should be replaced
    assert messages[0].parts_list[0]["type"] == "text"
    assert "已裁剪" in messages[0].parts_list[0]["content"]
    assert "m1" in messages[0].parts_list[0]["content"]


def test_old_small_tool_result_is_preserved():
    """tool_result below threshold in an old message remains unchanged."""
    small_content = "small result"  # well below 2000 tokens
    messages = [
        _make_msg("m1", 100, [{"type": "tool_result", "content": small_content}]),
        _make_msg("m2", 200, [{"type": "text", "content": "recent"}]),
        _make_msg("m3", 300, [{"type": "text", "content": "recent2"}]),
        _make_msg("m4", 400, [{"type": "text", "content": "recent3"}]),
    ]

    prune_old_tool_results(messages, recent_turns=3)

    assert messages[0].parts_list[0]["type"] == "tool_result"
    assert messages[0].parts_list[0]["content"] == small_content


def test_recent_tool_results_preserved_in_full():
    """tool_results within recent_turns are never pruned, even if large."""
    big_content = "x" * 10_000
    messages = [
        _make_msg("m1", 100, [{"type": "text", "content": "old"}]),
        _make_msg("m2", 200, [{"type": "tool_result", "content": big_content}]),
        _make_msg("m3", 300, [{"type": "tool_result", "content": big_content}]),
        _make_msg("m4", 400, [{"type": "tool_result", "content": big_content}]),
    ]

    prune_old_tool_results(messages, recent_turns=3)

    # Last 3 messages (m2, m3, m4) should have their tool_results intact
    for msg in messages[1:]:
        assert msg.parts_list[0]["type"] == "tool_result"
        assert msg.parts_list[0]["content"] == big_content


def test_prune_noop_when_fewer_messages_than_recent_turns():
    """When message count <= recent_turns, no pruning occurs."""
    big_content = "x" * 10_000
    messages = [
        _make_msg("m1", 100, [{"type": "tool_result", "content": big_content}]),
        _make_msg("m2", 200, [{"type": "tool_result", "content": big_content}]),
    ]

    prune_old_tool_results(messages, recent_turns=3)

    for msg in messages:
        assert msg.parts_list[0]["type"] == "tool_result"
        assert msg.parts_list[0]["content"] == big_content


# ─── fold_old_messages ────────────────────────────────────────────────────────


def test_fold_triggers_when_count_exceeds_threshold():
    """Messages beyond threshold are folded into a single marker."""
    messages = [_make_msg(f"m{i}", i * 100, [{"type": "text", "content": f"msg{i}"}]) for i in range(35)]

    result = fold_old_messages(messages, fold_threshold=30, keep_recent=20)

    # Should have: fold_marker + 20 recent = 21 messages
    assert len(result) == 21
    fold_marker = result[0]
    assert fold_marker.id == "folded_15"
    assert "已折叠" in fold_marker.parts_list[0]["content"]
    assert "15" in fold_marker.parts_list[0]["content"]


def test_fold_marker_includes_time_range():
    """Fold marker includes the time range of folded messages."""
    messages = [_make_msg(f"m{i}", i * 100, [{"type": "text", "content": f"msg{i}"}]) for i in range(35)]

    result = fold_old_messages(messages, fold_threshold=30, keep_recent=20)

    fold_marker = result[0]
    content = fold_marker.parts_list[0]["content"]
    # First folded message is m0 (created_at=0), last is m14 (created_at=1400)
    assert "0" in content
    assert "1400" in content


def test_pinned_messages_not_folded():
    """Pinned messages are kept even when they're in the old range."""
    messages = [_make_msg(f"m{i}", i * 100, [{"type": "text", "content": f"msg{i}"}]) for i in range(35)]

    result = fold_old_messages(
        messages,
        fold_threshold=30,
        keep_recent=20,
        pinned_ids={"m5"},
    )

    # m5 should be in the result (not folded)
    ids = [m.id for m in result]
    assert "m5" in ids
    # Fold marker should still be present
    assert any(m.id == "folded_14" for m in result)


def test_fold_noop_when_below_threshold():
    """When message count is at or below threshold, no folding occurs."""
    messages = [_make_msg(f"m{i}", i * 100, [{"type": "text", "content": f"msg{i}"}]) for i in range(30)]

    result = fold_old_messages(messages, fold_threshold=30, keep_recent=20)

    assert result is messages
    assert len(result) == 30
