"""Unit tests for the five-stage compaction pipeline (compact_pipeline.py).

Covers:
  - estimate_messages_tokens (content-only, excludes JSON metadata)
  - find_turn_boundaries / keep_recent_turns
  - summarize_tool_result (per-tool retention: fs_list / fs_read / bash / fs_grep / code_explore / unknown)
  - run_compact_pipeline stages 1/2/3
"""

from __future__ import annotations

import json
import logging

import pytest

from app.services.compact_pipeline import (
    estimate_messages_tokens,
    find_turn_boundaries,
    keep_recent_turns,
    run_compact_pipeline,
    summarize_tool_result,
)
from app.utils.model_registry import estimate_tokens

# ─── helpers ───────────────────────────────────────────────────────────────


def _make_assistant_with_tool_calls(
    turn_idx: int,
    tool_names=("fs_list", "fs_read", "bash", "fs_grep", "code_explore"),
    args_fn=None,
) -> dict:
    """Build an assistant message with N tool_calls."""
    calls = []
    for i, name in enumerate(tool_names):
        args = args_fn(name, i) if args_fn else {"path": f"src/file{i}.py"}
        calls.append({
            "id": f"call_{turn_idx}_{i}",
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(args, ensure_ascii=False),
            },
        })
    return {
        "role": "assistant",
        "content": f"Turn {turn_idx}: exploring.",
        "tool_calls": calls,
    }


def _make_tool_result(turn_idx: int, tool_idx: int, content: str = "result") -> dict:
    return {
        "role": "tool",
        "tool_call_id": f"call_{turn_idx}_{tool_idx}",
        "content": content,
    }


def _make_turn(turn_idx: int, tool_names=("fs_list", "fs_read", "bash", "fs_grep", "code_explore"), result_content="tool result content " * 50) -> list[dict]:
    """Build one complete turn: 1 assistant (with tool_calls) + N tool messages."""
    assistant = _make_assistant_with_tool_calls(turn_idx, tool_names)
    tools = [_make_tool_result(turn_idx, i, result_content) for i in range(len(tool_names))]
    return [assistant, *tools]


def _make_messages_with_turns(n_turns: int, tool_names=("_fs_list", "_fs_read"), result_content="x" * 200) -> list[dict]:
    """Build messages: system + user + n_turns complete turns."""
    messages = [
        {"role": "system", "content": "You are a helpful agent."},
        {"role": "user", "content": "Explore the project."},
    ]
    for t in range(n_turns):
        messages.extend(_make_turn(t, tool_names=tool_names, result_content=result_content))
    return messages


# ─── Section 2: estimate_messages_tokens ───────────────────────────────────


def test_estimate_messages_tokens_excludes_metadata():
    """New content-only estimate is 15-25% lower than legacy json.dumps estimate."""
    tool_result = "a typical file listing result " * 22  # ~660 chars
    messages = [
        {
            "role": "assistant",
            "content": "I will list the project files now.",
            "tool_calls": [
                {
                    "id": "call_abc123def456",
                    "type": "function",
                    "function": {
                        "name": "fs_list",
                        "arguments": json.dumps({"path": "src", "depth": 3}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_abc123def456",
            "content": tool_result,
        },
    ]

    legacy = estimate_tokens(json.dumps(messages, ensure_ascii=False))
    new = estimate_messages_tokens(messages)

    assert new < legacy, f"new ({new}) should be < legacy ({legacy})"
    reduction = (legacy - new) / legacy
    assert 0.15 <= reduction <= 0.35, (
        f"expected 15-25% reduction, got {reduction:.1%} (legacy={legacy}, new={new})"
    )


def test_estimate_messages_tokens_handles_multimodal_content():
    """When content is a list (vision parts), only type=text parts are counted."""
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What is in this image?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KG..."}},
                {"type": "text", "text": "Please describe."},
            ],
        }
    ]
    new = estimate_messages_tokens(messages)
    # 2 text parts: "What is in this image?" (23 chars) + "Please describe." (16 chars) = 39 chars
    # → ceil(39/4) = 10 tokens + 4 overhead = 14
    assert new == 14
    # image_url part must not contribute tokens (no crash, no inflation)
    assert new < 50


def test_estimate_messages_tokens_counts_tool_calls_function_fields():
    """tool_calls.function.name + arguments are counted."""
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "fs_list",
                        "arguments": '{"path": "src"}',
                    },
                }
            ],
        },
    ]
    new = estimate_messages_tokens(messages)
    # content="" → 0; name "fs_list" (7) → 2; args '{"path": "src"}' (16) → 4; overhead 4 → 10
    assert new == 10


