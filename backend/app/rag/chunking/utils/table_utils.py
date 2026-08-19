"""Table structure recognition and protection utilities.

Detects Markdown tables and ensures they are not split across chunk boundaries.
"""

import re

# A Markdown table: header row + separator row + body rows
_TABLE_RE = re.compile(
    r"^(?:[ \t]*\|[^\n]+\n)"  # header row
    r"(?:[ \t]*\|[\s\-:|]+\|[^\n]*\n)"  # separator row
    r"(?:[ \t]*\|[^\n]*\n?)*",  # body rows
    re.MULTILINE,
)


def find_tables(text: str) -> list[tuple[int, int]]:
    """Find all table blocks in text.

    Returns list of (start, end) character positions.
    """
    return [(m.start(), m.end()) for m in _TABLE_RE.finditer(text)]


def is_inside_table(text: str, pos: int) -> bool:
    """Check if a character position is inside a table block."""
    return any(start <= pos < end for start, end in find_tables(text))


def split_around_tables(text: str) -> list[tuple[bool, str]]:
    """Split text into segments, marking table blocks as atomic.

    Returns list of (is_table, segment) tuples.
    """
    atoms: list[tuple[bool, str]] = []
    cursor = 0

    for start, end in find_tables(text):
        if start > cursor:
            atoms.append((False, text[cursor:start]))
        atoms.append((True, text[start:end]))
        cursor = end

    if cursor < len(text):
        atoms.append((False, text[cursor:]))

    if not atoms:
        atoms = [(False, text)]

    return atoms
