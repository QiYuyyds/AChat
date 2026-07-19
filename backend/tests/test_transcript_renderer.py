"""Unit tests for transcript_renderer — shared tool-aware transcript renderer.

Covers:
  - render_tool_aware_transcript: tool_use + tool_result rendering, thinking skip,
    fs_list compression, code_explore verbatim preservation
  - estimate_full_message_tokens: includes tool parts (not just text)
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.services.transcript_renderer import (
    estimate_dict_message_tokens,
    estimate_full_message_tokens,
    render_tool_aware_transcript,
)


def _make_msg(msg_id, created_at, parts, role="agent", agent_id="ag1"):
    """Create a lightweight message mock matching the Message interface."""
    return SimpleNamespace(
        id=msg_id,
        created_at=created_at,
        role=role,
        agent_id=agent_id,
        parts_list=list(parts),
    )


# ─── render_tool_aware_transcript ────────────────────────────────────────────


def test_render_tool_aware_transcript_includes_tool_use():
    """Agent message with tool_use + tool_result renders both in transcript."""
    msg = _make_msg("m1", 100, [
        {"type": "text", "content": "让我看看项目结构"},
        {"type": "tool_use", "callId": "c1", "toolName": "fs_list", "args": {"path": "src", "depth": 3}},
        {"type": "tool_result", "callId": "c1", "result": [{"name": "index.ts", "relativePath": "src/index.ts", "isDirectory": False}], "isError": False},
    ])
    transcript = render_tool_aware_transcript([msg])
    assert "↳ tool_use: fs_list(" in transcript
    assert "↳ tool_result: [fs_list]" in transcript
    assert "让我看看项目结构" in transcript


def test_render_user_message_as_single_line():
    """User message renders as ``用户：<text>`` without tool lines."""
    msg = _make_msg("m1", 100, [
        {"type": "text", "content": "分析一下这个项目"}
    ], role="user", agent_id=None)
    transcript = render_tool_aware_transcript([msg])
    assert transcript == "用户：分析一下这个项目"
    assert "↳" not in transcript


def test_render_system_message_as_single_line():
    """System message renders as ``系统：<text>``."""
    msg = _make_msg("m1", 100, [
        {"type": "text", "content": "系统提示"}
    ], role="system", agent_id=None)
    transcript = render_tool_aware_transcript([msg])
    assert transcript == "系统：系统提示"


def test_render_skips_thinking_parts():
    """Thinking parts are not rendered in the transcript."""
    msg = _make_msg("m1", 100, [
        {"type": "thinking", "content": "Let me think about this deeply..."},
        {"type": "text", "content": "Here is my answer."},
    ])
    transcript = render_tool_aware_transcript([msg])
    assert "Let me think about this deeply" not in transcript
    assert "Here is my answer." in transcript


def test_render_skips_empty_messages():
    """Messages with no text and no tool parts are skipped."""
    msg = _make_msg("m1", 100, [
        {"type": "thinking", "content": "only thinking, no text or tools"},
    ])
    transcript = render_tool_aware_transcript([msg])
    assert transcript == ""


def test_render_compresses_fs_list_result():
    """fs_list tool_result with 500 entries is compressed to < 30% original."""
    # Include large fields (preview) that get dropped by stage=1 summarizer,
    # which only keeps name + relativePath.
    entries = [
        {
            "name": f"file{i}.ts",
            "relativePath": f"src/file{i}.ts",
            "size": 1024,
            "depth": 2,
            "isDirectory": False,
            "preview": "x" * 200,
        }
        for i in range(500)
    ]
    original_content = json.dumps(entries, ensure_ascii=False)
    msg = _make_msg("m1", 100, [
        {"type": "tool_use", "callId": "c1", "toolName": "fs_list", "args": {"path": "src", "depth": 3}},
        {"type": "tool_result", "callId": "c1", "result": entries, "isError": False},
    ])
    transcript = render_tool_aware_transcript([msg])
    # Find the tool_result line
    tool_result_line = [line for line in transcript.split("\n") if "tool_result:" in line][0]
    # The tool_result line should be much shorter than the original content
    assert len(tool_result_line) < len(original_content) * 0.30


def test_render_preserves_code_explore_verbatim():
    """code_explore tool_result is preserved verbatim in transcript."""
    code_explore_content = "Architecture: The project uses a layered architecture with 3 layers. Key findings: entry point at src/index.ts, config at src/config.ts."
    msg = _make_msg("m1", 100, [
        {"type": "tool_use", "callId": "c1", "toolName": "code_explore", "args": {"query": "architecture"}},
        {"type": "tool_result", "callId": "c1", "result": code_explore_content, "isError": False},
    ])
    transcript = render_tool_aware_transcript([msg])
    assert code_explore_content in transcript


def test_render_tool_use_args_truncated():
    """tool_use args JSON is truncated when exceeding 200 chars."""
    long_args = {"path": "x" * 300}
    msg = _make_msg("m1", 100, [
        {"type": "tool_use", "callId": "c1", "toolName": "fs_read", "args": long_args},
    ])
    transcript = render_tool_aware_transcript([msg])
    tool_use_line = [line for line in transcript.split("\n") if "tool_use:" in line][0]
    # The args part (after the opening paren) should be truncated
    # Line format: "  ↳ tool_use: fs_read({\"path\": \"xxx...\"})"
    assert "…" in tool_use_line


def test_render_tool_result_without_matching_tool_use():
    """tool_result without matching tool_use uses 'unknown' as tool_name."""
    msg = _make_msg("m1", 100, [
        {"type": "text", "content": "result without matching use"},
        {"type": "tool_result", "callId": "orphan", "result": "some result", "isError": False},
    ])
    transcript = render_tool_aware_transcript([msg])
    assert "[unknown]" in transcript


def test_render_agent_names_mapping():
    """agent_names dict maps agent_id to display name."""
    msg = _make_msg("m1", 100, [
        {"type": "text", "content": "hello"}
    ], agent_id="ag_123")
    transcript = render_tool_aware_transcript([msg], agent_names={"ag_123": "Claude"})
    assert transcript.startswith("Claude：")


# ─── estimate_full_message_tokens ────────────────────────────────────────────


def test_estimate_full_message_tokens_includes_tool_parts():
    """Message with text + large tool_result estimates >> text-only."""
    text_content = "x" * 2000  # ~500 tokens
    tool_result_content = "y" * 200000  # ~50000 tokens
    msg = _make_msg("m1", 100, [
        {"type": "text", "content": text_content},
        {"type": "tool_result", "callId": "c1", "result": tool_result_content, "isError": False},
    ])
    total = estimate_full_message_tokens([msg])
    # Should be ~50500, way more than 500
    assert total > 10000
    # And significantly more than just the text portion
    from app.utils.model_registry import estimate_tokens
    text_only = estimate_tokens(text_content)
    assert total > text_only * 10


def test_estimate_full_message_tokens_counts_tool_use_args():
    """tool_use args are counted in the token estimate."""
    args = {"path": "src/very/long/path/to/some/file.ts", "mode": "full"}
    msg = _make_msg("m1", 100, [
        {"type": "tool_use", "callId": "c1", "toolName": "fs_read", "args": args},
    ])
    total = estimate_full_message_tokens([msg])
    assert total > 0


def test_estimate_full_message_tokens_counts_thinking():
    """thinking parts are counted in the token estimate."""
    msg = _make_msg("m1", 100, [
        {"type": "thinking", "content": "x" * 4000},  # ~1000 tokens
    ])
    total = estimate_full_message_tokens([msg])
    assert total >= 1000


def test_estimate_full_message_tokens_empty():
    """Empty message list → 0 tokens."""
    assert estimate_full_message_tokens([]) == 0


def test_estimate_full_message_tokens_dict_result():
    """tool_result with dict result is counted correctly."""
    msg = _make_msg("m1", 100, [
        {"type": "tool_result", "callId": "c1", "result": {"key": "x" * 1000}, "isError": False},
    ])
    total = estimate_full_message_tokens([msg])
    assert total > 100


# ─── estimate_dict_message_tokens ──────────────────────────────────────────────


def test_estimate_dict_message_tokens_basic():
    """content + tool_calls: estimate = content + name + arguments + 4 overhead."""
    from app.utils.model_registry import estimate_tokens

    content = "Please list the files in the src directory."
    name = "fs_list"
    arguments = '{"path": "src", "depth": 3}'
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
    result = estimate_dict_message_tokens(msg, include_reasoning=False)
    expected = (
        estimate_tokens(content)
        + estimate_tokens(name)
        + estimate_tokens(arguments)
        + 4
    )
    assert result == expected


def test_estimate_dict_message_tokens_excludes_metadata():
    """role, tool_call_id, type fields are NOT counted."""
    from app.utils.model_registry import estimate_tokens

    content = "hello world"
    msg = {
        "role": "assistant",
        "content": content,
        "tool_call_id": "call_abc123def456",
        "type": "function",
    }
    result = estimate_dict_message_tokens(msg)
    # Only content + 4 overhead; role/tool_call_id/type excluded
    assert result == estimate_tokens(content) + 4


def test_estimate_dict_message_tokens_include_reasoning():
    """include_reasoning=True counts reasoning_content; False does not."""
    from app.utils.model_registry import estimate_tokens

    reasoning = "Let me think about this problem step by step."
    msg = {
        "role": "assistant",
        "content": "Here is my answer.",
        "reasoning_content": reasoning,
    }
    without = estimate_dict_message_tokens(msg, include_reasoning=False)
    with_reasoning = estimate_dict_message_tokens(msg, include_reasoning=True)

    # Without reasoning: only content + 4
    assert without == estimate_tokens("Here is my answer.") + 4
    # With reasoning: content + reasoning + 4
    assert with_reasoning == without + estimate_tokens(reasoning)
    assert with_reasoning > without


def test_estimate_dict_message_tokens_list_content():
    """When content is a list (vision parts), only type=text parts are counted."""
    from app.utils.model_registry import estimate_tokens

    msg = {
        "role": "user",
        "content": [
            {"type": "text", "text": "What is in this image?"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},
            {"type": "text", "text": "Please describe."},
        ],
    }
    result = estimate_dict_message_tokens(msg)
    text1 = "What is in this image?"
    text2 = "Please describe."
    expected = estimate_tokens(text1) + estimate_tokens(text2) + 4
    assert result == expected
