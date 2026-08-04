"""File-native memory storage layer — Markdown files + frontmatter + wikilinks."""

from app.memory.file_store.frontmatter import MemoryFrontmatter
from app.memory.file_store.markdown_io import read_markdown, write_markdown
from app.memory.file_store.wikilinks import extract_wikilinks, render_wikilinks
from app.memory.file_store.workspace import MemoryWorkspace

__all__ = [
    "MemoryWorkspace",
    "MemoryFrontmatter",
    "read_markdown",
    "write_markdown",
    "extract_wikilinks",
    "render_wikilinks",
]