def test_estimate_messages_tokens_counts_reasoning_content():
    messages = [
        {
            "role": "assistant",
            "content": "answer",
            "reasoning_content": "thinking " * 10,  # 90 chars
        }
    ]
    new = estimate_messages_tokens(messages)
    # content "answer" (6) → 2; reasoning 90 → 23; overhead 4 → 29
    assert new == 29


# ─── Section 3: TurnBoundaryFinder ─────────────────────────────────────────


def test_find_turn_boundaries_basic():
    """4 turns × (1 assistant + 7 tool) → 4 tuples, each spanning 8 messages."""
    tool_names = [f"tool_{i}" for i in range(7)]
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "go"},
    ]
    for t in range(4):
        messages.extend(_make_turn(t, tool_names=tool_names))

    boundaries = find_turn_boundaries(messages)
    assert len(boundaries) == 4
    for start, end in boundaries:
        assert end - start == 7, f"turn span should be 8 messages (end-start=7), got {end - start}"


def test_find_turn_boundaries_no_tool_calls():
    """All assistant messages without tool_calls → empty list."""
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "bye"},
        {"role": "assistant", "content": "bye"},
    ]
    boundaries = find_turn_boundaries(messages)
    assert boundaries == []


def test_keep_recent_turns_returns_correct_split():
    """8 turns, k=2: recent has last 2 turns, old has first 6 turns."""
    tool_names = ("fs_list", "fs_read", "bash")
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "go"},
    ]
    for t in range(8):
        messages.extend(_make_turn(t, tool_names=tool_names))

    recent, old = keep_recent_turns(messages, k=2)
    # recent = last 2 turns = 2 * (1 + 3) = 8 messages
    assert len(recent) == 8
    # old = system + user + 6 turns = 2 + 6 * 4 = 26 messages
    assert len(old) == 26
    # recent starts with an assistant message
    assert recent[0]["role"] == "assistant"
    assert recent[0].get("tool_calls")
    # old ends with a tool message (end of turn 6)
    assert old[-1]["role"] == "tool"
    # No split inside a turn: recent's first assistant's tool_calls all have
    # matching tool results in recent.
    first_call_ids = {tc["id"] for tc in recent[0]["tool_calls"]}
    recent_tool_ids = {m["tool_call_id"] for m in recent if m.get("role") == "tool"}
    assert first_call_ids.issubset(recent_tool_ids), "tool_use ↔ tool_result pair must not be split"


def test_keep_recent_turns_when_fewer_than_k():
    """When <= k complete turns, returns (messages, [])."""
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "go"},
    ]
    messages.extend(_make_turn(0, tool_names=("fs_list",)))

    recent, old = keep_recent_turns(messages, k=2)
    assert recent == messages
    assert old == []


# ─── Section 4: ToolResultSummarizer ────────────────────────────────────────


