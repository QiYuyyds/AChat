"""Hybrid search — RRF fusion of BM25 + wikilink expansion.

Combines SQLite FTS5 BM25 keyword matching with wikilink graph relation
expansion, fused via Reciprocal Rank Fusion (RRF).

Each SearchResult includes:
  - scores: per-component breakdown (bm25, wikilink, rrf)
  - expansion: outlinks + inlinks neighbor metadata (path, name, description)

Result paths are workspace-relative so clients can open files via the files API.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings
from app.memory.file_store.markdown_io import read_markdown
from app.memory.search.bm25_index import BM25Index
from app.memory.search.wikilink_expander import WikilinkExpander

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
    scores: dict = field(default_factory=dict)  # {"bm25": float, "wikilink": float, "rrf": float}
    expansion: dict = field(default_factory=dict)  # {"outlinks": [...], "inlinks": [...]}


class HybridSearch:
    """Hybrid BM25 + wikilink search with RRF fusion."""

    def __init__(
        self,
        settings: Settings,
        bm25: BM25Index,
        expander: WikilinkExpander,
        workspace_root: Path | None = None,
    ):
        self.settings = settings
        self.bm25 = bm25
        self.expander = expander
        # Optional: when provided, relative index keys resolve against it.
        # Absolute keys (legacy / direct bm25.add) still work without it.
        self.workspace_root = Path(workspace_root) if workspace_root is not None else None

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

        # Phase 2: Wikilink expansion from BM25 hits.
        # Skip provenance edges — derived_from only records lineage and often
        # points at unrelated co-batched daily cards.
        seed_paths = [path for path, _ in bm25_hits]
        expanded = self.expander.expand(
            seed_paths,
            max_hops=1,
            exclude_predicates={"derived_from"},
        )
        # Normalize expanded keys so absolute targets can fuse with relative BM25 keys.
        expanded_norm: list[str] = []
        seen_exp: set[str] = set()
        for p in expanded:
            key = self._to_rel(p)
            if key not in seen_exp and key not in bm25_ranked:
                seen_exp.add(key)
                expanded_norm.append(key)
        wl_ranked = {path: rank + 1 for rank, path in enumerate(expanded_norm)}

        # Phase 3: RRF fusion — require BM25 hit, or a non-provenance wikilink neighbor.
        # Pure provenance-only neighbors are already excluded above.
        all_paths = set(bm25_ranked.keys()) | set(wl_ranked.keys())
        rrf_scores: list[tuple[str, float, dict]] = []
        for path in all_paths:
            score = 0.0
            bm25_component = 0.0
            wl_component = 0.0
            if path in bm25_ranked:
                bm25_component = bm25_weight * (1.0 / (rrf_k + bm25_ranked[path]))
                score += bm25_component
            if path in wl_ranked:
                # Related neighbors without a keyword hit stay secondary.
                wl_component = wl_weight * (1.0 / (rrf_k + wl_ranked[path]))
                if path not in bm25_ranked:
                    wl_component *= 0.5
                score += wl_component
            rrf_scores.append((path, score, {
                "bm25": round(bm25_component, 6),
                "wikilink": round(wl_component, 6),
                "rrf": round(score, 6),
            }))

        rrf_scores.sort(key=lambda x: x[1], reverse=True)
        top_paths = rrf_scores[:k]

        # Load file content for results + build expansion meta
        results: list[SearchResult] = []
        for path, score, score_breakdown in top_paths:
            mem_file = read_markdown(self._resolve(path))
            if mem_file is None:
                continue

            source = "rrf"
            if path in bm25_ranked and path not in wl_ranked:
                source = "bm25"
            elif path in wl_ranked and path not in bm25_ranked:
                source = "wikilink"

            # Apply archived status deprioritization (0.5x BM25 multiplier)
            if mem_file.frontmatter.status == "archived":
                score_breakdown = dict(score_breakdown)
                score_breakdown["bm25"] = round(score_breakdown["bm25"] * 0.5, 6)
                score_breakdown["rrf"] = round(
                    score_breakdown["bm25"] + score_breakdown["wikilink"], 6
                )
                score = score_breakdown["rrf"]

            # Build link expansion metadata
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
