"""Unit tests for Custom ReAct loop termination state machine."""

from __future__ import annotations

from app.services.react_loop_termination import (
    COMPACT_FOLD_RATIO,
    COMPACT_MASK_RATIO,
    COMPACT_RATIO,
    HARD_RATIO,
    SOFT_RATIO,
    StopReason,
    TerminationState,
    decide_pre_model,
    format_child_stop_prefix,
    should_compact,
    stable_tool_fingerprint,
    stop_reason_label,
)


def test_stable_fingerprint_sorted_and_path_normalize():
    a = stable_tool_fingerprint("fs_read", {"path": r"C:\ws\foo\bar.txt", "b": 1, "a": 2})
    b = stable_tool_fingerprint("fs_read", {"a": 2, "b": 1, "path": "/ws/foo/bar.txt"})
    assert a == b


def test_stable_fingerprint_excludes_volatile():
    a = stable_tool_fingerprint("x", {"path": "/a", "timestamp": 1, "uuid": "u1"})
    b = stable_tool_fingerprint("x", {"path": "/a", "timestamp": 99, "uuid": "u2"})
    assert a == b


def test_stop_reason_label_complete_is_empty():
    assert stop_reason_label(StopReason.COMPLETE) is None
    assert stop_reason_label(StopReason.BUDGET_FORCED_FINAL)
    assert "总结" in stop_reason_label(StopReason.BUDGET_FORCED_FINAL)


def test_format_child_stop_prefix():
    assert format_child_stop_prefix(StopReason.COMPLETE, "ok") == "ok"
    out = format_child_stop_prefix(StopReason.BUDGET_FORCED_FINAL, "summary")
    assert out.startswith("[stopped: budget_forced_final]")
    assert "summary" in out


def test_decide_continue_when_under_budget():
    state = TerminationState()
    d = decide_pre_model(state=state, total_tokens=100, model_limit=1000)
    assert d.action == "continue"


def test_decide_compact_band():
    """Legacy single-point compact only fires when pipeline is disabled."""
    state = TerminationState()
    tokens = int(COMPACT_RATIO * 1000) + 1
    d = decide_pre_model(state=state, total_tokens=tokens, model_limit=1000, pipeline_enabled=False)
    assert d.action == "compact"


def test_decide_pre_model_mask_at_0_75():
    """ratio=0.76 → action='mask' (stage 1) when pipeline enabled."""
    state = TerminationState()
    tokens = 760  # ratio = 0.76
    d = decide_pre_model(state=state, total_tokens=tokens, model_limit=1000)
    assert d.action == "mask"


def test_decide_pre_model_fold_at_0_88():
    """ratio=0.89 → action='fold' (stage 3), not legacy 'compact'."""
    state = TerminationState()
    tokens = 890  # ratio = 0.89
    d = decide_pre_model(state=state, total_tokens=tokens, model_limit=1000)
    assert d.action == "fold"


def test_decide_pre_model_legacy_when_disabled():
    """When pipeline_enabled=False, ratio=0.86 returns legacy 'compact'."""
    state = TerminationState()
    tokens = 860  # ratio = 0.86 >= COMPACT_RATIO (0.85)
    d = decide_pre_model(state=state, total_tokens=tokens, model_limit=1000, pipeline_enabled=False)
    assert d.action == "compact"


def test_decide_soft_band():
    state = TerminationState()
    tokens = int(SOFT_RATIO * 1000) + 1
    d = decide_pre_model(state=state, total_tokens=tokens, model_limit=1000)
    assert d.action == "soft_inject"
    assert state.soft_done is True
    assert d.inject_message


def test_decide_hard_still_gets_soft_then_forced():
    state = TerminationState()
    tokens = int(HARD_RATIO * 1000) + 10
    d1 = decide_pre_model(state=state, total_tokens=tokens, model_limit=1000)
    assert d1.action == "soft_inject"
    # soft ignored → force flag
    state.force_after_soft = True
    d2 = decide_pre_model(state=state, total_tokens=tokens, model_limit=1000)
    assert d2.action == "force_final"
    state.forced_done = True
    d3 = decide_pre_model(state=state, total_tokens=tokens, model_limit=1000)
    assert d3.action == "hard_stop"


def test_max_tool_turns_fuse():
    state = TerminationState(max_tool_turns=3, tool_turn_count=3)
    d = decide_pre_model(state=state, total_tokens=10, model_limit=1000)
    assert d.action == "soft_inject"
    assert state.soft_trigger_reason == StopReason.MAX_TOOL_TURNS


