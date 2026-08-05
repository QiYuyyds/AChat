"""File-native memory storage layer — Markdown files + frontmatter + wikilinks."""

from app.memory.file_store.file_catalog import FileCatalog
from app.memory.file_store.frontmatter import MemoryFrontmatter
from app.memory.file_store.markdown_io import read_markdown, write_markdown
from app.memory.file_store.wikilinks import (
    WikilinkLink,
    add_wikilink,
    extract_wikilinks,
    extract_wikilinks_detailed,
    render_wikilinks,
    retarget_wikilinks,
)
from app.memory.file_store.workspace import MemoryWorkspace

__all__ = [
    "MemoryWorkspace",
    "MemoryFrontmatter",
    "FileCatalog",
    "WikilinkLink",
    "read_markdown",
    "write_markdown",
    "extract_wikilinks",
    "extract_wikilinks_detailed",
    "render_wikilinks",
    "add_wikilink",
    "retarget_wikilinks",
]