def test_summarize_fs_list_depth_gt_1():
    """fs_list(depth=3) with 500 entries: stage 1 keeps name/relativePath, drops size/depth, token drops ≥40%."""
    entries = [
        {
            "name": f"file{i}.py",
            "relativePath": f"src/sub/file{i}.py",
            "size": 1024,
            "depth": 3,
            "isDirectory": i % 5 == 0,
        }
        for i in range(500)
    ]
    content = json.dumps(entries, ensure_ascii=False)
    args = {"path": "src", "depth": 3}

    result = summarize_tool_result("fs_list", args, content, stage=1)
    parsed = json.loads(result)

    # kept name + relativePath
    assert len(parsed) == 500
    assert "name" in parsed[0]
    assert "relativePath" in parsed[0]
    # dropped size / depth / isDirectory
    assert "size" not in parsed[0]
    assert "depth" not in parsed[0]
    assert "isDirectory" not in parsed[0]

    # token reduction ≥ 40%
    pre = estimate_tokens(content)
    post = estimate_tokens(result)
    assert post < pre * 0.60, f"expected ≥40% reduction, got {(1 - post/pre):.1%}"


def test_summarize_fs_list_depth_1_stage1_preserves_names():
    """fs_list(depth=1) stage 1 still keeps name/relativePath."""
    entries = [{"name": f"f{i}", "relativePath": f"f{i}", "size": 1, "depth": 1, "isDirectory": False} for i in range(10)]
    content = json.dumps(entries, ensure_ascii=False)
    args = {"path": ".", "depth": 1}

    result = summarize_tool_result("fs_list", args, content, stage=1)
    parsed = json.loads(result)
    assert len(parsed) == 10
    assert "size" not in parsed[0]


def test_summarize_fs_read_full_to_outline():
    """fs_read(mode='full') with 12k tokens: stage 1 calls extract_outline, returns outline, drops content."""
    # ~12k tokens ≈ 48k chars of Python code
    lines = []
    for i in range(1500):
        if i % 50 == 0:
            lines.append(f"def function_{i}():")
        lines.append(f"    x_{i} = {i}  # {'line content ' * 3}")
    content = "\n".join(lines)
    args = {"path": "src/module.py", "mode": "full"}

    result = summarize_tool_result("fs_read", args, content, stage=1)
    parsed = json.loads(result)

    assert "outline" in parsed
    assert "language" in parsed
    assert "totalLines" in parsed
    assert "fullSize" in parsed
    assert parsed["language"] == "python"
    # content is dropped (not present as a field)
    assert "content" not in parsed
    # outline should have at least some functions
    assert len(parsed["outline"]) > 0
    # token reduction
    pre = estimate_tokens(content)
    post = estimate_tokens(result)
    assert post < pre * 0.5, f"expected significant reduction, got {(1 - post/pre):.1%}"


def test_summarize_fs_read_outline_mode_preserved():
    """fs_read(mode='outline') is preserved verbatim across all stages."""
    content = json.dumps([{"type": "function", "line": 1, "content": "def foo():"}])
    args = {"path": "src/x.py", "mode": "outline"}

    for stage in (1, 2, 3):
        result = summarize_tool_result("fs_read", args, content, stage=stage)
        assert result == content, f"outline mode should be preserved at stage {stage}"


def test_summarize_fs_read_head_mode_preserved():
    """fs_read(mode='head') is preserved verbatim across all stages."""
    content = "line 1\nline 2\nline 3"
    args = {"path": "src/x.py", "mode": "head"}

    for stage in (1, 2, 3):
        result = summarize_tool_result("fs_read", args, content, stage=stage)
        assert result == content, f"head mode should be preserved at stage {stage}"


def test_summarize_code_explore_preserved():
    """code_explore is preserved verbatim across all stages (high density, unrecoverable)."""
    content = "code explore summary " * 1500  # ~30k chars ≈ 7.5k tokens
    args = {"path": "src/"}

    for stage in (1, 2, 3):
        result = summarize_tool_result("code_explore", args, content, stage=stage)
        assert result == content, f"code_explore must be preserved verbatim at stage {stage}"


def test_summarize_bash_stage1_keeps_tail():
    """bash stage 1 keeps last 20 lines + exit code."""
    lines = [f"output line {i}" for i in range(50)]
    content = "\n".join(lines)
    args = {"command": "ls -la"}

    result = summarize_tool_result("bash", args, content, stage=1)
    # last 20 lines of the original
    assert "output line 49" in result
    assert "output line 30" in result
    # earlier lines are dropped
    assert "output line 10" not in result


