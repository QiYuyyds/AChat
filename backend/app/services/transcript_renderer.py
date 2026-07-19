"""Shared transcript renderer and token estimation utilities.

This module serves as the public entry point for two concerns used across the
four-layer compaction system:

1. Tool-aware transcript rendering (``render_tool_aware_transcript``) — render
   DB Message list as plain-text transcript that includes tool_use and
   tool_result information. tool_result content is compressed via Tier 0's
   ``summarize_tool_result`` strategy table (stage=1).

2. Token estimation — three message-level estimators, each serving a different
   input format:

   - ``estimate_dict_message_tokens(msg: dict, include_reasoning=False)`` —
     OpenAI chat message dict format. Used by Tier 0 (in-memory ReAct loop,
     passes ``include_reasoning=True`` because in-memory messages carry
     ``reasoning_content``) and Tier 4 (cross-run serialized history, passes
     ``include_reasoning=False`` because spec 13 does not replay thinking
     cross-run). Counts: content + tool_calls.function.name/arguments +
     4/msg overhead. Excludes role/tool_call_id/type JSON metadata.

   - ``estimate_full_message_tokens(messages: list[Message])`` — DB Message
     format. Used by Session Memory (``should_extract``) and Tier 2/3
     (``estimate_uncompacted_tokens``). Counts text + thinking + tool_use
     args + tool_result + effective_prompt — the FULL picture needed to
     decide whether compaction is necessary.

   - ``estimate_tokens(text: str)`` — base function in ``model_registry.py``.
     4 chars ≈ 1 token. Used by both estimators above for per-field
     estimation.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.db.models import Message
from app.services.compact_pipeline import summarize_tool_result_with_summary
from app.utils.model_registry import estimate_tokens

logger = logging.getLogger(__name__)

__all__ = [
    "render_tool_aware_transcript",
    "estimate_dict_message_tokens",
    "estimate_full_message_tokens",
]

# Per-message overhead to approximate chat-template framing tokens.
_DICT_MESSAGE_OVERHEAD_TOKENS = 4

# Truncate tool_use args JSON in transcript to keep lines readable.
_MAX_ARGS_JSON_LEN = 200


def render_tool_aware_transcript(
    messages: list[Message],
    agent_names: dict[str, str] | None = None,
) -> str:
    """Render messages as a tool-aware plain-text transcript.

    - user / system messages → ``<role>：<text>`` (single line).
    - agent messages → multi-line: text content, then ``↳ tool_use:`` and
      ``↳ tool_result:`` lines for each tool part.
    - ``thinking`` parts are skipped (private to the agent).
    - Messages with no text and no tool parts are skipped.

    ``agent_names`` maps agent_id → display name. When omitted or no match,
    falls back to ``"Agent"``.
    """
    agent_names = agent_names or {}
    lines: list[str] = []

    for msg in messages:
        parts = msg.parts_list
        if not parts:
            continue

        if msg.role in ("user", "system"):
            text = _extract_text(parts)
            if text:
                who = "用户" if msg.role == "user" else "系统"
                lines.append(f"{who}：{text}")
        else:
            agent_lines = _render_agent_message(parts, msg, agent_names)
            lines.extend(agent_lines)

    return "\n".join(lines)


def estimate_full_message_tokens(messages: list[Message]) -> int:
    """Estimate token count for ALL parts of messages.

    Counts text, thinking, effective_prompt, tool_use (args), and
    tool_result (result) parts. This replaces the legacy text-only
    estimation that severely undercounted tool-heavy turns.
    """
    total = 0
    for msg in messages:
        for p in msg.parts_list:
            total += _estimate_part_tokens(p)
    return total


def estimate_dict_message_tokens(
    msg: dict, include_reasoning: bool = False,
) -> int:
    """Estimate tokens for a single OpenAI-format chat message dict.

    Counts ``content`` (string, or text parts when it's a list),
    ``tool_calls[*].function.name`` + ``function.arguments``, and
    ``reasoning_content`` (only when ``include_reasoning=True``). Each message
    adds a fixed 4-token overhead. Does NOT count ``role``, ``tool_call_id``,
    ``type``, or other JSON-structural fields.

    Used by Tier 0 (``compact_pipeline.estimate_messages_tokens`` with
    ``include_reasoning=True``) and Tier 4 (``conversation_context`` with
    ``include_reasoning=False``).
    """
    total = _DICT_MESSAGE_OVERHEAD_TOKENS

    content = msg.get("content")
    if isinstance(content, str):
        total += estimate_tokens(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    total += estimate_tokens(part.get("text", ""))
            elif isinstance(part, str):
                total += estimate_tokens(part)

    if include_reasoning:
        reasoning = msg.get("reasoning_content")
        if isinstance(reasoning, str):
            total += estimate_tokens(reasoning)

    tool_calls = msg.get("tool_calls")
    if isinstance(tool_calls, list):
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            name = fn.get("name") or ""
            args = fn.get("arguments") or ""
            total += estimate_tokens(name)
            if isinstance(args, dict):
                args = json.dumps(args, ensure_ascii=False)
            total += estimate_tokens(args)

    return total


# ─── internal helpers ───────────────────────────────────────────────────────


def _extract_text(parts: list[dict]) -> str:
    """Extract plain text from text-type parts."""
    texts = [
        p.get("content", "")
        for p in parts
        if p.get("type") == "text" and p.get("content")
    ]
    return "\n".join(texts).strip()


def _render_agent_message(
    parts: list[dict],
    msg: Message,
    agent_names: dict[str, str],
) -> list[str]:
    """Render an agent message as multiple transcript lines."""
    # Check if there's any content to render.
    text = _extract_text(parts)
    has_tool_parts = any(
        p.get("type") in ("tool_use", "tool_result") for p in parts
    )
    if not text and not has_tool_parts:
        return []

    who = agent_names.get(msg.agent_id or "", msg.agent_id or "Agent")
    result: list[str] = []

    if text:
        result.append(f"{who}：{text}")

    # Build callId → (toolName, args) map from tool_use parts for pairing.
    tool_use_map: dict[str, tuple[str, dict]] = {}
    for p in parts:
        if p.get("type") == "tool_use":
            call_id = p.get("callId", "")
            tool_name = p.get("toolName", "unknown")
            args = _parse_args(p.get("args"))
            tool_use_map[call_id] = (tool_name, args)

    for p in parts:
        ptype = p.get("type")
        if ptype == "tool_use":
            result.append(_render_tool_use_line(p))
        elif ptype == "tool_result":
            result.append(_render_tool_result_line(p, tool_use_map))

    return result


def _render_tool_use_line(part: dict) -> str:
    """Render a tool_use part as ``  ↳ tool_use: <name>(<args_json>)``."""
    tool_name = part.get("toolName", "unknown")
    args = _parse_args(part.get("args"))
    args_json = json.dumps(args, ensure_ascii=False)
    if len(args_json) > _MAX_ARGS_JSON_LEN:
        args_json = args_json[:_MAX_ARGS_JSON_LEN] + "…"
    return f"  ↳ tool_use: {tool_name}({args_json})"


def _render_tool_result_line(
    part: dict,
    tool_use_map: dict[str, tuple[str, dict]],
) -> str:
    """Render a tool_result part as ``  ↳ tool_result: [<name>] <summary> | <content>``."""
    call_id = part.get("callId", "")
    tool_name, args = tool_use_map.get(call_id, ("unknown", {}))

    content = _normalize_result(part.get("result"))
    compressed, summary = summarize_tool_result_with_summary(
        tool_name, args, content, stage=1
    )
    return f"  ↳ tool_result: [{tool_name}] {summary} | {compressed}"


def _estimate_part_tokens(part: dict) -> int:
    """Estimate tokens for a single message part."""
    ptype = part.get("type")
    if ptype in ("text", "thinking", "effective_prompt"):
        return estimate_tokens(part.get("content", ""))
    if ptype == "tool_use":
        return estimate_tokens(json.dumps(part.get("args", {}), ensure_ascii=False))
    if ptype == "tool_result":
        result = part.get("result", "")
        if isinstance(result, str):
            return estimate_tokens(result)
        return estimate_tokens(json.dumps(result, ensure_ascii=False))
    return 0


def _parse_args(raw: Any) -> dict:
    """Best-effort parse of tool_call arguments into a dict."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except (TypeError, ValueError):
            pass
    return {}


def _normalize_result(result: Any) -> str:
    """Normalize a tool_result's result field to a plain string."""
    if isinstance(result, str):
        return result
    if result is None:
        return ""
    try:
        return json.dumps(result, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(result)
