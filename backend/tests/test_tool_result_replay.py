"""Tests for tool_result cross-run replay.

Verifies that ``_extract_tool_result_text`` reads the correct ``result`` field
(not ``content``) and that ``_render_agent_public_text`` replays tool_result
content in cross-run history.

After the universal mask refactor, there is no 4000-char hard truncation in
``_render_agent_public_text`` — mask-compacted content is already short, and
whitelisted content should not be truncated (design doc §8.5).
"""

from app.services.conversation_context import (
    _extract_tool_result_text,
    _render_agent_public_text,
)


# ─── _extract_tool_result_text ──────────────────────────────────────────────


def test_extract_string_result():
    part = {"type": "tool_result", "result": "file contents here"}
    assert _extract_tool_result_text(part) == "file contents here"


def test_extract_dict_result():
    part = {"type": "tool_result", "result": {"path": "src/app.ts", "lines": 42}}
    text = _extract_tool_result_text(part)
    assert '"path"' in text
    assert '"src/app.ts"' in text
    assert '"lines"' in text


def test_extract_list_result():
    part = {"type": "tool_result", "result": [{"name": "a"}, {"name": "b"}]}
    text = _extract_tool_result_text(part)
    assert '"name"' in text


def test_extract_none_result():
    assert _extract_tool_result_text({"type": "tool_result", "result": None}) == ""


def test_extract_missing_result_field():
    """Old code read 'content' which never exists — verify we read 'result'."""
    assert _extract_tool_result_text({"type": "tool_result", "callId": "c1"}) == ""


# ─── _render_agent_public_text ───────────────────────────────────────────────


def test_render_includes_tool_result_content():
    """Cross-run history includes tool_result content (was silently dropped)."""
    parts = [
        {"type": "text", "content": "Let me read the file."},
        {
            "type": "tool_result",
            "result": {"path": "src/app.ts", "lines": 42},
            "isError": False,
        },
    ]
    text = _render_agent_public_text(parts, {})
    assert "Let me read the file." in text
    assert "[tool_result]" in text
    assert "src/app.ts" in text


def test_render_marks_error_results():
    parts = [
        {"type": "tool_result", "result": "File not found", "isError": True},
    ]
    text = _render_agent_public_text(parts, {})
    assert "[tool_error]" in text
    assert "File not found" in text


def test_render_skips_empty_result():
    parts = [
        {"type": "tool_result", "result": None, "isError": False},
    ]
    text = _render_agent_public_text(parts, {})
    assert text == ""


def test_render_no_truncation_after_universal_mask():
    """After universal mask refactor, _render_agent_public_text does not truncate.

    Mask-compacted content is already short; whitelisted content (code_explore,
    fs_read outline/head) should not be truncated (design doc §8.5).
    """
    long_content = "x" * 8000
    parts = [
        {"type": "tool_use", "callId": "b1", "toolName": "bash", "args": {"command": "ls"}},
        {"type": "tool_result", "callId": "b1", "result": long_content, "isError": False},
    ]
    text = _render_agent_public_text(parts, {})
    assert "[truncated" not in text
    assert len(text) >= 8000
