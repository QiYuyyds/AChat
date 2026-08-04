"""auto_index — file change monitoring + index maintenance.

Scans daily/ and digest/ directories on startup (full reindex) and
after file writes (incremental update). Maintains both BM25 and
wikilink indexes.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.memory.file_store.markdown_io import read_markdown
from app.memory.file_store.wikilinks import extract_wikilinks
from app.memory.file_store.workspace import MemoryWorkspace
from app.memory.search.bm25_index import BM25Index
from app.memory.search.wikilink_expander import WikilinkExpander

logger = logging.getLogger(__name__)


class AutoIndex:
    """Index maintenance for BM25 + wikilink graphs."""

    def __init__(
        self,
        workspace: MemoryWorkspace,
        bm25: BM25Index,
        expander: WikilinkExpander,
    ):
        self.workspace = workspace
        self.bm25 = bm25
        self.expander = expander

    def index_file(self, filepath: Path) -> None:
        """Index a single memory file (add/update in BM25 + wikilink graph)."""
        mem_file = read_markdown(filepath)
        if mem_file is None:
            return

        # BM25 index
        self.bm25.add(
            path=str(filepath),
            name=mem_file.frontmatter.name,
            content=mem_file.content,
            agent_id=mem_file.frontmatter.agent_id,
            bucket=mem_file.frontmatter.bucket,
            tags=mem_file.frontmatter.tags,
        )

        # Wikilink graph
        self.expander.remove_edges_for(str(filepath))
        wikilinks = extract_wikilinks(mem_file.body)
        if wikilinks:
            self.expander.add_edges(str(filepath), wikilinks)

    def remove_file(self, filepath: Path) -> None:
        """Remove a file from both indexes."""
        path_str = str(filepath)
        self.bm25.remove(path_str)
        self.expander.remove_all_for(path_str)

    def full_reindex(self) -> int:
        """Rebuild both indexes from scratch by scanning all memory files.

        Returns the number of files indexed.
        """
        self.bm25.clear()
        self.expander.clear()

        count = 0
        # Index daily files
        if self.workspace.daily_dir.exists():
            for f in self.workspace.daily_dir.rglob("*.md"):
                self.index_file(f)
                count += 1

        # Index digest files
        if self.workspace.digest_dir.exists():
            for f in self.workspace.digest_dir.rglob("*.md"):
                self.index_file(f)
                count += 1

        logger.info("Full reindex complete: %d files indexed", count)
        return count
