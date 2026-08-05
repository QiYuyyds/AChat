"""auto_index — file change monitoring + index maintenance.

Scans daily/ and digest/ directories on startup (full reindex) and
after file writes (incremental update). Maintains:
  - BM25 index (SQLite FTS5)
  - Wikilink adjacency graph (with predicate column)
  - File catalog (path + st_mtime tracking)
  - Broken link detection (target file missing)
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.memory.file_store.file_catalog import FileCatalog
from app.memory.file_store.markdown_io import read_markdown
from app.memory.file_store.wikilinks import extract_wikilinks_detailed
from app.memory.file_store.workspace import MemoryWorkspace
from app.memory.search.bm25_index import BM25Index
from app.memory.search.wikilink_expander import WikilinkExpander

logger = logging.getLogger(__name__)


class AutoIndex:
    """Index maintenance for BM25 + wikilink graphs + file catalog."""

    def __init__(
        self,
        workspace: MemoryWorkspace,
        bm25: BM25Index,
        expander: WikilinkExpander,
        file_catalog: FileCatalog | None = None,
    ):
        self.workspace = workspace
        self.bm25 = bm25
        self.expander = expander
        self.file_catalog = file_catalog

    def index_file(self, filepath: Path) -> None:
        """Index a single memory file (add/update in BM25 + wikilink graph + catalog)."""
        mem_file = read_markdown(filepath)
        if mem_file is None:
            return

        path_str = str(filepath)

        # BM25 index
        self.bm25.add(
            path=path_str,
            name=mem_file.frontmatter.name,
            content=mem_file.content,
            agent_id=mem_file.frontmatter.agent_id,
            bucket=mem_file.frontmatter.bucket,
            tags=mem_file.frontmatter.tags,
        )

        # Wikilink graph — use detailed extraction for predicate support
        self.expander.remove_edges_for(path_str)
        links = extract_wikilinks_detailed(mem_file.body)
        if links:
            edge_list = [(link.target, link.predicate) for link in links]
            self.expander.add_edges_detailed(path_str, edge_list)

        # Broken link detection: check if targets exist
        for link in links:
            target_path = Path(link.target)
            if not target_path.is_absolute():
                # Try resolving relative to workspace root
                resolved = self.workspace.root / link.target
            else:
                resolved = target_path
            if not resolved.exists():
                logger.debug("Broken wikilink: %s → %s (target missing)", path_str, link.target)

        # Update file catalog
        if self.file_catalog:
            bucket = mem_file.frontmatter.bucket
            if str(filepath).startswith(str(self.workspace.daily_dir)):
                bucket = "daily"
            self.file_catalog.upsert(path_str, bucket=bucket)

    def remove_file(self, filepath: Path) -> None:
        """Remove a file from all indexes."""
        path_str = str(filepath)
        self.bm25.remove(path_str)
        self.expander.remove_all_for(path_str)
        if self.file_catalog:
            self.file_catalog.remove(path_str)

    def full_reindex(self) -> int:
        """Rebuild all indexes from scratch by scanning all memory files.

        Also cleans broken wikilink entries.
        Returns the number of files indexed.
        """
        self.bm25.clear()
        self.expander.clear()

        count = 0
        all_files: list[Path] = []

        # Index daily files
        if self.workspace.daily_dir.exists():
            for f in self.workspace.daily_dir.rglob("*.md"):
                self.index_file(f)
                all_files.append(f)
                count += 1

        # Index digest files
        if self.workspace.digest_dir.exists():
            for f in self.workspace.digest_dir.rglob("*.md"):
                self.index_file(f)
                all_files.append(f)
                count += 1

        # Clean broken wikilinks: targets that don't exist on disk
        existing_paths = {str(f) for f in all_files}
        # Also check targets that might use relative paths from workspace root
        for f in all_files:
            existing_paths.add(str(f.relative_to(self.workspace.root)))
        broken_count = self.expander.remove_broken_links(existing_paths)
        if broken_count:
            logger.info("Full reindex: removed %d broken wikilink entries", broken_count)

        # Reconcile file catalog
        if self.file_catalog:
            try:
                self.file_catalog.reconcile(self.workspace.daily_dir, self.workspace.digest_dir)
            except Exception as e:
                logger.warning("File catalog reconcile failed during reindex: %s", e)

        logger.info("Full reindex complete: %d files indexed", count)
        return count