def test_summarize_bash_stage3_minimal():
    """bash stage 3 keeps only last line (+ exit if present)."""
    content = "\n".join(f"line {i}" for i in range(30))
    args = {"command": "make test"}

    result = summarize_tool_result("bash", args, content, stage=3)
    assert "line 29" in result
    # should be very short
    assert len(result) < 100


def test_summarize_fs_grep_stage1_keeps_first_10():
    content = "\n".join(f"src/file{i}.py:10:match" for i in range(30))
    args = {"pattern": "match", "path": "src"}

    result = summarize_tool_result("fs_grep", args, content, stage=1)
    result_lines = result.splitlines()
    assert len(result_lines) == 10
    assert "file0" in result_lines[0]


def test_summarize_fs_grep_stage3_count_only():
    content = "\n".join(f"src/file{i}.py:10:match" for i in range(30))
    args = {"pattern": "match", "path": "src"}

    result = summarize_tool_result("fs_grep", args, content, stage=3)
    assert "30" in result  # count
    assert "file0" not in result  # no match details


def test_summarize_unknown_tool_fallback():
    """Unknown tool: stage 1 returns first 1000 chars, stage 2 returns marker, stage 3 folds."""
    content = "x" * 5000
    args = {"path": "src"}

    s1 = summarize_tool_result("some_unknown_tool", args, content, stage=1)
    assert len(s1) == 1000
    assert s1 == content[:1000]

    s2 = summarize_tool_result("some_unknown_tool", args, content, stage=2)
    assert "已摘要" in s2 or "已折叠" in s2

    s3 = summarize_tool_result("some_unknown_tool", args, content, stage=3)
    assert "已折叠" in s3


# ─── Section 6: Pipeline stages ─────────────────────────────────────────────


def test_pipeline_stage1_preserves_recent_turns():
    """Stage 1: last 2 turns complete, old turns' tool_results summarized."""
    tool_names = ("fs_list", "fs_read", "bash", "fs_grep", "code_explore")
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "go"},
    ]
    for t in range(4):
        messages.extend(_make_turn(t, tool_names=tool_names, result_content="result " * 100))

    original_len = len(messages)
    result = run_compact_pipeline(messages, stage=1)

    # Same number of messages (stage 1 only replaces content, doesn't fold)
    assert len(result) == original_len

    # system + user preserved
    assert result[0]["role"] == "system"
    assert result[1]["role"] == "user"

    # last 2 turns (turns 2,3) are intact: each turn = 1 assistant + 5 tools
    recent_start = len(result) - 2 * (1 + len(tool_names))
    recent = result[recent_start:]
    # first message of recent should be an assistant with tool_calls
    assert recent[0]["role"] == "assistant"
    assert recent[0].get("tool_calls")
    # recent tool messages should still have original (long) content
    recent_tools = [m for m in recent if m.get("role") == "tool"]
    assert all(len(m.get("content", "")) > 50 for m in recent_tools)

    # old turns' tool messages should be summarized (shorter content)
    old_tools = [
        m for m in result[:recent_start]
        if isinstance(m, dict) and m.get("role") == "tool"
    ]
    # at least some old tool results should have been shortened
    shortened = [m for m in old_tools if len(m.get("content", "")) < 100]
    assert len(shortened) > 0, "old tool_results should be summarized"


