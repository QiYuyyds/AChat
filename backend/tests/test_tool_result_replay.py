"""Tests for tool_result cross-run replay (fix for field name mismatch bug).

The persisted tool_result part shape is ``{"type": "tool_result", "result": ...}``
(see ``persist_event`` in ``agent_runner.py``). Earlier code in
``conversation_context.py`` read ``content`` which never exists on tool_result
parts — these tests verify the fix reads ``result`` and correctly replays
tool_result content across runs.
"""

from types import SimpleNamespace

from app.services.conversation_context import (
    TOOL_RESULT_REPLAY_CHAR_CAP,
    _extract_tool_result_text,
    _render_agent_public_text,
    prune_old_tool_results,
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
    """Cross-run history now includes tool_result content (was silently dropped)."""
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


def test_render_truncates_long_result():
    long_text = "x" * (TOOL_RESULT_REPLAY_CHAR_CAP + 1000)
    parts = [
        {"type": "tool_result", "result": long_text, "isError": False},
    ]
    text = _render_agent_public_text(parts, {})
    assert "[truncated" in text
    assert len(text) < len(long_text)


def test_render_skips_empty_result():
    parts = [
        {"type": "tool_result", "result": None, "isError": False},
    ]
    text = _render_agent_public_text(parts, {})
    assert text == ""


# ─── prune_old_tool_results ──────────────────────────────────────────────────


def _agent_msg_with_tool(msg_id, call_id, tool_name, args, result, text=None):
    """Build a SimpleNamespace agent Message with tool_use + tool_result parts."""
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
    return SimpleNamespace(id=msg_id, role="agent", parts_list=parts)


def test_prune_reads_result_field():
    """prune_old_tool_results reads 'result' field and replaces with structured marker."""
    big_content = "x" * 10000
    msg_old = _agent_msg_with_tool(
        "msg_old", "c1", "bash", {"command": "ls"}, big_content,
    )
    msg_recent = _agent_msg_with_tool(
        "msg_recent", "c2", "bash", {"command": "pwd"}, "small",
    )
    # Need 3+ turns for prune to kick in with keep_recent_turns=2.
    msg_mid = _agent_msg_with_tool(
        "msg_mid", "c3", "bash", {"command": "echo"}, "mid",
    )
    result = prune_old_tool_results([msg_old, msg_mid, msg_recent], keep_recent_turns=2)

    old_parts = result[0].parts_list
    # The tool_result part should be replaced with a text marker.
    assert not any(p.get("type") == "tool_result" for p in old_parts)
    marker_text = next(p["content"] for p in old_parts if p.get("type") == "text" and "compacted" in p.get("content", ""))
    assert "compacted" in marker_text
    assert "recover" in marker_text

    # Recent turns preserved.
    assert any(p.get("type") == "tool_result" for p in result[2].parts_list)


def test_prune_keeps_small_result():
    """Small tool_results in recent turns are kept verbatim."""
    small_content = "x" * 100
    msg_old = _agent_msg_with_tool(
        "msg_old", "c1", "bash", {"command": "ls"}, small_content,
    )
    msg_mid = _agent_msg_with_tool(
        "msg_mid", "c2", "bash", {"command": "echo"}, "mid",
    )
    msg_recent = _agent_msg_with_tool(
        "msg_recent", "c3", "bash", {"command": "pwd"}, "recent",
    )
    result = prune_old_tool_results([msg_old, msg_mid, msg_recent], keep_recent_turns=2)

    # msg_mid and msg_recent are in the recent 2 turns — tool_result preserved.
    assert any(
        p.get("type") == "tool_result" and p.get("result") == "mid"
        for p in result[1].parts_list
    )
    assert any(
        p.get("type") == "tool_result" and p.get("result") == "recent"
        for p in result[2].parts_list
    )


# ─── tool_result replay differentiated truncation ──────────────────────────


def test_replay_code_explore_not_truncated():
    """code_explore results are high-density summaries — never truncated."""
    long_content = "x" * 8000
    parts = [
        {"type": "tool_use", "callId": "ce1", "toolName": "code_explore", "args": {}},
        {"type": "tool_result", "callId": "ce1", "result": long_content, "isError": False},
    ]
    text = _render_agent_public_text(parts, {})
    assert "[truncated" not in text
    assert len(text) >= 8000


def test_replay_fs_read_outline_not_truncated():
    """fs_read(mode=outline) results are short by construction — not truncated."""
    long_content = "x" * (TOOL_RESULT_REPLAY_CHAR_CAP + 1000)
    parts = [
        {"type": "tool_use", "callId": "fr1", "toolName": "fs_read", "args": {"path": "src/app.ts", "mode": "outline"}},
        {"type": "tool_result", "callId": "fr1", "result": long_content, "isError": False},
    ]
    text = _render_agent_public_text(parts, {})
    assert "[truncated" not in text


def test_replay_fs_read_head_not_truncated():
    """fs_read(mode=head) results are short by construction — not truncated."""
    long_content = "x" * (TOOL_RESULT_REPLAY_CHAR_CAP + 1000)
    parts = [
        {"type": "tool_use", "callId": "fr2", "toolName": "fs_read", "args": {"path": "src/app.ts", "mode": "head"}},
        {"type": "tool_result", "callId": "fr2", "result": long_content, "isError": False},
    ]
    text = _render_agent_public_text(parts, {})
    assert "[truncated" not in text


def test_replay_bash_truncated():
    """bash results above the cap are truncated with a suffix."""
    long_content = "x" * 6000
    parts = [
        {"type": "tool_use", "callId": "b1", "toolName": "bash", "args": {"command": "ls"}},
        {"type": "tool_result", "callId": "b1", "result": long_content, "isError": False},
    ]
    text = _render_agent_public_text(parts, {})
    assert "[truncated" in text
    assert "6000 chars total" in text
    assert len(text) < len(long_content)


def test_replay_unknown_tool_truncated():
    """tool_result without matching tool_use falls back to default truncation."""
    long_text = "x" * (TOOL_RESULT_REPLAY_CHAR_CAP + 1000)
    parts = [
        {"type": "tool_result", "result": long_text, "isError": False},
    ]
    text = _render_agent_public_text(parts, {})
    assert "[truncated" in text
    assert len(text) < len(long_text)
