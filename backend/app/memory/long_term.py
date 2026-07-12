"""Long-term memory — embedding-based semantic memory with async PG persistence.

Ported from AGI-memory ``internal/memory/memory.py`` LongTerm class.
改造: psycopg2 → SQLAlchemy async session; threading.RLock → asyncio.Lock.
"""

import asyncio
import logging
import time
from typing import Any, Callable, List, Optional, Set, TYPE_CHECKING

from sqlalchemy import delete, select, update

from app.config import Settings
from app.db.engine import get_db
from app.db.models import LongTermMemory
from app.db.models import MemoryEdge as _MemoryEdge
from app.db.models import MemoryNode as _MemoryNode
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

        now_ts = time.time()
        self.items = []
        for r in rows:
            self.items.append(Item(
                content=r.content,
                importance=r.importance,
                embedding=list(r.embedding) if r.embedding else None,
                id=r.id,
                created_at=r.created_at,
                last_accessed=r.last_accessed,
                category=r.category or "",
                tags=list(r.tags) if r.tags else [],
                slot_hint=r.slot_hint or "",
                score=r.score or 0.0,
                scope=getattr(r, "scope", None) or "global",
                agent_id=getattr(r, "agent_id", None) or "",
                # Reset decay checkpoint to load time — persisted importance is
                # already decayed; don't retroactively re-decay the idle period.
                last_decay_ts=now_ts,
            ))
        self._next_id = max((r.id for r in rows), default=-1) + 1
        logger.info("Loaded %d long-term memories from PG", len(self.items))

        if self.graph_memory is not None:
            try:
                await self.graph_memory.bulk_index(self.items)
            except Exception as e:
                logger.warning("graph_memory.bulk_index failed: %s", e)

    async def add(
        self,
        content: str,
        importance: float = 0.5,
        scope: str = "global",
        agent_id: str = "",
    ) -> None:
        """Add a new memory item, persist to PG, and sync to graph."""
        # Embedding is blocking I/O — run it off the event loop, outside the lock.
        embedding = None
        if self._embed_fn:
            try:
                embedding = await asyncio.to_thread(self._embed_fn, content)
            except Exception as e:
                logger.warning("Embedding failed: %s", e)

        now_ts = time.time()
        # Guard in-memory bookkeeping + id assignment against concurrent writers.
        async with self._lock:
            item = Item(
                content=content,
                importance=importance,
                embedding=embedding,
                id=self._next_id,
                created_at=now_ts,
                last_accessed=now_ts,
                scope=scope,
                agent_id=agent_id,
                last_decay_ts=now_ts,
            )
            self._next_id += 1
            prior = list(self.items)
            self.items.append(item)
            self._items_since_last += 1

            # Persist to PG (fast local write kept inside the lock so the id
            # backfill stays atomic with the _next_id bump above).
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
                        scope=scope,
                        agent_id=agent_id or None,
                    )
                    session.add(row)
                    await session.flush()
                    if row.id:
                        item.id = row.id
                        if row.id >= self._next_id:
                            self._next_id = row.id + 1
            except Exception as e:
                logger.warning("PG save failed: %s", e)

        # Graph sync (Neo4j I/O) runs outside the lock.
        if self.graph_memory is not None:
            try:
                await self.graph_memory.add_to_graph(item, neighbors=prior[-50:])
            except Exception as e:
                logger.warning("graph_memory.add_to_graph failed: %s", e)

    async def store_classified(
        self,
        content: str,
        importance: float,
        emb: Optional[List[float]],
        category: str,
        tags: Optional[List[str]],
        slot_hint: str,
        scope: str = "global",
        agent_id: str = "",
    ) -> bool:
        """Schema-driven write with cosine dedup against existing items.

        If embedding cosine similarity >= 0.95 against an existing item
        **in the same (scope, agent_id) group**, update that item's
        importance/tags/category/slot_hint (no new row).
        Otherwise insert a new row via the full add path.

        Returns True if a new item was inserted, False if dedup hit (update only).
        """
        tags = list(tags or [])
        category = category or ""
        slot_hint = slot_hint or ""

        dedup_threshold = self.cfg.dedup_threshold  # default 0.95

        # In-memory dedup/insert bookkeeping is guarded by the lock; graph I/O
        # (Neo4j) is deferred until after the lock is released.
        target: Optional[Item] = None
        new_item: Optional[Item] = None
        prior: List[Item] = []

        async with self._lock:
            # Cosine dedup: if highly similar to existing item in the same scope
            # group, update instead of insert
            if emb and self.items:
                best_idx = -1
                best_sim = -1.0
                for idx, existing in enumerate(self.items):
                    if existing.scope != scope or existing.agent_id != agent_id:
                        continue
                    if not existing.embedding or len(existing.embedding) != len(emb):
                        continue
                    sim = cosine_similarity(emb, existing.embedding)
                    if sim > best_sim:
                        best_sim = sim
                        best_idx = idx

                if best_idx >= 0 and best_sim >= dedup_threshold:
                    target = self.items[best_idx]
                    # Update fields on existing item
                    if importance > target.importance:
                        target.importance = importance
                    if tags:
                        merged_tags: List[str] = []
                        seen = set()
                        for t in list(target.tags) + list(tags):
                            if not t or t in seen:
                                continue
                            seen.add(t)
                            merged_tags.append(t)
                        target.tags = merged_tags
                    if category and (target.category == "" or target.category == "general"):
                        target.category = category
                    if slot_hint and target.slot_hint == "":
                        target.slot_hint = slot_hint
                    target.last_accessed = time.time()

                    # PG UPDATE
                    if target.id is not None:
                        try:
                            async with get_db() as session:
                                stmt = (
                                    update(LongTermMemory)
                                    .where(LongTermMemory.id == target.id)
                                    .values(
                                        importance=target.importance,
                                        tags=target.tags,
                                        category=target.category,
                                        slot_hint=target.slot_hint,
                                        last_accessed=target.last_accessed,
                                    )
                                )
                                await session.execute(stmt)
                        except Exception as e:
                            logger.warning("store_classified PG update failed: %s", e)

            # Dedup miss — full insert path
            if target is None:
                now_ts = time.time()
                new_item = Item(
                    content=content,
                    importance=importance,
                    embedding=list(emb) if emb else None,
                    id=self._next_id,
                    created_at=now_ts,
                    last_accessed=now_ts,
                    category=category if category else "general",
                    tags=tags,
                    slot_hint=slot_hint,
                    score=0.0,
                    scope=scope,
                    agent_id=agent_id,
                    last_decay_ts=now_ts,
                )
                self._next_id += 1
                prior = list(self.items)
                self.items.append(new_item)
                self._items_since_last += 1

                # PG INSERT
                try:
                    async with get_db() as session:
                        row = LongTermMemory(
                            content=content,
                            importance=importance,
                            embedding=list(emb) if emb else None,
                            created_at=now_ts,
                            last_accessed=now_ts,
                            category=new_item.category,
                            tags=new_item.tags,
                            slot_hint=new_item.slot_hint,
                            score=new_item.score,
                            scope=scope,
                            agent_id=agent_id or None,
                        )
                        session.add(row)
                        await session.flush()
                        if row.id:
                            new_item.id = row.id
                            if row.id >= self._next_id:
                                self._next_id = row.id + 1
                except Exception as e:
                    logger.warning("store_classified PG save failed: %s", e)

        # Graph sync runs outside the lock.
        if self.graph_memory is not None:
            try:
                if target is not None:
                    await self.graph_memory.update_node(target)
                elif new_item is not None:
                    await self.graph_memory.add_to_graph(new_item, neighbors=prior[-50:])
            except Exception as e:
                logger.warning("store_classified graph sync failed: %s", e)

        return target is None

    # ─── Recall ───────────────────────────────────────────────────────────────

    async def recall(
        self, query: str, top_k: int = 3, agent_id: str = "",
    ) -> List[Item]:
        async with self._lock:
            if not self.items:
                return []

            query_emb = None
            if self._embed_fn:
                try:
                    query_emb = await asyncio.to_thread(self._embed_fn, query)
                except Exception as e:
                    logger.warning("Query embedding failed: %s", e)

            if not query_emb:
                # No embedding: return agent-scoped first, then global fill
                if agent_id:
                    agent_items = [
                        it for it in self.items
                        if it.scope == "agent" and it.agent_id == agent_id
                    ]
                    global_items = [
                        it for it in self.items if it.scope == "global"
                    ]
                    remaining = max(0, top_k - len(agent_items))
                    return agent_items[:top_k] + global_items[:remaining]
                return self.items[:top_k]

            # Phase 1: agent-scoped recall
            if agent_id:
                agent_pool = [
                    it for it in self.items
                    if it.scope == "agent" and it.agent_id == agent_id
                ]
            else:
                agent_pool = []

            agent_scored = []
            for item in agent_pool:
                if item.embedding:
                    sim = cosine_similarity(query_emb, item.embedding)
                    score = sim * 0.7 + item.importance * 0.3
                    agent_scored.append((item, score))

            agent_scored.sort(key=lambda x: x[1], reverse=True)
            agent_results = [
                item for item, score in agent_scored[:top_k] if score >= 0.4
            ]

            # Phase 2: global recall (fill remaining slots)
            remaining = max(0, top_k - len(agent_results))
            global_results: List[Item] = []
            if remaining > 0:
                global_pool = [
                    it for it in self.items if it.scope == "global"
                ]
                global_scored = []
                for item in global_pool:
                    if item.embedding:
                        sim = cosine_similarity(query_emb, item.embedding)
                        score = sim * 0.7 + item.importance * 0.3
                        global_scored.append((item, score))

                global_scored.sort(key=lambda x: x[1], reverse=True)
                global_results = [
                    item for item, score in global_scored[:remaining]
                    if score >= 0.4
                ]

            seed_items = agent_results + global_results
            return await self._graph_expand(seed_items, top_k, agent_id=agent_id)

    async def recall_by_filter(
        self,
        query: str,
        query_embedding: Optional[List[float]],
        filt: Any,
        agent_id: str = "",
    ) -> List[Item]:
        """Filtered semantic recall (async version).

        When ``agent_id`` is provided, recall first searches agent-scoped
        memories, then fills remaining slots from global memories.
        """
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

            def _score_item(item: Item) -> Optional[float]:
                """Score a single item; return None if it fails filters."""
                if cat_set is not None:
                    item_cat = item.category or "general"
                    if item_cat not in cat_set:
                        return None
                if require_tags:
                    item_tags = set(item.tags or [])
                    if not all(t in item_tags for t in require_tags):
                        return None
                if max_age_hours > 0:
                    age_hours = (now - item.created_at) / 3600.0
                    if age_hours > float(max_age_hours):
                        return None

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
                    return None
                return score

            def _make_copy(item: Item, score: float) -> Item:
                item.last_accessed = now
                return Item(
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
                    scope=item.scope,
                    agent_id=item.agent_id,
                )

            # Phase 1: agent-scoped recall
            agent_candidates: List[Item] = []
            if agent_id:
                for item in self.items:
                    if item.scope != "agent" or item.agent_id != agent_id:
                        continue
                    score = _score_item(item)
                    if score is not None:
                        agent_candidates.append(_make_copy(item, score))
                agent_candidates.sort(key=lambda it: it.score, reverse=True)
                if top_k > 0 and len(agent_candidates) > top_k:
                    agent_candidates = agent_candidates[:top_k]

            # Phase 2: global recall (fill remaining slots)
            remaining = top_k - len(agent_candidates) if top_k > 0 else 0
            global_candidates: List[Item] = []
            if remaining > 0 or top_k == 0:
                for item in self.items:
                    if item.scope != "global":
                        continue
                    score = _score_item(item)
                    if score is not None:
                        global_candidates.append(_make_copy(item, score))
                global_candidates.sort(key=lambda it: it.score, reverse=True)
                if remaining > 0 and len(global_candidates) > remaining:
                    global_candidates = global_candidates[:remaining]
                elif top_k > 0 and len(global_candidates) > top_k:
                    global_candidates = global_candidates[:top_k]

            candidates = agent_candidates + global_candidates
            return await self._graph_expand(candidates, top_k, cat_set, agent_id=agent_id)

    # ─── Graph expansion ───────────────────────────────────────────────────

    async def _graph_expand(
        self,
        seed_items: List[Item],
        top_k: int,
        cat_set: Optional[Set[str]] = None,
        agent_id: str = "",
    ) -> List[Item]:
        """1-hop graph expansion from seed items.

        For each seed item, calls ``graph_memory.find_related(id)`` to discover
        neighbouring mem_ids.  Expanded items receive a fixed score of 0.45,
        are merged with the seeds, sorted by score descending, and truncated
        to *top_k*.

        If *cat_set* is given, expanded items whose category is not in the set
        are excluded.

        If *agent_id* is given, expanded items are restricted to the same
        ``(scope, agent_id)`` group as the seeds — no cross-agent expansion.

        On any failure the method logs a warning and returns only the seeds.
        """
        if not self.graph_memory or not seed_items:
            return seed_items

        seed_id_set = {it.id for it in seed_items if it.id is not None}
        if not seed_id_set:
            return seed_items

        try:
            expanded_ids: Set[int] = set()
            for sid in seed_id_set:
                related = await self.graph_memory.find_related(sid, agent_id=agent_id)
                expanded_ids.update(related or [])
        except Exception as e:
            logger.warning("graph_memory.find_related failed during recall: %s", e)
            return seed_items

        extras: List[Item] = []
        for it in self.items:
            if it.id is None or it.id not in expanded_ids or it.id in seed_id_set:
                continue
            if cat_set is not None and (it.category or "general") not in cat_set:
                continue
            if agent_id and it.scope == "agent" and it.agent_id != agent_id:
                continue
            extra = Item(
                content=it.content,
                importance=it.importance,
                embedding=list(it.embedding) if it.embedding else None,
                id=it.id,
                created_at=it.created_at,
                last_accessed=it.last_accessed,
                category=it.category,
                tags=list(it.tags),
                slot_hint=it.slot_hint,
                score=0.45,
                scope=it.scope,
                agent_id=it.agent_id,
            )
            extras.append(extra)

        all_items = list(seed_items) + extras
        all_items.sort(key=lambda x: x.score, reverse=True)
        if top_k > 0 and len(all_items) > top_k:
            all_items = all_items[:top_k]
        return all_items

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

            # Phase 1: incremental exponential decay since each item's last
            # decay checkpoint (not since creation) so repeated runs don't
            # compound-decay the same item. Checkpoint advances to now.
            for item in self.items:
                checkpoint = item.last_decay_ts or item.created_at
                days = max(0.0, (now - checkpoint) / 86400.0)
                if days > 0:
                    item.importance *= decay_rate ** days
                item.last_decay_ts = now

            # Phase 2: pairwise dedup + merge (within same scope+agent_id group only)
            removed = [False] * len(self.items)
            for i in range(len(self.items)):
                if removed[i]:
                    continue
                for j in range(i + 1, len(self.items)):
                    if removed[j]:
                        continue
                    item_i = self.items[i]
                    item_j = self.items[j]
                    # Never dedup/merge across scope or agent boundaries
                    if item_i.scope != item_j.scope or item_i.agent_id != item_j.agent_id:
                        continue
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
                        merged.last_decay_ts = now
                        self.items[i] = merged
                        removed[j] = True
                        result.merged += 1
                        if item_j.id is not None:
                            result.delete_from_db.append(item_j.id)

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

            # Persist decayed/merged/deduped importance for all survivors so the
            # in-memory state and PG stay consistent (survivors exclude removed
            # items, so there is no overlap with delete_from_db).
            result.update_in_db = [it for it in self.items if it.id is not None]

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
            scope=item_i.scope,
            agent_id=item_i.agent_id,
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
                scope=it.scope, agent_id=it.agent_id,
            )
            for it in self.items
        ]

    async def filter_by_category(
        self, categories: List[str], limit: int
    ) -> List[Item]:
        """Return in-memory items whose category matches any of *categories*.

        Results are ordered by importance descending and limited to *limit*.
        This mirrors the AGI-memory ``LongTermCategoryFilter`` Protocol.
        """
        if not categories or not self.items:
            return []
        cat_set = set(categories)
        matched = [
            it for it in self.items
            if (it.category or "general") in cat_set
        ]
        matched.sort(key=lambda x: x.importance, reverse=True)
        if limit > 0 and len(matched) > limit:
            matched = matched[:limit]
        return matched

    # ─── Management API (user-facing CRUD) ───────────────────────────────────

    async def update_item(
        self,
        memory_id: int,
        content: Optional[str] = None,
        importance: Optional[float] = None,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> Optional[Item]:
        """Update a single LTM item in memory + PG.

        Returns the updated Item, or None if not found.
        Only non-None fields are updated. Does NOT recompute embedding —
        the API layer handles that asynchronously when content changes.
        """
        async with self._lock:
            target: Optional[Item] = None
            for it in self.items:
                if it.id == memory_id:
                    target = it
                    break
            if target is None:
                return None

            if content is not None:
                target.content = content
            if importance is not None:
                target.importance = importance
            if category is not None:
                target.category = category
            if tags is not None:
                target.tags = list(tags)
            target.last_accessed = time.time()

            try:
                async with get_db() as session:
                    stmt = (
                        update(LongTermMemory)
                        .where(LongTermMemory.id == memory_id)
                        .values(
                            content=target.content,
                            importance=target.importance,
                            category=target.category,
                            tags=target.tags,
                            last_accessed=target.last_accessed,
                        )
                    )
                    await session.execute(stmt)
            except Exception as e:
                logger.warning("LTM update_item PG write failed (id=%s): %s", memory_id, e)

            return target

    async def update_embedding(self, memory_id: int, embedding: Optional[List[float]]) -> None:
        """Persist a recomputed embedding to PG and the in-memory Item."""
        async with self._lock:
            for it in self.items:
                if it.id == memory_id:
                    it.embedding = list(embedding) if embedding else None
                    break
        try:
            async with get_db() as session:
                stmt = (
                    update(LongTermMemory)
                    .where(LongTermMemory.id == memory_id)
                    .values(embedding=list(embedding) if embedding else None)
                )
                await session.execute(stmt)
        except Exception as e:
            logger.warning("LTM update_embedding PG write failed (id=%s): %s", memory_id, e)

    async def delete_item(self, memory_id: int) -> bool:
        """Delete a single LTM item from memory, PG, and GraphMemory.

        Returns True if the item was found and deleted, False otherwise.
        """
        target: Optional[Item] = None
        async with self._lock:
            for idx, it in enumerate(self.items):
                if it.id == memory_id:
                    target = it
                    self.items.pop(idx)
                    break
            if target is None:
                return False

        # Delete PG mirror tables (MemoryNode + MemoryEdge)
        try:
            async with get_db() as session:
                await session.execute(
                    delete(_MemoryNode).where(_MemoryNode.mem_id == memory_id)
                )
                await session.execute(
                    delete(_MemoryEdge).where(
                        (_MemoryEdge.from_id == memory_id)
                        | (_MemoryEdge.to_id == memory_id)
                    )
                )
                await session.execute(
                    delete(LongTermMemory).where(LongTermMemory.id == memory_id)
                )
        except Exception as e:
            logger.warning("LTM delete_item PG delete failed (id=%s): %s", memory_id, e)

        # Delete from Neo4j (if available)
        if self.graph_memory is not None:
            try:
                await self.graph_memory.delete_from_graph(memory_id)
            except Exception as e:
                logger.warning("LTM delete_item graph delete failed (id=%s): %s", memory_id, e)

        return True
