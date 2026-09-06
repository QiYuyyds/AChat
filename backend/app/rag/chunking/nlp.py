"""Token counting and text splitting utilities for chunking presets.

Uses rune-based counting (Unicode code points) to match RecursiveSplitter's
chunk_size semantics — no external tokenizer dependency needed.
"""

import re

# Sentence-ending punctuation for CJK + Latin
_SENTENCE_END_RE = re.compile(r"[。！？；\.\!\?]\s*")


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
