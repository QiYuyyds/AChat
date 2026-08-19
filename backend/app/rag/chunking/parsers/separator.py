"""Separator preset — strict separator-based splitting.

Splits content at occurrences of a configured separator string.
Each segment between separators becomes a chunk. Optionally merges
small segments up to chunk_size.
"""

from app.rag.chunking.parsers import general


def chunk_markdown(content: str, config: dict) -> list[str]:
    """Separator chunking: split at configured separator, merge small segments."""
    if not content or not content.strip():
        return []

    separator = config.get("separator", "---")
    chunk_size = int(config.get("chunk_size", 200))

    if not separator:
        return general.chunk_markdown(content, config)

    # Split at separator occurrences
    parts = content.split(separator)

    # Filter empty parts and strip
    segments = [p.strip() for p in parts if p.strip()]

    if not segments:
        return []

    # Merge small segments up to chunk_size
    chunks: list[str] = []
    buf = ""

    for seg in segments:
        if buf and len(buf) + len(separator) + len(seg) > chunk_size:
            chunks.append(buf)
            buf = seg
        else:
            buf = buf + separator + seg if buf else seg

    if buf:
        chunks.append(buf)

    return chunks
