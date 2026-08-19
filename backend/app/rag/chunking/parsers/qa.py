"""QA preset — extract question-answer pairs from FAQ/interview documents.

Uses regex patterns to detect QA structures in multiple formats:
  Q: ... A: ... | 问：... 答：... | Q1. ... A1. ...

Non-QA content falls back to the general strategy.
"""

import re

from app.rag.chunking.parsers import general
from app.rag.chunking.utils.md_parser_utils import split_by_headings

# QA patterns: match question marker + content + answer marker + content
# Supports Q:/A:, 问：/答：, Q1./A1., and numbered question formats
_QA_PATTERNS = [
    # Q: / A: format (English)
    re.compile(
        r"(?:^|\n)\s*[Qq]\s*[:：]\s*(.+?)\s*\n\s*[Aa]\s*[:：]\s*(.+?)(?=\n\s*[Qq]\s*[:：]|\Z)",
        re.DOTALL,
    ),
    # 问：/ 答： format (CJK)
    re.compile(
        r"(?:^|\n)\s*问\s*[:：]\s*(.+?)\s*\n\s*答\s*[:：]\s*(.+?)(?=\n\s*问\s*[:：]|\Z)",
        re.DOTALL,
    ),
    # Q1. / A1. format (numbered)
    re.compile(
        r"(?:^|\n)\s*[Qq]\d+\s*[\.．]\s*(.+?)\s*\n\s*[Aa]\d+\s*[\.．]\s*(.+?)(?=\n\s*[Qq]\d+\s*[\.．]|\Z)",
        re.DOTALL,
    ),
]


def chunk_markdown(content: str, config: dict) -> list[str]:
    """QA chunking: extract QA pairs, fallback non-QA content to general."""
    if not content or not content.strip():
        return []

    chunks: list[str] = []
    consumed_positions: list[tuple[int, int]] = []  # (start, end) of matched QA pairs

    # Process content section by section (by headings) to preserve context
    for _breadcrumb, section_text in split_by_headings(content):
        section_chunks = _extract_qa_from_section(section_text, config, consumed_positions)
        if section_chunks:
            chunks.extend(section_chunks)

    # If no QA pairs found, fall back entirely to general
    if not chunks:
        return general.chunk_markdown(content, config)

    # Collect non-QA text and chunk it with general
    non_qa_text = _extract_non_qa_text(content, consumed_positions)
    if non_qa_text.strip():
        non_qa_chunks = general.chunk_markdown(non_qa_text, config)
        chunks.extend(non_qa_chunks)

    return chunks


def _extract_qa_from_section(
    section_text: str, config: dict, consumed_positions: list[tuple[int, int]]
) -> list[str]:
    """Extract QA pairs from a section, recording consumed positions."""
    qa_chunks: list[str] = []

    for pattern in _QA_PATTERNS:
        for m in pattern.finditer(section_text):
            question = m.group(1).strip()
            answer = m.group(2).strip()
            if not question or not answer:
                continue

            qa_chunk = f"Q: {question}\nA: {answer}"
            qa_chunks.append(qa_chunk)
            consumed_positions.append((m.start(), m.end()))

    return qa_chunks


def _extract_non_qa_text(
    text: str, consumed_positions: list[tuple[int, int]]
) -> str:
    """Extract text that was not part of any QA pair."""
    if not consumed_positions:
        return text

    # Sort by start position
    sorted_positions = sorted(consumed_positions, key=lambda x: x[0])

    parts: list[str] = []
    cursor = 0
    for start, end in sorted_positions:
        if start > cursor:
            parts.append(text[cursor:start])
        cursor = max(cursor, end)

    if cursor < len(text):
        parts.append(text[cursor:])

    return "".join(parts)
