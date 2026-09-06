"""Markdown structure parsing utilities for chunking.

Provides heading tree extraction and table protection — shared by
multiple preset parsers.
"""

import re

# Markdown headings
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

# Table detection: line starting with | and following separator line
_TABLE_RE = re.compile(
    r"^\|[^\n]*\n\|[\s\-:|]+\|[^\n]*\n(?:\|[^\n]*\n?)*",
    re.MULTILINE,
)


def protect_tables(text: str) -> list[tuple[bool, str]]:
    """Split text into segments, marking table blocks as atomic.

    Returns list of (is_atom, segment) tuples.
    """
    atoms: list[tuple[bool, str]] = []
    cursor = 0
    for m in _TABLE_RE.finditer(text):
        if m.start() > cursor:
            atoms.append((False, text[cursor:m.start()]))
        atoms.append((True, m.group(0)))
        cursor = m.end()
    if cursor < len(text):
        atoms.append((False, text[cursor:]))
    if not atoms:
        atoms = [(False, text)]
    return atoms


def extract_headings(text: str) -> list[tuple[int, str, int, int]]:
    """Extract heading tree: (level, title, start_pos, end_pos).

    end_pos is the start of the next heading, or len(text) for the last.
    """
    headings: list[tuple[int, str, int]] = []
    for m in _HEADING_RE.finditer(text):
        level = len(m.group(1))
        title = m.group(2).strip()
        headings.append((level, title, m.start()))

    result: list[tuple[int, str, int, int]] = []
    for i, (level, title, start) in enumerate(headings):
        end = headings[i + 1][2] if i + 1 < len(headings) else len(text)
        result.append((level, title, start, end))

    return result


def split_by_headings(text: str) -> list[tuple[str, str]]:
    """Split text into sections by top-level headings.

    Returns list of (heading_breadcrumb, section_content) tuples.
    Sections without a heading get an empty breadcrumb.
    """
    tree = extract_headings(text)

    if not tree:
        return [("", text)] if text.strip() else []

    sections: list[tuple[str, str]] = []

    # Content before first heading
    if tree[0][2] > 0:
        pre = text[: tree[0][2]]
        if pre.strip():
            sections.append(("", pre))

    for _i, (level, title, start, end) in enumerate(tree):
        section_text = text[start:end]
        breadcrumb = f"{'#' * level} {title}"
        sections.append((breadcrumb, section_text))

    return sections
