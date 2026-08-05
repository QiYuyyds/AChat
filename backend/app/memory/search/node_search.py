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
            bucket: Optional bucket filter ('procedure' | 'personal' | 'wiki'); None = all digest.
            limit: Maximum number of results.

        Returns:
            List of NodeSearchResult, sorted by score descending.
        """
        # BM25 search restricted to digest directory files
        # We use bucket filter but also need to ensure results are from digest/
        bm25_hits = self.bm25.search(query, top_k=limit * 3, bucket=bucket)

        def _is_digest_path(path: str) -> bool:
            p = path.replace("\\", "/")
            if p.startswith("digest/"):
                return True
            abs_prefix = str(self.workspace.digest_dir.resolve()).replace("\\", "/")
            return p.startswith(abs_prefix.rstrip("/") + "/") or p == abs_prefix

        def _resolve_path(path: str) -> Path:
            p = Path(path)
            if p.is_absolute():
                return p
            return self.workspace.root / p

        # Filter to only digest/ paths and aggregate by path (take highest score)
        path_best: dict[str, float] = {}
        for path, score in bm25_hits:
            if not _is_digest_path(path):
                continue
            if path not in path_best or score > path_best[path]:
                path_best[path] = score

        if not path_best:
            # No direct digest hits — expand from BM25 seeds (including daily),
            # but skip provenance edges that only encode lineage noise.
            seed_paths = [path for path, _ in bm25_hits]
            if seed_paths:
                expanded = self.expander.expand(
                    seed_paths,
                    max_hops=1,
                    exclude_predicates={"derived_from"},
                )
                for p in expanded:
                    if _is_digest_path(p) and p not in path_best:
                        path_best[p] = 0.1  # low score for expansion-only hits

        # Sort by score descending
        sorted_paths = sorted(path_best.items(), key=lambda x: x[1], reverse=True)[:limit]

        # Build results with frontmatter
        results: list[NodeSearchResult] = []
        for path, score in sorted_paths:
            mem_file = read_markdown(_resolve_path(path))
            if mem_file is None:
                continue

            # Skip archived nodes
            if mem_file.frontmatter.status == "archived":
                continue

            results.append(NodeSearchResult(
                path=path.replace("\\", "/"),
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
