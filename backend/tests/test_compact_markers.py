"""Unit tests for compact_markers.py — CompactMarkerBuilder + CompactSuccessJudge."""

from __future__ import annotations

from collections import Counter

from app.services.compact_markers import (
    EFFECTIVE_COMPACT_RATIO,
    MAX_MARKER_CHARS,
    MAX_SUMMARY_CHARS,
    CompactMarkerBuilder,
    CompactSuccessJudge,
)

# ─── CompactSuccessJudge ────────────────────────────────────────────────────


def test_judge_returns_true_when_token_drops_15_percent():
    """pre=100k, post=80k → 20% drop → True."""
    assert CompactSuccessJudge.judge(pre_tokens=100_000, post_tokens=80_000, pre_len=50, post_len=20) is True


def test_judge_returns_true_at_exactly_15_percent_boundary():
    """post == pre * 0.85 is NOT success (strict less-than)."""
    pre = 100_000
    post = int(pre * EFFECTIVE_COMPACT_RATIO)  # exactly 85% → not < 85%
    assert CompactSuccessJudge.judge(pre_tokens=pre, post_tokens=post, pre_len=50, post_len=20) is False
    # post = 84.9% → True
    assert CompactSuccessJudge.judge(pre_tokens=pre, post_tokens=post - 1, pre_len=50, post_len=20) is True


def test_judge_returns_false_when_only_len_changes():
    """Fold that reduces len but not tokens → False."""
    assert CompactSuccessJudge.judge(pre_tokens=100_000, post_tokens=98_000, pre_len=50, post_len=15) is False


def test_judge_returns_false_when_pre_is_zero():
    assert CompactSuccessJudge.judge(pre_tokens=0, post_tokens=0, pre_len=0, post_len=0) is False


# ─── CompactMarkerBuilder ───────────────────────────────────────────────────


def test_build_tool_result_marker_format():
    marker = CompactMarkerBuilder.build_tool_result_marker(
        stage=1,
        tool_name="fs_list",
        args={"path": "src", "depth": 3},
        summary="src/ 下 5 文件、3 子目录",
        recover_hint="fs_list(path='src', depth=3) 重新获取结构",
    )
    assert "[compacted stage=1 tool=fs_list" in marker
    assert "path='src'" in marker
    assert "depth=3" in marker
    assert "[summary:" in marker
    assert "[recover:" in marker
    assert "fs_list(path='src', depth=3)" in marker
    assert len(marker) <= MAX_MARKER_CHARS


def test_build_fold_marker_lists_top_5_tools():
    counts = Counter({"fs_read": 10, "fs_list": 5, "bash": 3, "fs_grep": 2, "code_explore": 1, "fs_write": 8})
    marker = CompactMarkerBuilder.build_fold_marker(
        stage=3,
        turns_folded=3,
        tools_used_counts=counts,
        summary="explored src and backend",
    )
    assert "[folded stage=3 turns=3" in marker
    # top 5 by count: fs_read×10, fs_write×8, fs_list×5, bash×3, fs_grep×2
    assert "fs_read×10" in marker
    assert "fs_write×8" in marker
    # code_explore is 6th → not shown
    assert "code_explore" not in marker
    assert "[summary:" in marker
    assert len(marker) <= MAX_MARKER_CHARS


def test_marker_length_capped():
    """Construct an oversized summary → marker ≤ MAX_MARKER_CHARS."""
    long_summary = "x" * 1000
    marker = CompactMarkerBuilder.build_tool_result_marker(
        stage=2,
        tool_name="fs_read",
        args={"path": "very/long/path/that/goes/on/and/on/here.py"},
        summary=long_summary,
        recover_hint="x" * 500,
    )
    assert len(marker) <= MAX_MARKER_CHARS, f"marker len={len(marker)} > {MAX_MARKER_CHARS}"


def test_fold_marker_length_capped():
    counts = Counter({"tool_with_a_very_long_name": 999})
    marker = CompactMarkerBuilder.build_fold_marker(
        stage=3,
        turns_folded=99,
        tools_used_counts=counts,
        summary="x" * 1000,
        first_user_msg_head="y" * 500,
        last_assistant_text_head="z" * 500,
    )
    assert len(marker) <= MAX_MARKER_CHARS


def test_summary_field_capped():
    """Summary field within marker is ≤ MAX_SUMMARY_CHARS."""
    long_summary = "a" * 1000
    marker = CompactMarkerBuilder.build_fold_marker(
        stage=3,
        turns_folded=1,
        tools_used_counts=Counter(),
        summary=long_summary,
    )
    # extract the summary field (strip "[summary: " prefix and trailing "]")
    lines = marker.split("\n")
    summary_line = next((ln for ln in lines if ln.startswith("[summary:")), "")
    content = summary_line[len("[summary: "):]
    if content.endswith("]"):
        content = content[:-1]
    assert len(content) <= MAX_SUMMARY_CHARS
