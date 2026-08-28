"""Unit tests for compact_markers.py — CompactMarkerBuilder."""

from __future__ import annotations

from collections import Counter

from app.services.compact_markers import (
    MAX_MARKER_CHARS,
    MAX_SUMMARY_CHARS,
    CompactMarkerBuilder,
)

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
