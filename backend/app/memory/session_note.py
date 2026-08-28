"""SessionNote — structured 10-section YAML session note.

Replaces the legacy unstructured text summary in ``context_summaries`` table
(``summary_type='session'``).  The note is serialized to YAML for storage and
to XML for LLM injection.  YAML parsing failure (legacy plain-text data or
malformed output) returns ``None``, triggering fallback to plain-text
``<session_memory>`` injection.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import yaml

logger = logging.getLogger(__name__)

# Section size limits (design doc §7.5.3 / §10.2)
SECTION_LIMITS: dict[str, int] = {
    "key_decisions": 20,
    "files_touched": 30,
    "commands_run": 20,
    "artifacts_produced": 10,
    "blockers": 10,
    "open_questions": 10,
}

# Total YAML character limit before compression kicks in
YAML_MAX_CHARS = 3000


@dataclass
class SessionNote:
    """Structured session note with 10 sections + covers_up_to timestamp."""

    title: str = ""
    current_state: str = ""
    key_decisions: list[str] = field(default_factory=list)
    files_touched: list[str] = field(default_factory=list)
    commands_run: list[str] = field(default_factory=list)
    artifacts_produced: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)
    architecture_understanding: str = ""
    covers_up_to: float | None = None

    # ─── serialization ────────────────────────────────────────────────

    def to_yaml(self) -> str:
        """Serialize to YAML for storage. Ensures Chinese is not escaped."""
        data = {
            "title": self.title,
            "current_state": self.current_state,
            "key_decisions": list(self.key_decisions),
            "files_touched": list(self.files_touched),
            "commands_run": list(self.commands_run),
            "artifacts_produced": list(self.artifacts_produced),
            "blockers": list(self.blockers),
            "open_questions": list(self.open_questions),
            "next_steps": list(self.next_steps),
            "architecture_understanding": self.architecture_understanding,
            "covers_up_to": self.covers_up_to,
        }
        return yaml.safe_dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)

    @classmethod
    def from_yaml(cls, raw: str) -> SessionNote | None:
        """Parse from YAML. Returns None if parsing fails.

        If raw contains a ```yaml code block, extract the block content first.
        """
        if not raw or not raw.strip():
            return None

        extracted = _extract_yaml_code_block(raw)
        content = extracted if extracted is not None else raw

        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError:
            return None

        if not isinstance(data, dict):
            return None

        def _get_list(key: str) -> list[str]:
            val = data.get(key)
            if val is None:
                return []
            if isinstance(val, list):
                return [str(item) for item in val]
            return [str(val)]

        def _get_str(key: str) -> str:
            val = data.get(key)
            if val is None:
                return ""
            return str(val)

        covers = data.get("covers_up_to")
        covers_float: float | None
        if covers is None:
            covers_float = None
        elif isinstance(covers, (int, float)):
            covers_float = float(covers)
        else:
            covers_float = None

        return cls(
            title=_get_str("title"),
            current_state=_get_str("current_state"),
            key_decisions=_get_list("key_decisions"),
            files_touched=_get_list("files_touched"),
            commands_run=_get_list("commands_run"),
            artifacts_produced=_get_list("artifacts_produced"),
            blockers=_get_list("blockers"),
            open_questions=_get_list("open_questions"),
            next_steps=_get_list("next_steps"),
            architecture_understanding=_get_str("architecture_understanding"),
            covers_up_to=covers_float,
        )

    def to_xml(self) -> str:
        """Serialize to XML format for LLM injection.

        Each section is a child tag; list items are wrapped in ``<item>``.
        """
        covers_attr = (
            f' covers_up_to="{self.covers_up_to}"' if self.covers_up_to is not None else ""
        )
        lines: list[str] = [f"<session_note{covers_attr}>"]

        lines.append(f"<title>{_xml_escape(self.title)}</title>")
        lines.append(f"<current_state>{_xml_escape(self.current_state)}</current_state>")

        for section in ("key_decisions", "files_touched", "commands_run",
                        "artifacts_produced", "blockers", "open_questions",
                        "next_steps"):
            items = getattr(self, section, [])
            lines.append(f"<{section}>")
            for item in items:
                lines.append(f"  <item>{_xml_escape(item)}</item>")
            lines.append(f"</{section}>")

        lines.append(
            f"<architecture_understanding>{_xml_escape(self.architecture_understanding)}</architecture_understanding>"
        )
        lines.append("</session_note>")
        return "\n".join(lines)


# ─── helpers ────────────────────────────────────────────────────────────


def _xml_escape(text: str) -> str:
    """Escape XML special characters."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _extract_yaml_code_block(raw: str) -> str | None:
    """Extract content from a ```yaml ... ``` code block if present.

    Returns None if no code block is found.
    """
    pattern = r"```(?:yaml|yml)?\s*\n(.*?)```"
    match = re.search(pattern, raw, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


# ─── size control ───────────────────────────────────────────────────────


def _enforce_section_limits(note: SessionNote) -> SessionNote:
    """Enforce per-section size limits and trigger compression if needed.

    Limits (design doc §7.5.3):
    - key_decisions ≤ 20, files_touched ≤ 30, commands_run ≤ 20
    - artifacts_produced ≤ 10, blockers ≤ 10, open_questions ≤ 10
    - If YAML total > 3000 chars, invoke _compress_note.
    """
    for section, limit in SECTION_LIMITS.items():
        items = getattr(note, section, [])
        if len(items) > limit:
            setattr(note, section, items[-limit:])

    yaml_len = len(note.to_yaml())
    if yaml_len > YAML_MAX_CHARS:
        note = _compress_note(note)

    return note


def _compress_note(note: SessionNote) -> SessionNote:
    """Compress session note when it exceeds size limit.

    Strategy (design doc §7.8):
    - key_decisions: merge similar items (e.g., "改了 A 文件" + "改了 B 文件" → "改了 A, B 等 2 个文件")
    - files_touched: keep all "已改", trim "已读" to recent 15
    - commands_run: keep only recent 10
    - blockers/open_questions: keep all unresolved + recent 5 resolved
    """
    note.key_decisions = _merge_similar_decisions(note.key_decisions)

    changed = [f for f in note.files_touched if "已改" in f]
    read_only = [f for f in note.files_touched if "已读" in f]
    note.files_touched = changed + read_only[-15:]

    note.commands_run = note.commands_run[-10:]

    note.blockers = _filter_resolved(note.blockers, "[已解决]")
    note.open_questions = _filter_resolved(note.open_questions, "[已回答]")

    return note


def _merge_similar_decisions(decisions: list[str]) -> list[str]:
    """Merge decisions that share the same prefix (e.g., '改了 X 文件').

    Groups by the first token (before space) and merges similar items
    into a summary entry when a group has more than 2 items.
    """
    if not decisions:
        return []

    groups: dict[str, list[str]] = {}
    order: list[str] = []

    for d in decisions:
        key = d.split(" ")[0].split(":")[0].strip()[:10]
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(d)

    merged: list[str] = []
    for key in order:
        items = groups[key]
        if len(items) > 2:
            names = ", ".join(items[:3])
            merged.append(f"{names} 等 {len(items)} 条相关决策")
        else:
            merged.extend(items)

    return merged


def _filter_resolved(items: list[str], marker: str) -> list[str]:
    """Keep all unresolved + only the most recent 5 resolved items."""
    resolved = [item for item in items if marker in item]
    unresolved = [item for item in items if marker not in item]
    return unresolved + resolved[-5:]
