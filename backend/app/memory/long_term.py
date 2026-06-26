"""Long-term memory — embedding-based semantic memory with async PG persistence.

Ported from AGI-memory ``internal/memory/memory.py`` LongTerm class.
改造: psycopg2 → SQLAlchemy async session; threading.RLock → asyncio.Lock.
"""

import asyncio
import logging
import time
from typing import Any, Callable, List, Optional, TYPE_CHECKING

from sqlalchemy import delete, select

from app.config import Settings
from app.db.engine import get_db
from app.db.models import LongTermMemory
from app.memory.consolidation import (
    ConsolidationConfig,
    ConsolidationResult,
    Item,
    RecallFilter,
    cosine_similarity,
    tf_cosine,
    tokenize_zh,
)

if TYPE_CHECKING:
    from app.memory.graph_memory import GraphMemory

logger = logging.getLogger(__name__)


class LongTerm:
    """Long-term semantic memory backed by PostgreSQL + in-memory item list.

    All DB operations are async (SQLAlchemy async session). Consolidation
    algorithm (decay → dedup/merge → expire) is unchanged from AGI-memory.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.cfg = ConsolidationConfig.from_settings(settings)
        self.items: List[Item] = []
        self._embed_fn: Optional[Callable] = None
        self._last_consolidate_ts: float = 0.0
        self._items_since_last: int = 0
        self.graph_memory: Optional["GraphMemory"] = None
        self._next_id: int = 0
        self._lock = asyncio.Lock()

    def set_embed_fn(self, fn: Callable) -> None:
        self._embed_fn = fn

    def set_graph_memory(self, graph_memory: Optional["GraphMemory"]) -> None:
        self.graph_memory = graph_memory
        if graph_memory is not None and hasattr(graph_memory, "set_ltm"):
            try:
                graph_memory.set_ltm(self)
            except Exception:
                pass

    # ─── Storage ──────────────────────────────────────────────────────────────

    async def load_from_storage(self) -> None:
        """Restore all items from PostgreSQL."""
        async with get_db() as session:
            stmt = select(LongTermMemory).order_by(LongTermMemory.id)
            result = await session.execute(stmt)
            rows = result.scalars().all()

        self.items = []
        for r in rows:
            self.items.append(Item(
                content=r.content,
                importance=r.importance,
                embedding=list(r.embedding) if r.embedding else None,
                created_at=r.created_at,
                last_accessed=r.last_accessed,
                category=r.category or "",
                tags=list(r.tags) if r.tags else [],
                slot_hint=r.slot_hint or "",
                score=r.score or 0.0,
            ))
        for idx, item in enumerate(self.items):
            item.id = idx
        self._next_id = len(self.items)
        logger.info("Loaded %d long-term memories from PG", len(self.items))

        if self.graph_memory is not None:
            try:
                await self.graph_memory.bulk_index(self.items)
            except Exception as e:
                logger.warning("graph_memory.bulk_index failed: %s", e)

    async def add(self, content: str, importance: float = 0.5) -> None:
        """Add a new memory item, persist to PG, and sync to graph."""
        embedding = None
        if self._embed_fn:
            try:
                embedding = self._embed_fn(content)
            except Exception as e:
                logger.warning("Embedding failed: %s", e)

        now_ts = time.time()
        item = Item(
            content=content,
            importance=importance,
            embedding=embedding,
            id=self._next_id,
            created_at=now_ts,
            last_accessed=now_ts,
        )
        self._next_id += 1
        prior = list(self.items)
        self.items.append(item)
        self._items_since_last += 1

        # Persist to PG
        try:
            async with get_db() as session:
                row = LongTermMemory(
                    content=content,
                    importance=importance,
                    embedding=embedding,
                    created_at=now_ts,
                    last_accessed=now_ts,
                    category=item.category,
                    tags=item.tags,
                    slot_hint=item.slot_hint,
                    score=item.score,
                )
                session.add(row)
                await session.flush()
                if row.id:
                    item.id = row.id
                    if row.id >= self._next_id:
                        self._next_id = row.id + 1
        except Exception as e:
            logger.warning("PG save failed: %s", e)

        if self.graph_memory is not None:
            try:
                await self.graph_memory.add_to_graph(item, neighbors=prior[-50:])
            except Exception as e:
                logger.warning("graph_memory.add_to_graph failed: %s", e)

    # ─── Recall ───────────────────────────────────────────────────────────────

    async def recall(self, query: str, top_k: int = 3) -> List[Item]:
        async with self._lock:
            if not self.items:
                return []

            query_emb = None
            if self._embed_fn:
                try:
                    query_emb = self._embed_fn(query)
                except Exception as e:
                    logger.warning("Query embedding failed: %s", e)

            if not query_emb:
                return self.items[:top_k]

            scored = []
            for item in self.items:
                if item.embedding:
                    sim = cosine_similarity(query_emb, item.embedding)
                    score = sim * 0.7 + item.importance * 0.3
                    scored.append((item, score))

            scored.sort(key=lambda x: x[1], reverse=True)
            return [item for item, score in scored[:top_k] if score >= 0.4]

    async def recall_by_filter(
        self,
        query: str,
        query_embedding: Optional[List[float]],
        filt: Any,
    ) -> List[Item]:
        """Filtered semantic recall (async version)."""
        async with self._lock:
            if not self.items:
                return []

            min_score = float(getattr(filt, "min_score", 0.0) or 0.0)
            threshold = min_score if min_score > 0 else 0.4
            categories = list(getattr(filt, "categories", []) or [])
            require_tags = list(getattr(filt, "require_tags", []) or [])
            max_age_hours = int(getattr(filt, "max_age_hours", 0) or 0)
            top_k = int(getattr(filt, "top_k", 0) or 0)

            cat_set = set(categories) if categories else None
            now = time.time()
            use_tf = not query_embedding
            query_tokens = tokenize_zh(query) if use_tf else None

            candidates: List[Item] = []
            for item in self.items:
                if cat_set is not None:
                    item_cat = item.category or "general"
                    if item_cat not in cat_set:
                        continue
                if require_tags:
                    item_tags = set(item.tags or [])
                    if not all(t in item_tags for t in require_tags):
                        continue
                if max_age_hours > 0:
                    age_hours = (now - item.created_at) / 3600.0
                    if age_hours > float(max_age_hours):
                        continue

                use_tf_for_item = use_tf or (
                    not item.embedding
                    or (query_embedding is not None and len(query_embedding) != len(item.embedding))
                )
                if use_tf_for_item:
                    sim = tf_cosine(query_tokens, item.content)
                else:
                    sim = cosine_similarity(query_embedding, item.embedding)

                score = sim * 0.7 + item.importance * 0.3
                if score < threshold:
                    continue

                item.last_accessed = now
                copy = Item(
                    content=item.content,
                    importance=item.importance,
                    embedding=list(item.embedding) if item.embedding else None,
                    id=item.id,
                    created_at=item.created_at,
                    last_accessed=item.last_accessed,
                    category=item.category,
                    tags=list(item.tags),
                    slot_hint=item.slot_hint,
                    score=score,
                )
                candidates.append(copy)

            candidates.sort(key=lambda it: it.score, reverse=True)
            if top_k > 0 and len(candidates) > top_k:
                candidates = candidates[:top_k]
            return candidates

    # ─── Consolidation ────────────────────────────────────────────────────────

    def need_consolidation(self) -> bool:
        return self._items_since_last >= max(1, self.cfg.trigger_interval)

    async def consolidate(self) -> ConsolidationResult:
        """Three-phase consolidation: decay → dedup/merge → expire."""
        result = ConsolidationResult()
        async with self._lock:
            if len(self.items) <= 1:
                return result

            self._last_consolidate_ts = time.time()
            self._items_since_last = 0

            decay_rate = self.cfg.decay_rate
            sim_threshold = self.cfg.similarity_threshold
            dedup_threshold = self.cfg.dedup_threshold
            ttl_days = self.cfg.ttl_days
            min_importance = self.cfg.min_importance

            now = time.time()

            # Phase 1: per-item exponential decay
            for item in self.items:
                days = max(0.0, (now - item.created_at) / 86400.0)
                item.importance *= decay_rate ** days

            # Phase 2: pairwise dedup + merge
            removed = [False] * len(self.items)
            for i in range(len(self.items)):
                if removed[i]:
                    continue
                for j in range(i + 1, len(self.items)):
                    if removed[j]:
                        continue
                    item_i = self.items[i]
                    item_j = self.items[j]
                    sim = self._compute_similarity(
                        item_i.content, item_j.content,
                        item_i.embedding, item_j.embedding,
                    )
                    if sim >= dedup_threshold:
                        item_i.importance = max(item_i.importance, item_j.importance)
                        item_i.tags = list(dict.fromkeys(list(item_i.tags) + list(item_j.tags)))
                        item_i.last_accessed = now
                        removed[j] = True
                        result.deduped += 1
                        if item_j.id is not None:
                            result.delete_from_db.append(item_j.id)
                    elif sim >= sim_threshold:
                        merged = self._merge_pair(item_i, item_j, now)
                        self.items[i] = merged
                        removed[j] = True
                        result.merged += 1
                        if item_j.id is not None:
                            result.delete_from_db.append(item_j.id)
                        result.update_in_db.append(merged)

            # Phase 3: dual-condition expiry
            for idx in range(len(self.items)):
                if removed[idx]:
                    continue
                item = self.items[idx]
                days = max(0.0, (now - item.created_at) / 86400.0)
                if ttl_days > 0 and days > float(ttl_days) and item.importance < min_importance:
                    removed[idx] = True
                    result.expired += 1
                    if item.id is not None:
                        result.delete_from_db.append(item.id)

            self.items = [it for k, it in enumerate(self.items) if not removed[k]]

            # Graph centrality protection
            if self.graph_memory is not None and result.delete_from_db:
                try:
                    protected = await self.graph_memory.filter_protected(
                        list(result.delete_from_db), 3
                    ) or []
                    if protected:
                        protected_set = set(protected)
                        result.delete_from_db = [
                            rid for rid in result.delete_from_db if rid not in protected_set
                        ]
                except Exception as e:
                    logger.warning("graph_memory.filter_protected failed: %s", e)

            # Sync deletions/updates to PG
            if result.delete_from_db:
                try:
                    async with get_db() as session:
                        stmt = delete(LongTermMemory).where(
                            LongTermMemory.id.in_(result.delete_from_db)
                        )
                        await session.execute(stmt)
                except Exception as e:
                    logger.warning("PG consolidation delete failed: %s", e)

        logger.info(
            "Consolidation done: deduped=%d merged=%d expired=%d remaining=%d",
            result.deduped, result.merged, result.expired, len(self.items),
        )
        return result

    # ─── Internal helpers ─────────────────────────────────────────────────────

    def _merge_pair(self, item_i: Item, item_j: Item, now: float) -> Item:
        content = f"{item_i.content}；{item_j.content}"
        emb: Optional[List[float]] = None
        if (
            item_i.embedding
            and item_j.embedding
            and len(item_i.embedding) == len(item_j.embedding)
        ):
            wi = item_i.importance
            wj = item_j.importance
            total = wi + wj
            if total > 0:
                emb = [
                    (item_i.embedding[k] * wi + item_j.embedding[k] * wj) / total
                    for k in range(len(item_i.embedding))
                ]
            else:
                emb = list(item_i.embedding)
        elif item_i.embedding:
            emb = list(item_i.embedding)
        elif item_j.embedding:
            emb = list(item_j.embedding)

        tags = list(dict.fromkeys(list(item_i.tags) + list(item_j.tags)))
        category = item_i.category if item_i.category else item_j.category
        slot_hint = item_i.slot_hint if item_i.slot_hint else item_j.slot_hint

        return Item(
            content=content,
            importance=max(item_i.importance, item_j.importance),
            embedding=emb,
            id=item_i.id,
            created_at=min(item_i.created_at, item_j.created_at),
            last_accessed=now,
            category=category,
            tags=tags,
            slot_hint=slot_hint,
            score=item_i.score,
        )

    def _compute_similarity(
        self,
        a: str,
        b: str,
        emb_a: Optional[List[float]] = None,
        emb_b: Optional[List[float]] = None,
    ) -> float:
        if emb_a and emb_b and len(emb_a) == len(emb_b):
            return cosine_similarity(emb_a, emb_b)
        return tf_cosine(tokenize_zh(a), b)

    def last_id(self) -> int:
        if not self.items:
            return -1
        last = self.items[-1]
        return -1 if last.id is None else int(last.id)

    def snapshot(self) -> List[Item]:
        return [
            Item(
                content=it.content, importance=it.importance,
                embedding=it.embedding, id=it.id,
                created_at=it.created_at, last_accessed=it.last_accessed,
                category=it.category, tags=it.tags,
                slot_hint=it.slot_hint, score=it.score,
            )
            for it in self.items
        ]
