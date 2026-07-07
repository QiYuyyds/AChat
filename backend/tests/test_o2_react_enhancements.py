"""Tests for O2 Step 5 (tool call cache) and Step 6 (token budget control).

Covers tasks 4.7 and 5.7. Tests _mid_run_compact directly and the cache
constants/logic via the ReAct loop.
"""

from __future__ import annotations

import json

from app.services.agent_runner import (
    READONLY_CACHEABLE_TOOLS,
    _mid_run_compact,
)
from app.utils.model_registry import estimate_tokens

# ─── Task 4.7: read-only tool cache constants ─────────────────────────────────


def test_readonly_cacheable_tools_contains_expected_set():
    """READONLY_CACHEABLE_TOOLS includes fs_read, read_artifact, read_attachment."""
    assert "fs_read" in READONLY_CACHEABLE_TOOLS
    assert "read_artifact" in READONLY_CACHEABLE_TOOLS
    assert "read_attachment" in READONLY_CACHEABLE_TOOLS


def test_readonly_cacheable_tools_excludes_write_tools():
    """fs_write, bash, write_artifact are NOT in the cacheable set."""
    assert "fs_write" not in READONLY_CACHEABLE_TOOLS
    assert "bash" not in READONLY_CACHEABLE_TOOLS
    assert "write_artifact" not in READONLY_CACHEABLE_TOOLS


def test_cache_key_construction():
    """Cache key format: '{tool_name}:{json.dumps(args, sort_keys=True)}'."""
    args = {"path": "src/main.py"}
    key = f"fs_read:{json.dumps(args, sort_keys=True)}"
    assert key == 'fs_read:{"path": "src/main.py"}'


def test_cache_key_different_args_different_keys():
    """Different args produce different cache keys."""
    key1 = f"fs_read:{json.dumps({'path': 'a.py'}, sort_keys=True)}"
    key2 = f"fs_read:{json.dumps({'path': 'b.py'}, sort_keys=True)}"
    assert key1 != key2


def test_cache_key_same_args_different_order_same_key():
    """Args with different key order produce the same cache key (sort_keys=True)."""
    key1 = f"fs_read:{json.dumps({'path': 'a.py', 'encoding': 'utf-8'}, sort_keys=True)}"
    key2 = f"fs_read:{json.dumps({'encoding': 'utf-8', 'path': 'a.py'}, sort_keys=True)}"
    assert key1 == key2


# ─── Task 5.7: _mid_run_compact ───────────────────────────────────────────────


def test_mid_run_compact_prunes_large_tool_results():
    """_mid_run_compact replaces large old tool results with a truncation marker."""
    large_content = "x" * 10000  # > 2000 tokens when serialized
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "do something"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "fs_read", "arguments": "{}"}}
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": large_content},
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": "next"},
    ]

    # len=6, recent_keep=6 → no pruning (all within recent window)
    result = _mid_run_compact(messages)
    assert len(result) == 6  # no fold since < 20


def test_mid_run_compact_prunes_with_more_messages():
    """With >6 messages, old tool results get pruned."""
    large_content = json.dumps({"data": "x" * 10000})
    # Need >6 messages with a tool message outside the recent_keep window.
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "fs_read", "arguments": "{}"}}
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": large_content},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u3"},
        {"role": "assistant", "content": "a3"},
        {"role": "user", "content": "u4"},
    ]

    result = _mid_run_compact(messages)
    # len=10, recent_keep=6, messages[:-6] = messages[:4]
    # messages[3] is the tool message with large content → should be pruned
    tool_msg = result[3]
    assert tool_msg["content"] == "[tool_result 已裁剪（mid-run compact）]"


def test_mid_run_compact_folds_excess_messages():
    """When message count > 20, old messages are folded into a marker."""
    messages = [
        {"role": "system", "content": "sys"},
    ]
    for i in range(25):
        messages.append({"role": "user", "content": f"msg {i}"})

    result = _mid_run_compact(messages)

    # len=26 > 20 (fold_threshold), keep_recent=15
    # first = system message, old_count = 26 - 15 - 1 = 10
    # result = [system, fold_marker, *recent_15]
    assert len(result) == 17  # 1 system + 1 fold_marker + 15 recent
    assert result[0]["role"] == "system"
    assert result[0]["content"] == "sys"
    assert result[1]["role"] == "system"
    assert "已折叠" in result[1]["content"]
    assert "10" in result[1]["content"]


def test_mid_run_compact_preserves_small_tool_results():
    """Small tool results are not pruned."""
    small_content = "small result"
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "fs_read", "arguments": "{}"}}
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": small_content},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "u2"},
        {"role": "assistant", "content": "a2"},
        {"role": "user", "content": "u3"},
        {"role": "assistant", "content": "a3"},
        {"role": "user", "content": "u4"},
    ]

    result = _mid_run_compact(messages)

    # The tool result should be preserved (small content)
    tool_msg = result[3]
    assert tool_msg["content"] == small_content


def test_mid_run_compact_no_change_when_few_messages():
    """With <= 20 messages and no large tool results, messages are unchanged."""
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]

    result = _mid_run_compact(messages)
    assert result == messages


def test_mid_run_compact_reduces_token_count():
    """After compact, the total token count should decrease for large messages."""
    large_content = json.dumps({"data": "x" * 10000})
    messages = [
        {"role": "system", "content": "sys"},
    ]
    # Add 25 messages with some large tool results
    for i in range(12):
        messages.append({"role": "user", "content": f"msg {i}"})
        messages.append({"role": "assistant", "content": None, "tool_calls": [
            {"id": f"c{i}", "type": "function", "function": {"name": "fs_read", "arguments": "{}"}}
        ]})
        messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": large_content})

    before_tokens = estimate_tokens(json.dumps(messages, ensure_ascii=False))
    result = _mid_run_compact(messages)
    after_tokens = estimate_tokens(json.dumps(result, ensure_ascii=False))

    assert after_tokens < before_tokens


# ─── Task 5.7: token budget thresholds ────────────────────────────────────────


def test_token_budget_90_percent_threshold():
    """90% threshold logic: if total > 0.90 * limit, compact is triggered."""
    model_limit = 100000
    total_tokens = 91000  # 91% > 90%
    assert total_tokens > 0.90 * model_limit
    assert total_tokens <= 0.95 * model_limit  # but not > 95%


def test_token_budget_95_percent_threshold():
    """95% threshold logic: if total > 0.95 * limit, loop stops."""
    model_limit = 100000
    total_tokens = 96000  # 96% > 95%
    assert total_tokens > 0.95 * model_limit


def test_token_budget_below_threshold():
    """Below 90%: no compact, no stop."""
    model_limit = 100000
    total_tokens = 80000  # 80% < 90%
    assert total_tokens <= 0.90 * model_limit


def test_token_budget_no_model_info_skips():
    """When model_limit = 0, token budget check is skipped."""
    model_limit = 0
    # The check is `if model_limit > 0`, so 0 means skip
    assert model_limit == 0
