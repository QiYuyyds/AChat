"""Hybrid search — RRF fusion of BM25 + wikilink expansion.

Combines SQLite FTS5 BM25 keyword matching with wikilink graph relation
expansion, fused via Reciprocal Rank Fusion (RRF).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.memory.file_store.markdown_io import MemoryFile, read_markdown
from app.memory.search.bm25_index import BM25Index
from app.memory.search.wikilink_expander import WikilinkExpander
from app.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """A single memory search result."""

    path: str
    name: str
    content: str
    score: float
    source: str = "bm25"  # bm25 | wikilink | rrf
    frontmatter: dict = field(default_factory=dict)


class HybridSearch:
    """Hybrid BM25 + wikilink search with RRF fusion."""

    def __init__(self, settings: Settings, bm25: BM25Index, expander: WikilinkExpander):
        self.settings = settings
        self.bm25 = bm25
        self.expander = expander

    async def search(
        self,
        query: str,
        top_k: int | None = None,
        agent_id: str | None = None,
        bucket: str | None = None,
    ) -> list[SearchResult]:
        """Search memory files using BM25 + wikilink expansion + RRF fusion."""
        k = top_k or self.settings.memory_search_top_k
        bm25_weight = self.settings.memory_bm25_weight
        wl_weight = self.settings.memory_wikilink_weight
        rrf_k = self.settings.memory_rrf_k

        # Phase 1: BM25 search
        bm25_hits = self.bm25.search(query, top_k=k * 2, agent_id=agent_id, bucket=bucket)
        bm25_ranked = {path: rank + 1 for rank, (path, _) in enumerate(bm25_hits)}

        # Phase 2: Wikilink expansion from BM25 hits
        seed_paths = [path for path, _ in bm25_hits]
        expanded = self.expander.expand(seed_paths, max_hops=1)
        wl_ranked = {path: rank + 1 for rank, path in enumerate(expanded)}

        # Phase 3: RRF fusion
        all_paths = set(bm25_ranked.keys()) | set(wl_ranked.keys())
        rrf_scores: list[tuple[str, float]] = []
        for path in all_paths:
            score = 0.0
            if path in bm25_ranked:
                score += bm25_weight * (1.0 / (rrf_k + bm25_ranked[path]))
            if path in wl_ranked:
                score += wl_weight * (1.0 / (rrf_k + wl_ranked[path]))
            rrf_scores.append((path, score))

        rrf_scores.sort(key=lambda x: x[1], reverse=True)
        top_paths = rrf_scores[:k]

        # Load file content for results
        results: list[SearchResult] = []
        for path, score in top_paths:
            mem_file = read_markdown(Path(path))
            if mem_file is None:
                continue
            source = "rrf"
            if path in bm25_ranked and path not in wl_ranked:
                source = "bm25"
            elif path in wl_ranked and path not in bm25_ranked:
                source = "wikilink"
            results.append(SearchResult(
                path=path,
                name=mem_file.frontmatter.name,
                content=mem_file.body,
                score=score,
                source=source,
                frontmatter=mem_file.frontmatter.to_dict(),
            ))

        return results
