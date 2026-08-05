"""Hybrid search — RRF fusion of BM25 + Vector, with wikilink post-processing expansion.

Combines SQLite FTS5 BM25 keyword matching with vector cosine similarity,
fused via Reciprocal Rank Fusion (RRF). Wikilink graph relations are used
only as post-processing expansion (neighbor metadata), not for ranking.

Each SearchResult includes:
  - scores: per-component breakdown (bm25, vector, rrf)
  - expansion: outlinks + inlinks neighbor metadata (path, name, description)

Result paths are workspace-relative so clients can open files via the files API.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings
from app.memory.file_store.markdown_io import read_markdown
from app.memory.search.bm25_index import BM25Index
from app.memory.search.vector_index import VectorIndex
from app.memory.search.wikilink_expander import WikilinkExpander

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """A single memory search result."""

    path: str
    name: str
    content: str
    score: float
    source: str = "bm25"  # bm25 | vector | rrf
    frontmatter: dict = field(default_factory=dict)
    scores: dict = field(default_factory=dict)  # {"bm25": float, "vector": float, "rrf": float}
    expansion: dict = field(default_factory=dict)  # {"outlinks": [...], "inlinks": [...]}


class HybridSearch:
    """Hybrid BM25 + Vector search with RRF fusion and wikilink post-processing."""

    def __init__(
        self,
        settings: Settings,
        bm25: BM25Index,
        expander: WikilinkExpander,
        workspace_root: Path | None = None,
        vector_index: VectorIndex | None = None,
        embed_fn: Callable[[str], list[float]] | None = None,
    ):
        self.settings = settings
        self.bm25 = bm25
        self.expander = expander
        # Optional: when provided, relative index keys resolve against it.
        # Absolute keys (legacy / direct bm25.add) still work without it.
        self.workspace_root = Path(workspace_root) if workspace_root is not None else None
        self.vector_index = vector_index
        self.embed_fn = embed_fn

    def set_embed_fn(self, fn: Callable[[str], list[float]] | None) -> None:
        """Inject or update embedding function at runtime."""
        self.embed_fn = fn

    def _resolve(self, path: str) -> Path:
        """Resolve an index path (absolute tolerated; relative needs workspace_root)."""
        p = Path(path)
        if p.is_absolute() or self.workspace_root is None:
            return p
        return self.workspace_root / p

    def _to_rel(self, path: str) -> str:
        """Normalize path to workspace-relative for API consumers when possible."""
        p = Path(path)
        if not p.is_absolute():
            return path
        if self.workspace_root is None:
            return path
        try:
            return str(p.resolve().relative_to(self.workspace_root.resolve()))
        except ValueError:
            return path

    def _vector_search(
        self,
        query: str,
        top_k: int,
        agent_id: str | None,
        bucket: str | None,
    ) -> dict[str, int]:
        """Run vector search and aggregate to file-level ranks.

        Returns {path: rank} where rank is 1-based (1 = best).
        Returns empty dict if vector search is not available.
        """
        if not self.embed_fn or not self.vector_index or self.vector_index.count() == 0:
            return {}

        try:
            query_emb = self.embed_fn(query)
        except Exception as e:
            logger.warning("HybridSearch: query embedding failed, degrading to BM25-only: %s", e)
            return {}

        hits = self.vector_index.search(
            query_emb,
            top_k=top_k * 2,
            agent_id=agent_id,
            bucket=bucket,
        )
        if not hits:
            return {}

        # File-level aggregation: keep highest-scoring chunk per path
        best_per_path: dict[str, float] = {}
        for path, _chunk_idx, score in hits:
            if path not in best_per_path or score > best_per_path[path]:
                best_per_path[path] = score

        # Rank paths by best chunk score (descending)
        sorted_paths = sorted(best_per_path.keys(), key=lambda p: best_per_path[p], reverse=True)
        return {path: rank + 1 for rank, path in enumerate(sorted_paths)}

    async def search(
        self,
        query: str,
        top_k: int | None = None,
        agent_id: str | None = None,
        bucket: str | None = None,
    ) -> list[SearchResult]:
        """Search memory files using BM25 + Vector RRF fusion + wikilink post-processing."""
        k = top_k or self.settings.memory_search_top_k
        bm25_weight = self.settings.memory_bm25_weight
        vector_weight = self.settings.memory_vector_weight
        rrf_k = self.settings.memory_rrf_k

        # Phase 1: BM25 search
        bm25_hits = self.bm25.search(query, top_k=k * 2, agent_id=agent_id, bucket=bucket)
        bm25_ranked = {path: rank + 1 for rank, (path, _) in enumerate(bm25_hits)}

        # Phase 1: Vector search (when available)
        vector_ranked = self._vector_search(query, k, agent_id, bucket)

        # Phase 2: Two-way RRF fusion (wikilink does NOT participate)
        all_paths = set(bm25_ranked.keys()) | set(vector_ranked.keys())
        rrf_scores: list[tuple[str, float, dict]] = []
        for path in all_paths:
            score = 0.0
            bm25_component = 0.0
            vector_component = 0.0
            if path in bm25_ranked:
                bm25_component = bm25_weight * (1.0 / (rrf_k + bm25_ranked[path]))
                score += bm25_component
            if path in vector_ranked:
                vector_component = vector_weight * (1.0 / (rrf_k + vector_ranked[path]))
                score += vector_component
            rrf_scores.append((path, score, {
                "bm25": round(bm25_component, 6),
                "vector": round(vector_component, 6),
                "rrf": round(score, 6),
            }))

        rrf_scores.sort(key=lambda x: x[1], reverse=True)
        top_paths = rrf_scores[:k]

        # Phase 3: Load file content + wikilink post-processing expansion
        results: list[SearchResult] = []
        for path, score, score_breakdown in top_paths:
            mem_file = read_markdown(self._resolve(path))
            if mem_file is None:
                continue

            source = "rrf"
            if path in bm25_ranked and path not in vector_ranked:
                source = "bm25"
            elif path in vector_ranked and path not in bm25_ranked:
                source = "vector"

            # Apply archived status deprioritization (0.5x BM25 multiplier)
            if mem_file.frontmatter.status == "archived":
                score_breakdown = dict(score_breakdown)
                score_breakdown["bm25"] = round(score_breakdown["bm25"] * 0.5, 6)
                score_breakdown["rrf"] = round(
                    score_breakdown["bm25"] + score_breakdown["vector"], 6
                )
                score = score_breakdown["rrf"]

            # Build wikilink expansion metadata (post-processing, not ranking)
            expansion = self._build_expansion(path)

            results.append(SearchResult(
                path=self._to_rel(path),
                name=mem_file.frontmatter.name,
                content=mem_file.body,
                score=score,
                source=source,
                frontmatter=mem_file.frontmatter.to_dict(),
                scores=score_breakdown,
                expansion=expansion,
            ))

        return results

    def _build_expansion(self, path: str) -> dict:
        """Build outlinks + inlinks expansion metadata for a search result."""
        outlinks_meta: list[dict] = []
        inlinks_meta: list[dict] = []

        for ol in self.expander.get_outlinks(path):
            target_path = ol["target"]
            target_mem = read_markdown(self._resolve(target_path))
            if target_mem:
                outlinks_meta.append({
                    "path": self._to_rel(target_path),
                    "name": target_mem.frontmatter.name,
                    "description": target_mem.frontmatter.description,
                    "predicate": ol.get("predicate"),
                })

        for il in self.expander.get_inlinks(path):
            source_path = il["source"]
            source_mem = read_markdown(self._resolve(source_path))
            if source_mem:
                inlinks_meta.append({
                    "path": self._to_rel(source_path),
                    "name": source_mem.frontmatter.name,
                    "description": source_mem.frontmatter.description,
                    "predicate": il.get("predicate"),
                })

        return {"outlinks": outlinks_meta, "inlinks": inlinks_meta}
