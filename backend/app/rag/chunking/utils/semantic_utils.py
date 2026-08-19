"""Semantic chunking utilities — sentence boundary detection and embedding clustering.

Used by the `semantic` preset to group sentences by embedding similarity.
"""

import math
from collections.abc import Callable

from app.rag.chunking.nlp import split_sentences


def cluster_sentences_by_embedding(
    sentences: list[str],
    embed_fn: Callable[[str], list[float]],
    *,
    threshold: float = 0.5,
    max_chunk_size: int = 200,
) -> list[str]:
    """Group sentences into chunks by embedding similarity.

    Uses a sequential greedy approach: start a new chunk when cosine similarity
    between consecutive sentence embeddings drops below threshold, or when
    chunk reaches max_chunk_size.

    Returns list of chunk strings (sentences joined with space/newline).
    """
    if not sentences:
        return []

    # Embed all sentences
    embeddings: list[list[float]] = []
    for s in sentences:
        try:
            emb = embed_fn(s)
            embeddings.append(emb)
        except Exception:
            # If embedding fails, fall back to size-based grouping
            return _group_by_size(sentences, max_chunk_size)

    if len(embeddings) != len(sentences):
        return _group_by_size(sentences, max_chunk_size)

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for i, sent in enumerate(sentences):
        sent_len = len(sent)

        # Check if adding this sentence exceeds max size
        if current and current_len + sent_len > max_chunk_size:
            chunks.append("\n".join(current))
            current = [sent]
            current_len = sent_len
            continue

        # Check similarity with previous sentence
        if current and i > 0:
            sim = _cosine_similarity(embeddings[i - 1], embeddings[i])
            if sim < threshold:
                chunks.append("\n".join(current))
                current = [sent]
                current_len = sent_len
                continue

        current.append(sent)
        current_len += sent_len

    if current:
        chunks.append("\n".join(current))

    return chunks


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0

    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot / (norm_a * norm_b)


def _group_by_size(sentences: list[str], max_size: int) -> list[str]:
    """Fallback: group sentences by cumulative size without exceeding max_size."""
    if not sentences:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for sent in sentences:
        sent_len = len(sent)
        if current and current_len + sent_len > max_size:
            chunks.append("\n".join(current))
            current = [sent]
            current_len = sent_len
        else:
            current.append(sent)
            current_len += sent_len

    if current:
        chunks.append("\n".join(current))

    return chunks


def split_and_cluster(
    text: str,
    embed_fn: Callable[[str], list[float]] | None,
    *,
    threshold: float = 0.5,
    max_chunk_size: int = 200,
) -> list[str]:
    """Split text into sentences, then cluster by embedding similarity.

    If embed_fn is None, falls back to size-based grouping.
    """
    sentences = split_sentences(text)
    if not sentences:
        return []

    if embed_fn is None:
        return _group_by_size(sentences, max_chunk_size)

    return cluster_sentences_by_embedding(
        sentences, embed_fn, threshold=threshold, max_chunk_size=max_chunk_size
    )
