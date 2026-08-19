"""RAG chunking presets — 4 种分块策略调度."""

from app.rag.chunking.dispatcher import chunk_markdown
from app.rag.chunking.presets import (
    CHUNK_PRESETS,
    normalize_chunk_preset_id,
    resolve_chunk_processing_params,
)

__all__ = [
    "CHUNK_PRESETS",
    "chunk_markdown",
    "normalize_chunk_preset_id",
    "resolve_chunk_processing_params",
]
