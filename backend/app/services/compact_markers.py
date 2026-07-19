"""Marker builders and success judge for the five-stage compaction pipeline.

Produces structured plain-text (not JSON) markers that carry recovery metadata,
replacing the old dead-end ``"[tool_result 已裁剪]"`` markers. Also provides a
strict ``CompactSuccessJudge`` that requires real token reduction (≥15%) rather
than just a change in message count — this lets the ``compact_disabled`` circuit
breaker actually trip.
"""

from __future__ import annotations

from collections import Counter

# Hard caps on marker length so a fold over many turns cannot itself blow the
# budget. summary is capped more tightly than the whole marker.
MAX_MARKER_CHARS = 500
MAX_SUMMARY_CHARS = 200

# success requires at least this much real token reduction (post < pre * 0.85)
EFFECTIVE_COMPACT_RATIO = 0.85

__all__ = [
    "MAX_MARKER_CHARS",
    "MAX_SUMMARY_CHARS",
    "EFFECTIVE_COMPACT_RATIO",
    "CompactMarkerBuilder",
    "CompactSuccessJudge",
]


def _truncate(text: str, limit: int) -> str:
    """Truncate text to limit chars, appending an ellipsis when cut."""
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3] + "..."


class CompactMarkerBuilder:
    """Builds structured plain-text markers for compacted tool results and folded turns.

    Marker shape (plain text, model-parseable, token-cheap)::

        [compacted stage=1 tool=fs_list path=src depth=3]
        [summary: src/ 下 5 文件、3 子目录]
        [recover: fs_list(path='src', depth=3) 重新获取结构]

    Fold marker shape::

        [folded stage=3 turns=3 tools: fs_list×2 fs_read×5 bash×1]
        [summary: 本段主要探索了 src/ 与 backend/ 目录结构]
    """

    @staticmethod
    def build_tool_result_marker(
        stage: int,
        tool_name: str,
        args: dict | None,
        summary: str,
        recover_hint: str,
    ) -> str:
        """Build a per-tool-result substitution marker (≤ MAX_MARKER_CHARS)."""
        args_str = ""
        if args:
            # Keep args compact: show only path-like / mode-like keys.
            keep_keys = ("path", "file", "dir", "directory", "mode", "depth", "query", "command")
            parts = []
            for k in keep_keys:
                if k in args:
                    parts.append(f"{k}={args[k]!r}")
            if parts:
                args_str = " " + " ".join(parts)

        summary = _truncate(summary or "", MAX_SUMMARY_CHARS)
        recover_hint = _truncate(recover_hint or "", MAX_MARKER_CHARS // 3)
        header = f"[compacted stage={stage} tool={tool_name}{args_str}]"

        marker = f"{header}\n[summary: {summary}]\n[recover: {recover_hint}]"
        return _truncate(marker, MAX_MARKER_CHARS)

    @staticmethod
    def build_fold_marker(
        stage: int,
        turns_folded: int,
        tools_used_counts: Counter[str],
        summary: str,
        first_user_msg_head: str | None = None,
        last_assistant_text_head: str | None = None,
    ) -> str:
        """Build a fold marker for a batch of older turns (≤ MAX_MARKER_CHARS).

        ``tools_used_counts`` is shown as top-5 ``tool×N`` pairs. The optional
        ``first_user_msg_head`` / ``last_assistant_text_head`` give a hint of
        what the folded span was about.
        """
        top5 = tools_used_counts.most_common(5)
        tools_str = " ".join(f"{name}×{count}" for name, count in top5) or "(无)"

        summary = _truncate(summary or "", MAX_SUMMARY_CHARS)
        header = f"[folded stage={stage} turns={turns_folded} tools: {tools_str}]"

        lines = [header, f"[summary: {summary}]"]
        if first_user_msg_head:
            lines.append(f"[first_user: {_truncate(first_user_msg_head, 80)}]")
        if last_assistant_text_head:
            lines.append(f"[last_reply: {_truncate(last_assistant_text_head, 80)}]")

        marker = "\n".join(lines)
        return _truncate(marker, MAX_MARKER_CHARS)


class CompactSuccessJudge:
    """Decide whether a compaction stage actually reduced the context.

    Returns ``True`` only when ``post_tokens < pre_tokens * EFFECTIVE_COMPACT_RATIO``
    (at least 15% real token reduction). A change in ``len(messages)`` alone is
    NOT considered success — the old len-based rule made ``compact_disabled``
    unreachable because fold always shrinks the count.
    """

    @staticmethod
    def judge(pre_tokens: int, post_tokens: int, pre_len: int, post_len: int) -> bool:  # noqa: ARG004
        # len is intentionally unused — kept in the signature so callers can
        # pass it without branching, and so the spec scenario "fold that doesn't
        # reduce tokens counts as failure" maps directly to this function.
        if pre_tokens <= 0:
            # No prior tokens → nothing to reduce → not a success.
            return False
        return post_tokens < int(pre_tokens * EFFECTIVE_COMPACT_RATIO)
