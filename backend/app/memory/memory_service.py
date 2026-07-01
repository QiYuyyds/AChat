"""MemoryService — assembly entry point for the memory subsystem.

Wires together ShortTerm, LongTerm, Preference, and GraphMemory.
Provides ``initialize()`` at startup and ``on_message_end()`` as a
post-conversation hook for memory writes, preference extraction,
and consolidation triggers.
"""

import asyncio
import logging
import time
from typing import Any, Callable, List, Optional

from app.config import Settings
from app.db.engine import get_db
from app.db.models import ChatHistory
from app.memory.consolidation import ConsolidationConfig, ConsolidationResult, Item
from app.memory.long_term import LongTerm
from app.memory.preference import Preference
from app.memory.short_term import ShortTerm
from app.memory.graph_memory import GraphMemory

logger = logging.getLogger(__name__)


class MemoryService:
    """Facade that owns and wires all memory components.

    Lifecycle:
        1. ``await svc.initialize()`` — load from storage, wire cross-references.
        2. ``await svc.on_message_end(role, content)`` — called after every
           conversation turn; handles STM add, LTM add, preference extraction,
           and optional consolidation.
        3. ``await svc.close()`` — clean shutdown.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

        # Short-term memory (pure in-memory deque)
        self.stm = ShortTerm(max_turns=settings.memory_short_term_max_turns)

        # Long-term memory (embedding + async PG)
        self.ltm = LongTerm(settings)

        # Preference extraction
        self.preference = Preference(user_id="default_user")

        # Graph memory (Neo4j, optional — driver injected later)
        self.graph_memory: Optional[GraphMemory] = None

        # Embedding function (injected by infrastructure factory)
        self._embed_fn: Optional[Callable] = None

        # LLM generate function (injected for memory extraction)
        self._generate_fn: Optional[Callable] = None

        self._initialized = False

    def set_embed_fn(self, fn: Callable) -> None:
        """Inject embedding function used by LTM for semantic recall."""
        self._embed_fn = fn
        self.ltm.set_embed_fn(fn)

    def set_generate_fn(self, fn: Callable) -> None:
        """Inject LLM generate function for memory extraction."""
        self._generate_fn = fn

    def set_neo4j_driver(self, driver) -> None:
        """Inject Neo4j AsyncDriver and wire GraphMemory ↔ LTM."""
        self.graph_memory = GraphMemory(
            settings=self.settings,
            driver=driver,
            sim_threshold=self.settings.memory_consolidation_similarity,
            ltm=self.ltm,
        )
        self.ltm.set_graph_memory(self.graph_memory)

    async def initialize(self) -> None:
        """Load persisted state from PG and wire cross-references."""
        if self._initialized:
            return

        # Load preferences
        try:
            await self.preference.load_from_storage()
        except Exception as e:
            logger.warning("Preference load failed: %s", e)

        # Load LTM items from PG
        try:
            await self.ltm.load_from_storage()
        except Exception as e:
            logger.warning("LTM load failed: %s", e)

        self._initialized = True
        logger.info(
            "MemoryService initialized: stm_max_turns=%d, ltm_items=%d, prefs=%d, graph=%s",
            self.settings.memory_short_term_max_turns,
            len(self.ltm.items),
            len(self.preference.data),
            "enabled" if self.graph_memory else "disabled",
        )

    async def on_message_end(self, role: str, content: str) -> None:
        """Post-conversation hook — called after each message exchange.

        1. Add to short-term memory (both user and assistant turns).
        2. Persist ChatHistory to PG (both roles).
        3. If user message: extract preferences, add to LTM, check consolidation.
        4. If assistant message: trigger LLM-based memory extraction (background).
        """
        # Always add to STM
        self.stm.add(role, content)

        # Persist to chat_history PG (both roles)
        try:
            async with get_db() as session:
                row = ChatHistory(
                    role=role,
                    content=content,
                    created_at=time.time(),
                )
                session.add(row)
        except Exception as e:
            logger.warning("ChatHistory PG write failed: %s", e)

        if role == "assistant":
            # Assistant message: trigger LLM-based memory extraction (background)
            if self._generate_fn and len(content) >= 10 and not self._is_trivial_reply(content):
                asyncio.create_task(self._safe_extract_memory(content))
            return

        if role != "user":
            return

        # Async fire-and-forget tasks for user messages
        tasks = []

        # Preference extraction
        tasks.append(self._safe_extract_preference(content))

        # LTM add (with importance heuristic)
        importance = self._estimate_importance(content)
        tasks.append(self._safe_ltm_add(content, importance))

        await asyncio.gather(*tasks)

        # Check if consolidation is needed
        if self.ltm.need_consolidation():
            asyncio.create_task(self._safe_consolidate())

    async def recall(self, query: str, top_k: Optional[int] = None) -> List[Item]:
        """Semantic recall from LTM."""
        k = top_k or self.settings.memory_long_term_top_k
        return await self.ltm.recall(query, top_k=k)

    async def graph_recall(self, item_id: int) -> List[int]:
        """Graph-based recall — expand from a seed memory."""
        if self.graph_memory is None:
            return []
        return await self.graph_memory.find_related(item_id)

    def get_stm_context(self) -> str:
        """Return short-term memory as formatted context string."""
        turns = self.stm.get()
        if not turns:
            return ""
        lines = []
        for msg in turns:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            prefix = "用户" if role == "user" else "助手"
            lines.append(f"{prefix}: {content}")
        return "\n".join(lines)

    def get_preference_context(self) -> str:
        """Return preference block for prompt injection."""
        return self.preference.build_context()

    async def close(self) -> None:
        """Clean shutdown."""
        if self.graph_memory is not None:
            await self.graph_memory.close()
        logger.info("MemoryService closed")

    # ─── Internal safe wrappers ───────────────────────────────────────────

    async def _safe_extract_preference(self, content: str) -> None:
        try:
            key, value, matched = await self.preference.extract_and_save(content)
            if matched:
                logger.info("Preference extracted: %s=%s", key, value)
        except Exception as e:
            logger.warning("Preference extraction failed: %s", e)

    async def _safe_ltm_add(self, content: str, importance: float) -> None:
        try:
            await self.ltm.add(content, importance)
        except Exception as e:
            logger.warning("LTM add failed: %s", e)

    async def _safe_consolidate(self) -> None:
        try:
            if self.graph_memory is not None:
                result = await self.graph_memory.graph_aware_consolidate()
            else:
                result = await self.ltm.consolidate()
            if result:
                logger.info(
                    "Consolidation: deduped=%d merged=%d expired=%d",
                    result.deduped, result.merged, result.expired,
                )
                await self._sync_consolidation_to_db(result)
        except Exception as e:
            logger.warning("Consolidation failed: %s", e)

    async def _sync_consolidation_to_db(self, result: ConsolidationResult) -> None:
        """Sync consolidation delete/update results to PostgreSQL.

        - ``delete_from_db``: batch DELETE using parameterised SQL.
        - ``update_in_db``: per-row UPDATE for each merged Item.
        Empty result → no SQL executed. Exceptions are logged, not raised.
        """
        if not result.delete_from_db and not result.update_in_db:
            return

        # Batch DELETE
        if result.delete_from_db:
            try:
                from sqlalchemy import delete as sa_delete
                from app.db.models import LongTermMemory
                async with get_db() as session:
                    stmt = sa_delete(LongTermMemory).where(
                        LongTermMemory.id.in_(result.delete_from_db)
                    )
                    await session.execute(stmt)
                logger.info(
                    "Consolidation DB sync: deleted %d rows",
                    len(result.delete_from_db),
                )
            except Exception as e:
                logger.warning("Consolidation DB delete failed: %s", e)

        # Per-row UPDATE for merged items
        if result.update_in_db:
            try:
                from sqlalchemy import update as sa_update
                from app.db.models import LongTermMemory
                for item in result.update_in_db:
                    if item.id is None:
                        continue
                    async with get_db() as session:
                        stmt = (
                            sa_update(LongTermMemory)
                            .where(LongTermMemory.id == item.id)
                            .values(
                                content=item.content,
                                importance=item.importance,
                                embedding=list(item.embedding) if item.embedding else None,
                                tags=list(item.tags) if item.tags else [],
                                category=item.category,
                                slot_hint=item.slot_hint,
                                last_accessed=item.last_accessed,
                            )
                        )
                        await session.execute(stmt)
                logger.info(
                    "Consolidation DB sync: updated %d rows",
                    len(result.update_in_db),
                )
            except Exception as e:
                logger.warning("Consolidation DB update failed: %s", e)

    async def _safe_extract_memory(self, content: str) -> None:
        """Extract memory facts from assistant reply using LLM (background task)."""
        try:
            from app.memory.memory_writer import extract_memory_from_reply
            await extract_memory_from_reply(
                generate_fn=self._generate_fn,
                embed_fn=self._embed_fn,
                ltm=self.ltm,
                content=content,
            )
        except Exception as e:
            logger.warning("Memory extraction failed: %s", e)

    @staticmethod
    def _is_trivial_reply(content: str) -> bool:
        """Check if assistant reply is trivial and should be skipped for extraction."""
        import re
        text = content.strip()
        if len(text) < 10:
            return True
        trivial_patterns = [
            r"^好的[。.！!]?\s*$",
            r"^没问题[。.！!]?\s*$",
            r"^OK[。.！!]?\s*$",
            r"^ok[。.！!]?\s*$",
            r"^明白[了]?[。.！!]?\s*$",
            r"^了解[。.！!]?\s*$",
            r"^收到[。.！!]?\s*$",
            r"^嗯[嗯]?[。.！!]?\s*$",
            r"^是的[。.！!]?\s*$",
            r"^好的.*没问题",
        ]
        return any(re.match(p, text) for p in trivial_patterns)

    @staticmethod
    def _estimate_importance(content: str) -> float:
        """Simple importance heuristic based on content length and keywords."""
        if not content:
            return 0.1
        base = 0.5
        # Longer messages are slightly more important
        length_bonus = min(0.3, len(content) / 1000.0)
        # Question marks suggest information-seeking (less important for memory)
        question_penalty = -0.1 if content.strip().endswith("？") or content.strip().endswith("?") else 0.0
        # Keywords that signal high importance
        important_keywords = ["记住", "重要", "必须", "永远", "不要忘记", "remember", "important", "always"]
        keyword_bonus = 0.2 if any(kw in content for kw in important_keywords) else 0.0
        return max(0.1, min(1.0, base + length_bonus + question_penalty + keyword_bonus))
