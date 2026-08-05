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
import re
from pathlib import Path

from app.memory.file_store.file_catalog import FileCatalog
from app.memory.file_store.markdown_io import read_markdown
from app.memory.file_store.wikilinks import extract_wikilinks_detailed
from app.memory.file_store.workspace import MemoryWorkspace
from app.memory.search.bm25_index import BM25Index
from app.memory.search.wikilink_expander import WikilinkExpander

logger = logging.getLogger(__name__)

# Provenance / relation lines are useful as graph edges, but their path tokens
# (especially absolute Windows paths) pollute BM25 keyword search.
_PREDICATE_LINE_RE = re.compile(r"^\w+::\s*\[\[")


def _indexable_body(body: str) -> str:
    """Body text for BM25 — drop predicate wikilink lines."""
    if not body:
        return ""
    kept: list[str] = []
    for line in body.splitlines():
        if _PREDICATE_LINE_RE.match(line.strip()):
            continue
        kept.append(line)
    return "\n".join(kept)


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
        """Store index keys as posix-relative paths under the memory workspace root."""
        try:
            return filepath.resolve().relative_to(self.workspace.root.resolve()).as_posix()
        except ValueError:
            return Path(filepath).as_posix()

    def _normalize_link_target(self, target: str) -> str:
        """Normalize wikilink targets to workspace-relative posix keys when possible."""
        if not target:
            return target
        p = Path(target)
        root = self.workspace.root.resolve()
        try:
            if p.is_absolute():
                return p.resolve().relative_to(root).as_posix()
            return (root / p).resolve().relative_to(root).as_posix()
        except (ValueError, OSError):
            return target.replace("\\", "/")

    def _is_daily_file(self, filepath: Path) -> bool:
        try:
            filepath.resolve().relative_to(self.workspace.daily_dir.resolve())
            return True
        except ValueError:
            return False

    def index_file(self, filepath: Path) -> None:
        """Index a single memory file (add/update in BM25 + wikilink graph + catalog)."""
        mem_file = read_markdown(filepath)
        if mem_file is None:
            return

        rel = self._rel_path(filepath)
        is_daily = self._is_daily_file(filepath)
        # Path wins over frontmatter: daily cards often keep a content bucket label.
        bucket = "daily" if is_daily else (mem_file.frontmatter.bucket or "wiki")

        # BM25 index — exclude provenance path tokens from searchable text.
        index_text = "\n".join(
            p for p in (
                mem_file.frontmatter.name,
                mem_file.frontmatter.description,
                _indexable_body(mem_file.body),
            ) if p and p.strip()
        )
        self.bm25.add(
            path=rel,
            name=mem_file.frontmatter.name,
            content=index_text,
            agent_id=mem_file.frontmatter.agent_id,
            bucket=bucket,
            tags=mem_file.frontmatter.tags,
        )

        # Wikilink graph — use detailed extraction for predicate support
        self.expander.remove_edges_for(rel)
        links = extract_wikilinks_detailed(mem_file.body)
        if links:
            edge_list = [
                (self._normalize_link_target(link.target), link.predicate)
                for link in links
            ]
            self.expander.add_edges_detailed(rel, edge_list)

        # Broken link detection: check if targets exist
        for link in links:
            norm = self._normalize_link_target(link.target)
            resolved = self.workspace.root / norm
            if not resolved.exists():
                logger.debug("Broken wikilink: %s → %s (target missing)", rel, link.target)

        # Update file catalog
        if self.file_catalog:
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
