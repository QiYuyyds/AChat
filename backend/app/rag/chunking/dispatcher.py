"""Chunk dispatcher — routes to preset-specific chunker by preset_id.

This is the single entry point for all chunking: chunk_markdown(content, preset_id, config).
"""

import logging
from collections.abc import Callable

from app.rag.chunking.parsers import general, qa, semantic, separator
from app.rag.chunking.presets import normalize_chunk_preset_id, resolve_chunk_processing_params

logger = logging.getLogger(__name__)


def chunk_markdown(
    content: str,
    preset_id: str = "",
    config: dict | None = None,
    *,
    embed_fn: Callable[[str], list[float]] | None = None,
) -> list[str]:
    """Dispatch chunking to the appropriate preset parser.

    Args:
        content: The text content to chunk.
        preset_id: Preset identifier (general/qa/semantic/separator).
                   Invalid IDs fall back to 'general'.
        config: Optional configuration dict with chunk_size, chunk_overlap, etc.
        embed_fn: Optional embedding function, needed for semantic preset.
                  If not provided, semantic preset falls back to general.

    Returns:
        List of chunk strings.
    """
    pid = normalize_chunk_preset_id(preset_id)
    params = resolve_chunk_processing_params(pid, config)

    if pid == "general":
        return general.chunk_markdown(content, params)

    if pid == "qa":
        return qa.chunk_markdown(content, params)

    if pid == "semantic":
        if embed_fn is None:
            logger.info("Semantic preset requested but embed_fn not available, falling back to general")
            return general.chunk_markdown(content, params)
        return semantic.chunk_markdown(content, params, embed_fn=embed_fn)

    if pid == "separator":
        return separator.chunk_markdown(content, params)

    # Should not reach here due to normalize_chunk_preset_id, but just in case
    return general.chunk_markdown(content, params)
