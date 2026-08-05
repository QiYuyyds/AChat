"""auto_index — file change monitoring + index maintenance.

Scans daily/ and digest/ directories on startup (full reindex) and
after file writes (incremental update). Maintains:
  - BM25 index (SQLite FTS5)
  - Wikilink adjacency graph (with predicate column)
  - File catalog (path + st_mtime tracking)
  - Broken link detection (target file missing)

Document keys are workspace-relative paths so search results can be
opened via the files API without absolute path leakage.
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

    def _rel_path(self, filepath: Path) -> str:
        """Store index keys as paths relative to the memory workspace root."""
        try:
            return str(filepath.resolve().relative_to(self.workspace.root.resolve()))
        except ValueError:
            return str(filepath)

    def index_file(self, filepath: Path) -> None:
        """Index a single memory file (add/update in BM25 + wikilink graph + catalog)."""
        mem_file = read_markdown(filepath)
        if mem_file is None:
            return

        rel = self._rel_path(filepath)

        # BM25 index
        self.bm25.add(
            path=rel,
            name=mem_file.frontmatter.name,
            content=mem_file.content,
            agent_id=mem_file.frontmatter.agent_id,
            bucket=mem_file.frontmatter.bucket,
            tags=mem_file.frontmatter.tags,
        )

        # Wikilink graph — use detailed extraction for predicate support
        self.expander.remove_edges_for(rel)
        links = extract_wikilinks_detailed(mem_file.body)
        if links:
            edge_list = [(link.target, link.predicate) for link in links]
            self.expander.add_edges_detailed(rel, edge_list)

        # Broken link detection: check if targets exist
        for link in links:
            target_path = Path(link.target)
            if not target_path.is_absolute():
                # Try resolving relative to workspace root
                resolved = self.workspace.root / link.target
            else:
                resolved = target_path
            if not resolved.exists():
                logger.debug("Broken wikilink: %s → %s (target missing)", rel, link.target)

        # Update file catalog
        if self.file_catalog:
            bucket = mem_file.frontmatter.bucket
            if str(filepath).startswith(str(self.workspace.daily_dir)):
                bucket = "daily"
            self.file_catalog.upsert(rel, bucket=bucket)

    def remove_file(self, filepath: Path) -> None:
        """Remove a file from all indexes."""
        rel = self._rel_path(filepath)
        self.bm25.remove(rel)
        self.expander.remove_all_for(rel)
        if self.file_catalog:
            self.file_catalog.remove(rel)

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
        existing_paths = {self._rel_path(f) for f in all_files}
        # Also keep absolute forms for legacy edges that may still use them
        for f in all_files:
            existing_paths.add(str(f))
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
