"""Token counting and text splitting utilities for chunking presets.

Uses rune-based counting (Unicode code points) to match RecursiveSplitter's
chunk_size semantics — no external tokenizer dependency needed.
"""

import re

# Sentence-ending punctuation for CJK + Latin
_SENTENCE_END_RE = re.compile(r"[。！？；\.\!\?]\s*")

# Markdown heading pattern
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def count_tokens(text: str) -> int:
    """Count tokens as rune length (matches RecursiveSplitter chunk_size semantics)."""
    return len(text)


def split_sentences(text: str) -> list[str]:
    """Split text into sentences by CJK/Latin sentence-ending punctuation.

    Preserves the trailing punctuation and whitespace with each sentence.
    """
    if not text or not text.strip():
        return []

    parts = _SENTENCE_END_RE.split(text)
    sentences: list[str] = []
    buf = ""

    for part in parts:
        if not part:
            continue
        buf += part
        # If buf ends with sentence-ending punct, flush it
        if buf and buf[-1] in "。！？；.!?":
            stripped = buf.strip()
            if stripped:
                sentences.append(buf.strip())
            buf = ""

    if buf.strip():
        sentences.append(buf.strip())

    return sentences


def extract_heading_tree(text: str) -> list[tuple[int, str, int, int]]:
    """Extract Markdown heading tree with character positions.

    Returns list of (level, title, start_pos, end_pos) tuples.
    end_pos is the start of the next heading or len(text).
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


def get_heading_context(text: str, pos: int) -> str:
    """Get the heading context (breadcrumb) for a character position.

    Returns a string like "# Title > ## Subsection" for the position,
    or empty string if no heading context found.
    """
    tree = extract_heading_tree(text)
    breadcrumbs: list[str] = []

    for level, title, start, end in tree:
        if start <= pos < end:
            breadcrumbs.append(f"{'#' * level} {title}")

    return " > ".join(breadcrumbs)
