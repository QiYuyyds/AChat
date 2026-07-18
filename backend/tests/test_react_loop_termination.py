"""Unit tests for Custom ReAct loop termination state machine."""

from __future__ import annotations

from app.services.react_loop_termination import (
    COMPACT_RATIO,
    HARD_RATIO,
    SOFT_RATIO,
    StopReason,
    TerminationState,
    decide_pre_model,
    format_child_stop_prefix,
    mark_compact_result,
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
    state = TerminationState()
    tokens = int(COMPACT_RATIO * 1000) + 1
    d = decide_pre_model(state=state, total_tokens=tokens, model_limit=1000)
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


def test_compact_failure_breaker():
    state = TerminationState()
    mark_compact_result(state, success=False)
    mark_compact_result(state, success=False)
    mark_compact_result(state, success=False)
    assert state.compact_disabled is True
    d = decide_pre_model(state=state, total_tokens=10, model_limit=1000)
    assert d.action == "soft_inject"
    assert state.soft_trigger_reason == StopReason.COMPACT_FAILURE_BREAKER


def test_record_tool_calls_fingerprint_streak():
    state = TerminationState()
    fp = stable_tool_fingerprint("fs_list", {"path": ""})
    state.record_tool_calls(["fs_list"], [fp], [False])
    state.record_tool_calls(["fs_list"], [fp], [False])
    state.record_tool_calls(["fs_list"], [fp], [False])
    assert state.consecutive_fingerprint_count == 3
    assert state.tool_turn_count == 3
