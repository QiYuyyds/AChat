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
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from app.config import Settings
from app.memory.access_stats import AccessStats
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
        access_stats: AccessStats | None = None,
    ):
        self.settings = settings
        self.bm25 = bm25
        self.expander = expander
        # Optional: when provided, relative index keys resolve against it.
        # Absolute keys (legacy / direct bm25.add) still work without it.
        self.workspace_root = Path(workspace_root) if workspace_root is not None else None
        self.vector_index = vector_index
        self.embed_fn = embed_fn
        self.access_stats = access_stats

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

        # Phase 2b: Rerank (importance × decay × recency), if enabled
        if self.settings.memory_rerank_enabled:
            top_paths = self._rerank(top_paths)

        # Fire-and-forget access stats recording (non-blocking, must not delay return)
        if self.access_stats:
            for path, _score, _scores in top_paths:
                # Use the workspace-relative path as the key
                rel_path = self._to_rel(path)
                try:
                    self.access_stats.record(rel_path)
                except Exception as e:
                    logger.warning("access_stats record failed for %s: %s", rel_path, e)

        # Phase 2c: daily TTL query-period filtering — exclude daily cards that are
        # past TTL AND already distilled (have digest inlinks). File & index untouched.
        if self.settings.memory_daily_ttl_days > 0:
            top_paths = self._apply_daily_ttl_filter(top_paths)

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

    def _rerank(
        self, paths_with_scores: list[tuple[str, float, dict]]
    ) -> list[tuple[str, float, dict]]:
        """Apply rerank formula: final = rrf × (0.5 + importance) × decay × recency.

        - decay = 0.5^(days_since_access / half_life); falls back to updated_at
          when no access record exists.
        - recency = monotonic non-decreasing function of updated_at.
        - Score breakdown gets a new "rerank" key with the multiplier applied.
        """
        half_life = self.settings.memory_decay_half_life_days
        now = time.time()
        day_seconds = 86400.0

        reranked: list[tuple[str, float, dict]] = []
        for path, rrf_score, score_breakdown in paths_with_scores:
            if rrf_score <= 0:
                reranked.append((path, rrf_score, score_breakdown))
                continue

            # Read frontmatter for importance + updated_at
            mem_file = read_markdown(self._resolve(path))
            if mem_file is None:
                reranked.append((path, rrf_score, score_breakdown))
                continue

            fm = mem_file.frontmatter
            importance = fm.importance

            # Days since access (from access_stats) or fallback to updated_at
            days_since_access = self._get_days_since_access(path, fm, now, day_seconds)

            # Decay factor: 0.5^(days / half_life)
            decay = 0.5 ** (days_since_access / half_life) if half_life > 0 else 1.0

            # Recency factor: based on updated_at (monotonic non-decreasing)
            recency = self._recency_factor(fm.updated_at, now, day_seconds)

            # Importance factor: (0.5 + importance) maps [0,1] → [0.5, 1.5]
            importance_factor = 0.5 + importance

            rerank_multiplier = importance_factor * decay * recency
            final_score = rrf_score * rerank_multiplier

            new_breakdown = dict(score_breakdown)
            new_breakdown["rerank"] = round(rerank_multiplier, 6)
            new_breakdown["rrf"] = round(rrf_score, 6)
            new_breakdown["final"] = round(final_score, 6)

            reranked.append((path, final_score, new_breakdown))

        reranked.sort(key=lambda x: x[1], reverse=True)
        return reranked

    def _get_days_since_access(
        self, path: str, fm, now: float, day_seconds: float
    ) -> float:
        """Get days since last access, falling back to updated_at."""
        if self.access_stats:
            stats = self.access_stats.get(self._to_rel(path))
            if stats and stats.get("last_accessed", 0) > 0:
                return max(0.0, (now - stats["last_accessed"]) / day_seconds)
        # Fallback: use updated_at from frontmatter
        updated = self._parse_date(fm.updated_at)
        if updated:
            return max(0.0, (now - updated) / day_seconds)
        return 0.0

    def _recency_factor(self, updated_at_str: str, now: float, day_seconds: float) -> float:
        """Compute recency factor: newer → higher (monotonic non-decreasing).

        Uses a gentle logistic-like scaling: 1.0 for very recent, approaching
        0.5 for very old. This ensures recency is a tiebreaker, not dominant.
        """
        updated = self._parse_date(updated_at_str)
        if not updated:
            return 0.75  # neutral for unknown
        days_old = max(0.0, (now - updated) / day_seconds)
        # Maps: 0 days → 1.0, 30 days → ~0.73, 90 days → ~0.55
        return 0.5 + 0.5 / (1.0 + days_old / 30.0)

    @staticmethod
    def _parse_date(date_str: str) -> float | None:
        """Parse a YYYY-MM-DD string to unix timestamp, or None."""
        if not date_str:
            return None
        try:
            d = date.fromisoformat(date_str.strip())
            return time.mktime(d.timetuple())
        except (ValueError, OSError):
            return None

    def _apply_daily_ttl_filter(
        self, paths_with_scores: list[tuple[str, float, dict]]
    ) -> list[tuple[str, float, dict]]:
        """Filter out daily cards past TTL that have been distilled into digest.

        A daily card is excluded when:
        1. Its created_at is older than memory_daily_ttl_days, AND
        2. It has inlinks from a digest/ source (meaning it's been distilled).

        Files and indexes are NOT modified — this is query-period only.
        """
        ttl_days = self.settings.memory_daily_ttl_days
        now = time.time()
        day_seconds = 86400.0

        filtered: list[tuple[str, float, dict]] = []
        for path, score, score_breakdown in paths_with_scores:
            # Only applies to daily/ paths
            rel = self._to_rel(path)
            if not rel.startswith("daily/"):
                filtered.append((path, score, score_breakdown))
                continue

            mem_file = read_markdown(self._resolve(path))
            if mem_file is None:
                filtered.append((path, score, score_breakdown))
                continue

            # Check age
            created = self._parse_date(mem_file.frontmatter.created_at)
            if created is None:
                filtered.append((path, score, score_breakdown))
                continue
            days_old = (now - created) / day_seconds
            if days_old <= ttl_days:
                filtered.append((path, score, score_breakdown))
                continue

            # Past TTL — check if distilled (has digest inlinks)
            has_digest_inlink = False
            if self.expander:
                for inlink in self.expander.get_inlinks(rel):
                    source = inlink.get("source", "")
                    if source.startswith("digest/"):
                        has_digest_inlink = True
                        break

            if has_digest_inlink:
                logger.debug(
                    "daily TTL: excluding %s (%.0f days old, distilled)", rel, days_old
                )
                continue

            filtered.append((path, score, score_breakdown))

        return filtered

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
