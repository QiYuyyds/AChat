"""Semantic preset — sentence boundary splitting + embedding clustering.

Groups sentences by embedding similarity, augments each chunk with
heading context from its parent section. Falls back to general when
embed_fn is not available (handled by dispatcher).
"""

from collections.abc import Callable

from app.rag.chunking.utils.md_parser_utils import split_by_headings
from app.rag.chunking.utils.semantic_utils import split_and_cluster


def chunk_markdown(
    content: str,
    config: dict,
    *,
    embed_fn: Callable[[str], list[float]] | None = None,
) -> list[str]:
    """Semantic chunking: sentence split + embedding clustering + heading context."""
    if not content or not content.strip():
        return []

    chunk_size = int(config.get("chunk_size", 200))
    threshold = float(config.get("semantic_threshold", 0.5))

    chunks: list[str] = []

    # Process section by section to add heading context
    for breadcrumb, section_text in split_by_headings(content):
        section_text_stripped = section_text.strip()
        if not section_text_stripped:
            continue

        # Cluster sentences within this section
        section_chunks = split_and_cluster(
            section_text_stripped,
            embed_fn,
            threshold=threshold,
            max_chunk_size=chunk_size,
        )

        for chunk_text in section_chunks:
            if not chunk_text.strip():
                continue

            # Augment with heading context
            if breadcrumb:
                chunks.append(f"{breadcrumb}\n{chunk_text}")
            else:
                chunks.append(chunk_text)

    # If no chunks produced (e.g., empty sections), return content as single chunk
    if not chunks and content.strip():
        chunks.append(content.strip())

    return chunks