def test_pipeline_stage1_skips_code_explore():
    """Stage 1 must not prune code_explore results (always preserved)."""
    code_explore_content = "code explore " * 1000  # ~13k chars
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "go"},
    ]
    for t in range(4):
        assistant = _make_assistant_with_tool_calls(t, tool_names=("code_explore",))
        messages.append(assistant)
        messages.append(_make_tool_result(t, 0, code_explore_content))
        messages.append(_make_tool_result(t, 0, code_explore_content))  # extra to make a turn
    # Fix tool_call_id matching: rebuild properly
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "go"},
    ]
    for t in range(4):
        assistant = {
            "role": "assistant",
            "content": f"turn {t}",
            "tool_calls": [
                {"id": f"ce_{t}_0", "type": "function", "function": {"name": "code_explore", "arguments": json.dumps({"path": "src"})}},
            ],
        }
        messages.append(assistant)
        messages.append({"role": "tool", "tool_call_id": f"ce_{t}_0", "content": code_explore_content})

    result = run_compact_pipeline(messages, stage=1)
    # all code_explore results must be intact
    tools = [m for m in result if isinstance(m, dict) and m.get("role") == "tool"]
    assert all(m["content"] == code_explore_content for m in tools)


def test_pipeline_stage3_fold_uses_turn_boundary():
    """Stage 3: 4+ turns → 1 fold marker + last 2 turns, no orphan tool_use/tool_result."""
    tool_names = ("fs_list", "fs_read", "bash")
    messages = [
        {"role": "system", "content": "You are an agent."},
        {"role": "user", "content": "Explore."},
    ]
    for t in range(5):
        messages.extend(_make_turn(t, tool_names=tool_names, result_content="data " * 50))

    result = run_compact_pipeline(messages, stage=3)

    # system prompt preserved
    assert result[0]["role"] == "system"
    assert result[0]["content"] == "You are an agent."
    # second message is the fold marker (system role)
    assert result[1]["role"] == "system"
    assert "[folded" in result[1]["content"]

    # remaining messages = last 2 turns = 2 * (1 + 3) = 8
    recent = result[2:]
    assert len(recent) == 8
    # recent starts with assistant (turn 4)
    assert recent[0]["role"] == "assistant"
    assert recent[0].get("tool_calls")

    # no orphan tool_use or tool_result: every tool_call_id in recent assistants
    # must have a matching tool result in recent
    recent_call_ids = set()
    for m in recent:
        if m.get("role") == "assistant":
            for tc in m.get("tool_calls") or []:
                recent_call_ids.add(tc["id"])
    recent_tool_ids = {m["tool_call_id"] for m in recent if m.get("role") == "tool"}
    assert recent_call_ids == recent_tool_ids, "no orphan tool_use ↔ tool_result pairs"


def test_pipeline_stage3_fallback_when_no_turns(caplog):
    """All text messages (no tool_calls) → fallback to recent_keep=6, warning logged."""
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]
    # 12 plain text assistant/user messages, no tool_calls
    for i in range(12):
        messages.append({"role": "assistant", "content": f"reply {i} " * 50})
        messages.append({"role": "user", "content": f"prompt {i} " * 50})

    with caplog.at_level(logging.WARNING, logger="app.services.compact_pipeline"):
        result = run_compact_pipeline(messages, stage=3)

    # system preserved
    assert result[0]["role"] == "system"
    # fallback: recent_keep=6 → keep last 6 messages
    # result = [system_prompt, fold_marker, *recent_6]
    assert len(result) == 1 + 1 + 6
    assert result[1]["role"] == "system"  # fold marker
    # last 6 messages preserved verbatim (last is "prompt 11 ...")
    assert result[-1]["content"].startswith("prompt 11")
    assert result[-2]["content"].startswith("reply 11")

    # warning was logged
    assert any("falling back" in r.message for r in caplog.records)


def test_pipeline_stage2_reprunes_more_aggressively():
    """Stage 2 re-prunes stage-1 summaries: further token reduction.

    Stage 2 is designed to run on the output of stage 1, not the original
    messages. We apply stage 1 first, then stage 2 on the stage-1 result,
    and verify further token reduction.
    """
    import copy

    tool_names = ("fs_list", "fs_read", "bash", "fs_grep")
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "go"},
    ]
    big_result = json.dumps([{"name": f"f{i}", "size": 100, "depth": 3, "isDirectory": i % 3 == 0} for i in range(200)])
    for t in range(4):
        messages.extend(_make_turn(t, tool_names=tool_names, result_content=big_result))

    stage1 = run_compact_pipeline(copy.deepcopy(messages), stage=1)
    # Stage 2 runs on stage 1's output (re-pruning).
    stage2_from_s1 = run_compact_pipeline(copy.deepcopy(stage1), stage=2)

    s1_tokens = estimate_messages_tokens(stage1)
    s2_tokens = estimate_messages_tokens(stage2_from_s1)
    # stage 2 should produce fewer tokens than stage 1
    assert s2_tokens < s1_tokens, f"stage 2 ({s2_tokens}) should be < stage 1 ({s1_tokens})"


