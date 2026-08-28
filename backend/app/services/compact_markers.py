"""Marker builders for the compaction pipeline.

Produces structured plain-text (not JSON) markers that carry recovery metadata,
replacing the old dead-end ``"[tool_result 已裁剪]"`` markers.
"""

from __future__ import annotations

from collections import Counter

# Hard caps on marker length so a fold over many turns cannot itself blow the
# budget. summary is capped more tightly than the whole marker.
MAX_MARKER_CHARS = 500
MAX_SUMMARY_CHARS = 200

__all__ = [
    "MAX_MARKER_CHARS",
    "MAX_SUMMARY_CHARS",
    "CompactMarkerBuilder",
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
