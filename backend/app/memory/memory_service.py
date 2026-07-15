"""MemoryService — assembly entry point for the memory subsystem.

Wires together ShortTerm, LongTerm, Preference, and GraphMemory.
Provides ``initialize()`` at startup and ``on_message_end()`` as a
post-conversation hook for memory writes, preference extraction,
and consolidation triggers.
"""

import asyncio
import logging
import time
from collections.abc import Callable

from app.config import Settings
from app.db.engine import get_db
from app.db.models import ChatHistory
from app.memory.consolidation import ConsolidationResult, Item
from app.memory.graph_memory import GraphMemory
from app.memory.long_term import LongTerm
from app.memory.preference import Preference
from app.memory.session_memory import SessionMemory
from app.memory.short_term import ShortTerm

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
        self.graph_memory: GraphMemory | None = None

        # Session memory (incremental conversation summary)
        self.session_memory = SessionMemory()

        # Embedding function (injected by infrastructure factory)
        self._embed_fn: Callable | None = None

        # LLM generate function (injected for memory extraction)
        self._generate_fn: Callable | None = None

        self._initialized = False

    def set_embed_fn(self, fn: Callable) -> None:
        """Inject embedding function used by LTM for semantic recall."""
        self._embed_fn = fn
        self.ltm.set_embed_fn(fn)

    def set_generate_fn(self, fn: Callable) -> None:
        """Inject LLM generate function for memory extraction."""
        self._generate_fn = fn
        self.session_memory.set_generate_fn(fn)

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

    async def on_message_end(
        self, role: str, content: str, agent_id: str = "",
        conversation_id: str = "",
        *,
        user_id: str | None = None,
    ) -> None:
        """Post-conversation hook — called after each message exchange.

        1. Add to short-term memory (both user and assistant turns).
        2. Persist ChatHistory to PG (both roles).
        3. If user message: extract preferences, add to LTM, check consolidation.
        4. If assistant message: trigger LLM-based memory extraction (background).
        5. If conversation_id provided: check Session Memory extraction trigger.

        Args:
            agent_id: When non-empty, extracted memories are written with
                ``scope='agent'`` so they are isolated per-agent.
            conversation_id: When non-empty, enables Session Memory extraction.
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
                    user_id=user_id,
                )
                session.add(row)
        except Exception as e:
            logger.warning("ChatHistory PG write failed: %s", e)

        if role == "assistant":
            # Assistant message: trigger LLM-based memory extraction (background)
            if self._generate_fn and len(content) >= 10 and not self._is_trivial_reply(content):
                asyncio.create_task(self._safe_extract_memory(content, agent_id, user_id=user_id))
            # Session Memory incremental extraction (background, after turn ends)
            if conversation_id:
                asyncio.create_task(self._safe_extract_session_memory(conversation_id))
            return

        if role != "user":
            return

        # User messages drive preference extraction only. Raw user text is no
        # longer dumped into LTM — LTM is fed exclusively by category-routed
        # extraction from assistant replies (see memory_writer).
        #
        # Single extraction pass: the LLM path is primary. Running the coarse
        # rule pass alongside it only produces duplicate keys (喜好 vs
        # 喜欢的音乐类型) that never reconcile, so the rules are a fallback for
        # when no LLM is available.
        if self._generate_fn and not self._is_trivial_reply(content):
            asyncio.create_task(self._safe_llm_extract_preference(content))
        else:
            await self._safe_extract_preference(content)

        # Check if consolidation is needed
        if self.ltm.need_consolidation():
            asyncio.create_task(self._safe_consolidate())

    async def recall(
        self, query: str, top_k: int | None = None, agent_id: str = "",
        *, user_id: str | None = None,
    ) -> list[Item]:
        """Semantic recall from LTM."""
        from app.observability import start_span
        k = top_k or self.settings.memory_long_term_top_k
        with start_span("memory.recall", source="ltm"):
            return await self.ltm.recall(query, top_k=k, agent_id=agent_id, user_id=user_id)

    async def graph_recall(self, item_id: int) -> list[int]:
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

    def get_preference_context(self, *, user_id: str | None = None) -> str:
        """Return preference block for prompt injection.

        For multi-user: when user_id is provided and differs from the default,
        returns a synchronous snapshot from the in-memory cache if it matches,
        or an empty string (async load not supported in sync context).
        """
        if user_id is not None and user_id != self.preference.user_id:
            # Different user — return empty context (async load not possible here)
            return ""
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

        # Layer 3 dedup: LLM-based preference consolidation when entries exceed threshold
        if len(self.preference.data) > 15:
            await self._consolidate_preferences()

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
                                scope=item.scope,
                                agent_id=item.agent_id or None,
                            )
                        )
                        await session.execute(stmt)
                logger.info(
                    "Consolidation DB sync: updated %d rows",
                    len(result.update_in_db),
                )
            except Exception as e:
                logger.warning("Consolidation DB update failed: %s", e)

    async def _consolidate_preferences(self) -> None:
        """LLM-based preference consolidation (Layer 3 dedup).

        Sends all ``preference.data`` to the LLM with a merge prompt, receives
        a merged dict, batch-updates the Preference table, and deletes removed
        keys from both in-memory cache and PG.
        """
        if not self._generate_fn or not self.preference.data:
            return
        try:
            import json

            from app.memory.memory_writer import _PREFERENCE_MERGE_PROMPT

            prefs_json = json.dumps(self.preference.data, ensure_ascii=False)
            raw = await asyncio.to_thread(
                self._generate_fn, _PREFERENCE_MERGE_PROMPT, prefs_json,
            )
            raw = (raw or "").strip()
            # Strip markdown code fences
            if raw.startswith("```"):
                raw = raw.split("```", 1)[-1]
                if raw.startswith("json"):
                    raw = raw[4:]
                if raw.endswith("```"):
                    raw = raw[:-3]
                raw = raw.strip()

            merged = json.loads(raw)
            if not isinstance(merged, dict) or not merged:
                return

            old_keys = set(self.preference.data.keys())
            new_keys = set(str(k) for k in merged.keys())
            removed_keys = old_keys - new_keys

            # Save merged preferences (updates existing, inserts new)
            await self.preference.save_batch(
                {str(k): str(v) for k, v in merged.items()},
                source="extracted",
            )

            # Delete removed keys from in-memory and PG
            if removed_keys:
                with self.preference._lock:
                    for k in removed_keys:
                        self.preference.preferences.pop(k, None)
                try:
                    from sqlalchemy import delete as sa_delete

                    from app.db.models import UserPreference
                    async with get_db() as session:
                        stmt = sa_delete(UserPreference).where(
                            (UserPreference.user_id == self.preference.user_id)
                            & (UserPreference.key.in_(list(removed_keys)))
                        )
                        await session.execute(stmt)
                except Exception as e:
                    logger.warning("Preference consolidation delete failed: %s", e)

            logger.info(
                "Preference consolidation: merged %d → %d keys (removed %d duplicates)",
                len(old_keys), len(new_keys), len(removed_keys),
            )
        except Exception as e:
            logger.warning("Preference consolidation failed: %s", e)

    async def _safe_extract_memory(self, content: str, agent_id: str = "", *, user_id: str | None = None) -> None:
        """Extract memory facts from assistant reply using LLM (background task)."""
        try:
            from app.memory.memory_writer import extract_memory_from_reply
            await extract_memory_from_reply(
                generate_fn=self._generate_fn,
                embed_fn=self._embed_fn,
                ltm=self.ltm,
                content=content,
                preference=self.preference,
                existing_keys=list(self.preference.data.keys()),
                agent_id=agent_id,
                user_id=user_id,
            )
        except Exception as e:
            logger.warning("Memory extraction failed: %s", e)

    async def _safe_extract_session_memory(self, conversation_id: str) -> None:
        """Check and run Session Memory extraction (background task)."""
        try:
            if await self.session_memory.should_extract(conversation_id):
                await self.session_memory.extract(conversation_id)
        except Exception as e:
            logger.warning("Session Memory extraction failed: %s", e)

    async def _safe_llm_extract_preference(self, content: str) -> None:
        """LLM overlay: refine rule-based preferences with a more precise extraction.

        Runs after the synchronous rule pass so the LLM's values overwrite the
        coarse rule output via ``save_batch``. Any failure degrades silently —
        the rule-based result already persisted is kept as-is.
        """
        try:
            from app.memory.memory_writer import extract_preferences
            prefs = await extract_preferences(
                self._generate_fn,
                content,
                existing_keys=list(self.preference.data.keys()),
            )
            if prefs:
                await self.preference.save_batch(prefs, source="extracted")
                logger.info("LLM preference overlay: %d keys", len(prefs))
        except Exception as e:
            logger.warning("LLM preference extraction failed: %s", e)

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