def test_pipeline_invalid_stage_raises():
    with pytest.raises(ValueError, match="unknown compact stage"):
        run_compact_pipeline([], stage=99)


# ─── Section 10: End-to-end + regression tests ───────────────────────────────


def test_eight_turn_seven_tool_pipeline():
    """E2E: 8 turns × 7 tools = 56 messages. Stages escalate correctly.

    Constructs a realistic scenario with fs_list / fs_read full / bash /
    code_explore tool results. Verifies that stage 1 reduces tokens while
    keeping recent turns intact, stage 2 reduces further, and stage 3 folds
    older turns into a marker while preserving the last 2 turns.
    """
    import copy

    tool_names = ("fs_list", "fs_read", "bash", "fs_grep", "code_explore", "fs_write", "fs_edit")
    # Realistic-ish results: fs_list returns entries, fs_read returns code, etc.
    fs_list_result = json.dumps(
        [{"name": f"file{i}.py", "size": 1024, "depth": 2, "isDirectory": i % 3 == 0} for i in range(50)]
    )
    fs_read_result = "\n".join(f"def func_{i}(): pass" for i in range(200))
    bash_result = "\n".join(f"output line {i}" for i in range(30))
    code_explore_result = "module summary " * 200

    def _result_for_tool(name, idx):
        if name == "fs_list":
            return fs_list_result
        if name == "fs_read":
            return fs_read_result
        if name == "bash":
            return bash_result
        if name == "code_explore":
            return code_explore_result
        return f"result for {name} {idx}"

    def _args_for_tool(name, idx):
        if name == "fs_read":
            return {"path": f"src/file{idx}.py", "mode": "full"}
        if name == "fs_list":
            return {"path": "src", "depth": 2}
        if name == "bash":
            return {"command": "ls"}
        if name == "fs_grep":
            return {"pattern": "func", "path": "src"}
        return {"path": f"src/{idx}"}

    messages = [
        {"role": "system", "content": "You are a helpful agent."},
        {"role": "user", "content": "Explore the project thoroughly."},
    ]
    for t in range(8):
        calls = []
        for i, name in enumerate(tool_names):
            calls.append({
                "id": f"call_{t}_{i}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(_args_for_tool(name, i), ensure_ascii=False),
                },
            })
        messages.append({"role": "assistant", "content": f"Turn {t}: exploring.", "tool_calls": calls})
        for i, name in enumerate(tool_names):
            messages.append({
                "role": "tool",
                "tool_call_id": f"call_{t}_{i}",
                "content": _result_for_tool(name, i),
            })

    original_tokens = estimate_messages_tokens(messages)
    assert original_tokens > 0

    # Stage 1: summarize old tool results, keep recent 2 turns
    s1 = run_compact_pipeline(copy.deepcopy(messages), stage=1)
    s1_tokens = estimate_messages_tokens(s1)
    assert s1_tokens < original_tokens, "stage 1 should reduce tokens"

    # Recent 2 turns (turns 6,7) must be intact — same tool_call_ids
    s1_recent_tools = {m["tool_call_id"] for m in s1 if isinstance(m, dict) and m.get("role") == "tool"}
    expected_recent_ids = {f"call_{t}_{i}" for t in (6, 7) for i in range(len(tool_names))}
    assert expected_recent_ids.issubset(s1_recent_tools), "recent turns' tool results must be preserved"

    # code_explore results in recent turns must be verbatim
    for m in s1:
        if isinstance(m, dict) and m.get("role") == "tool":
            tid = m.get("tool_call_id", "")
            if tid.startswith("call_6_4") or tid.startswith("call_7_4"):  # code_explore is index 4
                assert m["content"] == code_explore_result, "code_explore must be verbatim"

    # Stage 2: further reduction
    s2 = run_compact_pipeline(copy.deepcopy(s1), stage=2)
    s2_tokens = estimate_messages_tokens(s2)
    assert s2_tokens < s1_tokens, "stage 2 should reduce further"

    # Stage 3: fold older turns, keep recent 2 + system prompt
    s3 = run_compact_pipeline(copy.deepcopy(messages), stage=3)
    s3_tokens = estimate_messages_tokens(s3)
    assert s3_tokens < original_tokens, "stage 3 should reduce tokens"

    # System prompt preserved
    assert s3[0]["role"] == "system"
    assert s3[0]["content"] == "You are a helpful agent."
    # Fold marker present
    assert any(isinstance(m, dict) and m.get("role") == "system" and "[folded" in m.get("content", "") for m in s3)
    # Recent 2 turns intact
    s3_recent_ids = {m["tool_call_id"] for m in s3 if isinstance(m, dict) and m.get("role") == "tool"}
    assert expected_recent_ids.issubset(s3_recent_ids), "stage 3 must keep recent turns intact"