def test_duplicate_breaker_inject_then_force():
    state = TerminationState()
    state.consecutive_fingerprint_count = 3
    state.last_fingerprint = "fs_read|{}"
    d1 = decide_pre_model(state=state, total_tokens=10, model_limit=1000)
    assert d1.action == "soft_inject"
    assert state.soft_done
    state.force_after_duplicate = True
    d2 = decide_pre_model(state=state, total_tokens=10, model_limit=1000)
    assert d2.action == "force_final"
    assert d2.pending_reason == StopReason.DUPLICATE_TOOL_BREAKER


def test_tool_error_breaker():
    state = TerminationState()
    state.consecutive_tool_errors = 3
    state.last_error_tool = "bash"
    d1 = decide_pre_model(state=state, total_tokens=10, model_limit=1000)
    assert d1.action == "soft_inject"
    state.force_after_tool_error = True
    d2 = decide_pre_model(state=state, total_tokens=10, model_limit=1000)
    assert d2.action == "force_final"
    assert d2.pending_reason == StopReason.TOOL_ERROR_BREAKER


def test_record_tool_calls_fingerprint_streak():
    state = TerminationState()
    fp = stable_tool_fingerprint("fs_list", {"path": ""})
    state.record_tool_calls(["fs_list"], [fp], [False])
    state.record_tool_calls(["fs_list"], [fp], [False])
    state.record_tool_calls(["fs_list"], [fp], [False])
    assert state.consecutive_fingerprint_count == 3
    assert state.tool_turn_count == 3
    assert state.turns_since_compact == 3


# ─── should_compact anti-oscillation tests ──────────────────────────────────


def test_should_compact_anti_oscillation():
    """Verify the 17-turn anti-oscillation scenario.

    Turn 1-4: ratio rises to 0.76 → mask triggers at turn 4.
    Turn 5-7: ratio stays ~0.76 but turns_since_compact < 4 → skip.
    Turn 8: turns_since_compact == 4, ratio=0.77 → mask again.
    Turn 9-10: ratio rises to 0.88 → direct upgrade to fold.
    Turn 11-17: after fold, no more compaction regardless of ratio.
    """
    state = TerminationState()

    # Turns 1-3: ratio below threshold → None
    for _ in range(3):
        state.turns_since_compact += 1
        assert should_compact(state, 0.60) is None

    # Turn 4: ratio hits 0.76 → mask
    state.turns_since_compact += 1
    assert should_compact(state, 0.76) == "mask"
    # Execute mask: update state
    state.last_compact_stage = 1
    state.turns_since_compact = 0

    # Turns 5-7: turns_since_compact = 1, 2, 3 (< 4) → skip mask
    for t in range(3):
        state.turns_since_compact += 1
        assert should_compact(state, 0.76) is None, f"turn {t+5} should skip mask"

    # Turn 8: turns_since_compact == 4, ratio=0.77 → mask
    state.turns_since_compact += 1
    assert should_compact(state, 0.77) == "mask"
    state.last_compact_stage = 1
    state.turns_since_compact = 0

    # Turn 9: ratio=0.86, still within interval → None
    state.turns_since_compact += 1
    assert should_compact(state, 0.86) is None

    # Turn 10: ratio=0.88, direct upgrade → fold
    state.turns_since_compact += 1
    assert should_compact(state, 0.88) == "fold"
    state.last_compact_stage = 3
    state.turns_since_compact = 0

    # Turns 11-17: after fold, no compaction
    for t in range(7):
        state.turns_since_compact += 1
        assert should_compact(state, 0.90) is None, f"turn {t+11} should not compact after fold"


def test_should_compact_direct_upgrade():
    """After mask, ratio ≥ 0.88 → direct fold (skip re-mask)."""
    state = TerminationState()
    state.last_compact_stage = 1
    state.turns_since_compact = 1  # very recent mask

    # Ratio ≥ 0.88 should trigger fold even within minimum interval
    assert should_compact(state, 0.88) == "fold"
    assert should_compact(state, 0.95) == "fold"

    # Ratio between mask and fold thresholds, within interval → None
    state.turns_since_compact = 1
    assert should_compact(state, 0.76) is None

    # Ratio between mask and fold thresholds, outside interval → mask
    state.turns_since_compact = 5
    assert should_compact(state, 0.76) == "mask"


def test_should_compact_no_compaction_after_fold():
    """After fold, no compaction regardless of ratio."""
    state = TerminationState()
    state.last_compact_stage = 3
    state.turns_since_compact = 0

    assert should_compact(state, 0.76) is None
    assert should_compact(state, 0.88) is None
    assert should_compact(state, 0.95) is None
