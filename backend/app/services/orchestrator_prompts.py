"""Utility functions for text extraction and formatting.

Originally part of the orchestrator prompt builders, these helpers are still
used by agent_loop.py for extracting text from message parts.

The orchestrator plan/aggregate/sub-agent prompt builders have been removed
along with the legacy orchestrator (replaced by the Unified Agent Loop).
"""

from __future__ import annotations

import json


# ─── JSON-stringify parity ────────────────────────────────────────────────────
def _json(value: object) -> str:
    """Match JS JSON.stringify default output (compact separators, non-ascii)."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


# ─── misc ─────────────────────────────────────────────────────────────────────
def extract_text_from_parts(parts: list[dict]) -> str:
    out: list[str] = []
    for p in parts:
        ptype = p.get("type")
        if ptype in ("text", "thinking"):
            out.append(p.get("content", ""))
        elif ptype == "code":
            out.append("```" + p.get("language", "") + "\n" + p.get("content", "") + "\n```")
        elif ptype == "image_attachment":
            out.append(
                f"[图片附件: {p['fileName']} ({format_size(p['size'])}, "
                f"{p['mimeType']}) · id={p['attachmentId']}]"
            )
        elif ptype == "file_attachment":
            out.append(
                f"[文件附件: {p['fileName']} ({format_size(p['size'])}, "
                f"{p['mimeType']}) · id={p['attachmentId']}]"
            )
    return "\n\n".join(s for s in out if s)


def format_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes}B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f}KB"
    return f"{num_bytes / 1024 / 1024:.1f}MB"


def escape_xml(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def xml_attr(s: str) -> str:
    return '"' + escape_xml(s).replace('"', "&quot;") + '"'