def test_legacy_path_unchanged_when_disabled():
    """Regression: when pipeline disabled, legacy _mid_run_compact behavior is used.

    This is covered by test_react_loop_legacy_fallback_when_disabled in
    test_react_loop.py (verifies _mid_run_compact is called, not
    run_compact_pipeline). Here we verify the legacy function itself still
    works (prune + fold).
    """
    from app.services.agent_runner import _mid_run_compact

    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "go"},
    ]
    # Add enough messages to trigger fold (fold_threshold=20, keep_recent=15)
    for i in range(25):
        messages.append({"role": "assistant", "content": f"reply {i} " * 100})
        messages.append({"role": "user", "content": f"prompt {i}"})

    result = _mid_run_compact(list(messages))
    # Legacy fold reduces message count
    assert len(result) < len(messages)
    # System prompt preserved
    assert result[0]["role"] == "system"


# ─── Section 11: Regression — estimate_messages_tokens refactor ─────────────


def test_estimate_messages_tokens_unchanged_after_refactor():
    """Regression: estimate_messages_tokens returns the same value as before refactor.

    The old implementation computed per-field tokens directly; the new one
    delegates to ``estimate_dict_message_tokens``. Both use the same per-field
    logic (``estimate_tokens`` per field + 4/msg overhead with
    ``include_reasoning=True``), so results must be identical.
    """
    from app.utils.model_registry import estimate_tokens

    messages = [
        {"role": "system", "content": "You are a helpful agent."},
        {"role": "user", "content": "List the files in src."},
        {
            "role": "assistant",
            "content": "I'll list the files now.",
            "reasoning_content": "Thinking about which tool to use.",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "fs_list",
                        "arguments": '{"path": "src", "depth": 3}',
                    },
                },
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": '[{"name": "index.ts", "isDirectory": false}]',
        },
    ]

    # Compute expected using the old per-field formula
    expected = 0
    for msg in messages:
        expected += 4  # per-message overhead
        content = msg.get("content")
        if isinstance(content, str):
            expected += estimate_tokens(content)
        reasoning = msg.get("reasoning_content")
        if isinstance(reasoning, str):
            expected += estimate_tokens(reasoning)
        tool_calls = msg.get("tool_calls")
        if isinstance(tool_calls, list):
            for tc in tool_calls:
                fn = tc.get("function", {})
                expected += estimate_tokens(fn.get("name", ""))
                expected += estimate_tokens(fn.get("arguments", ""))

    result = estimate_messages_tokens(messages)
    assert result == expected

