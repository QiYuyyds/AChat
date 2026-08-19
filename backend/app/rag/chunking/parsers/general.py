"""General preset — delegates to existing RecursiveSplitter.

This preset provides backward-compatible chunking behavior: recursive separator
stack + Markdown fence protection + tail-rune overlap.
"""

from app.rag.splitter import RecursiveSplitter


def chunk_markdown(content: str, config: dict) -> list[str]:
    """General chunking: delegate to RecursiveSplitter, return list of strings."""
    chunk_size = int(config.get("chunk_size", 200))
    chunk_overlap = int(config.get("chunk_overlap", 50))
    separators = config.get("separators")

    splitter = RecursiveSplitter(chunk_size, chunk_overlap, separators)
    chunks = splitter.split(content)
    return [c.content for c in chunks]
