"""Node search — digest-only node-level search for dream integrate.

Dedicated search that only queries `digest/` directory files, aggregating
multiple hits on the same file into a single node-level result. Each result
includes the file path, frontmatter (name + description), and the highest
hit score. Does NOT apply link expansion or agent_id filtering — designed
for cross-agent recall during dream integrate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.memory.file_store.markdown_io import read_markdown
from app.memory.file_store.workspace import MemoryWorkspace
from app.memory.search.bm25_index import BM25Index
from app.memory.search.wikilink_expander import WikilinkExpander

logger = logging.getLogger(__name__)


@dataclass
class NodeSearchResult:
    """A single node-level search result from digest files."""

    path: str
    name: str
    description: str
    content: str
    score: float
    frontmatter: dict = field(default_factory=dict)
    bucket: str = ""


class NodeSearch:
    """Digest-only node-level search for dream integrate."""

    def __init__(
        self,
        bm25: BM25Index,
        expander: WikilinkExpander,
        workspace: MemoryWorkspace,
    ):
        self.bm25 = bm25
        self.expander = expander
        self.workspace = workspace

    def search(
        self,
        query: str,
        bucket: str | None = None,
        limit: int = 10,
    ) -> list[NodeSearchResult]:
        """Search digest files only, aggregating per-file hits.

        Args:
            query: Search query string.
            bucket: Optional bucket filter ('procedure' or 'wiki').
            limit: Maximum number of results.

        Returns:
            List of NodeSearchResult, sorted by score descending.
        """
        # BM25 search restricted to digest directory files
        # We use bucket filter but also need to ensure results are from digest/
        bm25_hits = self.bm25.search(query, top_k=limit * 3, bucket=bucket)

        # Filter to only digest/ paths and aggregate by path (take highest score)
        digest_prefix = str(self.workspace.digest_dir)
        path_best: dict[str, float] = {}
        for path, score in bm25_hits:
            if not path.startswith(digest_prefix):
                continue
            if path not in path_best or score > path_best[path]:
                path_best[path] = score

        if not path_best:
            # No direct digest hits — try wikilink expansion from ALL BM25 hits
            # (including daily files) to find linked digest nodes
            seed_paths = [path for path, _ in bm25_hits]
            if seed_paths:
                expanded = self.expander.expand(seed_paths, max_hops=1)
                for p in expanded:
                    if p.startswith(digest_prefix) and p not in path_best:
                        path_best[p] = 0.1  # low score for expansion-only hits

        # Sort by score descending
        sorted_paths = sorted(path_best.items(), key=lambda x: x[1], reverse=True)[:limit]

        # Build results with frontmatter
        results: list[NodeSearchResult] = []
        for path, score in sorted_paths:
            mem_file = read_markdown(Path(path))
            if mem_file is None:
                continue

            # Skip archived nodes
            if mem_file.frontmatter.status == "archived":
                continue

            results.append(NodeSearchResult(
                path=path,
                name=mem_file.frontmatter.name,
                description=mem_file.frontmatter.description,
                content=mem_file.body,
                score=score,
                frontmatter=mem_file.frontmatter.to_dict(),
                bucket=mem_file.frontmatter.bucket,
            ))

        logger.info(
            "node_search: query='%s' bucket=%s → %d results",
            query, bucket, len(results),
        )
        return results
