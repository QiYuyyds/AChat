"""MemoryService — file-native memory pipeline orchestrator.

Wires together:
  - File-native pipeline: auto_memory → auto_index → auto_dream → proactive
  - PG-backed Preference store (preserved unchanged)
  - SessionMemory (context compaction, preserved unchanged)

Provides ``initialize()`` at startup and ``on_message_end()`` as a
post-conversation hook for memory writes, preference extraction,
and pipeline triggers.
"""

import asyncio
import logging
import re
import time
from collections.abc import Callable

from app.config import Settings
from app.db.engine import get_db
from app.db.models import ChatHistory
from app.memory.file_store.file_catalog import FileCatalog
from app.memory.file_store.workspace import MemoryWorkspace
from app.memory.pipeline.auto_dream import AutoDream
from app.memory.pipeline.auto_index import AutoIndex
from app.memory.pipeline.auto_memory import AutoMemory
from app.memory.pipeline.proactive import Proactive
from app.memory.preference import Preference
from app.memory.search.bm25_index import BM25Index
from app.memory.search.chunker import MarkdownChunker
from app.memory.search.hybrid_search import HybridSearch, SearchResult
from app.memory.search.node_search import NodeSearch
from app.memory.search.vector_index import VectorIndex
from app.memory.search.wikilink_expander import WikilinkExpander
from app.memory.session_memory import SessionMemory

logger = logging.getLogger(__name__)


class MemoryService:
    """Facade that owns and wires all memory components.

    Lifecycle:
        1. ``await svc.initialize()`` — init workspace, build indexes, wire pipeline.
        2. ``await svc.on_message_end(role, content)`` — called after every
           conversation turn; triggers auto_memory, preference extraction,
           session/ dual-write, and auto_dream threshold check.
        3. ``await svc.close()`` — clean shutdown (close SQLite indexes).
    """

    def __init__(self, settings: Settings):
        self.settings = settings

        # File-native workspace
        self.workspace = MemoryWorkspace(settings)

        # Search indexes
        self.bm25 = BM25Index(self.workspace.metadata_dir / "bm25.db")
        self.wikilink_expander = WikilinkExpander(self.workspace.metadata_dir / "wikilinks.db")
        self.file_catalog = FileCatalog(self.workspace.metadata_dir / "catalog.db")
        self.vector_index = VectorIndex(self.workspace.metadata_dir / "vectors.db")
        self.chunker = MarkdownChunker(
            chunk_size=settings.memory_chunk_size,
            min_chunk_size=settings.memory_chunk_min_size,
        )

        # Pipeline components
        self.auto_memory = AutoMemory(self.workspace, self.file_catalog, self.wikilink_expander)
        self.auto_index = AutoIndex(
            self.workspace, self.bm25, self.wikilink_expander, self.file_catalog,
            vector_index=self.vector_index,
            chunker=self.chunker,
        )
        self.node_search = NodeSearch(self.bm25, self.wikilink_expander, self.workspace)
        self.auto_dream = AutoDream(self.workspace, self._build_search(), self.node_search, self.file_catalog)
        self.proactive = Proactive(self.workspace)

        # Hybrid search (built after indexes are initialized)
        self._search: HybridSearch | None = None

        # Preference facade retained for callers that still touch .preference;
        # runtime extract/recall always scopes by the real user_id argument.
        self.preference = Preference(user_id="default_user")

        # Session memory (context compaction, preserved)
        self.session_memory = SessionMemory()

        # LLM generate function (injected for memory extraction)
        self._generate_fn: Callable | None = None

        # Cache last user message per conversation for full-conversation extraction
        self._last_user_msg: dict[str, str] = {}

        self._initialized = False

    def _build_search(self) -> HybridSearch:
        return HybridSearch(
            self.settings, self.bm25, self.wikilink_expander, self.workspace.root,
            vector_index=self.vector_index,
        )

    @staticmethod
    def _pref_for(user_id: str | None) -> Preference | None:
        """Build a Preference store scoped to the real user. None if missing."""
        uid = (user_id or "").strip()
        if not uid:
            return None
        return Preference(user_id=uid)

    def set_embed_fn(self, fn: Callable[[str], list[float]] | None) -> None:
        """Inject embedding function for vector search and indexing."""
        if self._search:
            self._search.set_embed_fn(fn)
        self.auto_index.set_embed_fn(fn)

    def set_generate_fn(self, fn: Callable) -> None:
        """Inject LLM generate function for memory extraction and pipeline."""
        self._generate_fn = fn
        self.auto_memory.set_generate_fn(fn)
        self.auto_dream.set_generate_fn(fn)
        self.session_memory.set_generate_fn(fn)

    async def initialize(self) -> None:
        """Initialize workspace, indexes, and preference store."""
        if self._initialized:
            return

        # Initialize file workspace
        self.workspace.initialize()

        # Initialize SQLite indexes
        self.bm25.initialize()
        self.wikilink_expander.initialize()
        self.file_catalog.initialize()
        self.vector_index.initialize()

        # Reconcile file catalog with filesystem
        try:
            self.file_catalog.reconcile(self.workspace.daily_dir, self.workspace.digest_dir)
        except Exception as e:
            logger.warning("FileCatalog reconcile failed: %s", e)

        # Build hybrid search (now that indexes are ready)
        self._search = self._build_search()

        # Full reindex on startup (in thread to avoid blocking event loop during startup)
        try:
            count = await asyncio.to_thread(self.auto_index.full_reindex)
            logger.info("MemoryService startup reindex: %d files", count)
        except Exception as e:
            logger.warning("Startup reindex failed: %s", e)

        # Preferences are loaded per-user at extract/recall time; no global preload.
        self._initialized = True
        logger.info(
            "MemoryService initialized: workspace=%s, indexed=%d",
            self.workspace.root,
            self.bm25.count(),
        )

    async def on_message_end(
        self, role: str, content: str, agent_id: str = "",
        conversation_id: str = "",
        *,
        user_id: str | None = None,
    ) -> None:
        """Post-conversation hook — called after each message exchange.

        1. Persist ChatHistory to PG (both roles).
        2. If user message: cache for later auto_memory; extract preferences.
        3. If assistant message: trigger auto_memory + session memory extraction.
        """
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
            # Trigger auto_memory (background)
            if self._generate_fn and len(content) >= 10 and not self._is_trivial_reply(content):
                user_msg = self._last_user_msg.pop(conversation_id, "")
                asyncio.create_task(self._safe_auto_memory(
                    user_msg, content, agent_id, conversation_id,
                ))
            # Session Memory incremental extraction (background)
            if conversation_id:
                asyncio.create_task(self._safe_extract_session_memory(conversation_id))
            return

        if role != "user":
            return

        # Cache user message for auto_memory (retrieved when assistant reply arrives)
        if conversation_id:
            self._last_user_msg[conversation_id] = content

        # Preference extraction always scopes to the real conversation user.
        if self._generate_fn and not self._is_trivial_reply(content):
            asyncio.create_task(self._safe_llm_extract_preference(content, user_id=user_id))
        else:
            await self._safe_extract_preference(content, user_id=user_id)

    async def recall(
        self,
        query: str,
        top_k: int | None = None,
        agent_id: str = "",
        *,
        bucket: str | None = None,
        user_id: str | None = None,
    ) -> list[SearchResult]:
        """File-native memory recall via hybrid search."""
        from app.observability import start_span
        k = top_k or self.settings.memory_search_top_k
        with start_span("memory.recall", source="hybrid_search"):
            if self._search is None:
                logger.warning("recall: search not initialized")
                return []
            results = await self._search.search(
                query,
                top_k=k,
                agent_id=agent_id or None,
                bucket=bucket or None,
            )
            logger.info(
                "recall: query='%s' agent_id='%s' bucket=%s → %d results",
                query, agent_id, bucket, len(results),
            )
            for r in results[:3]:
                logger.debug("  - %s (score=%.3f source=%s)", r.name, r.score, r.source)
            return results

    async def graph_recall(self, seed_paths: list[str]) -> list[str]:
        """Wikilink graph expansion from seed paths (1-hop BFS)."""
        if self._search is None:
            return []
        # Keep provenance for explicit lineage walks; keyword search excludes it.
        return self.wikilink_expander.expand(seed_paths, max_hops=1)

    async def get_preference_context(self, *, user_id: str | None = None) -> str:
        """Return preference block for the given user (PG-backed)."""
        pref = self._pref_for(user_id)
        if pref is None:
            return ""
        try:
            await pref.load_from_storage()
        except Exception as e:
            logger.warning("Preference load failed for user %s: %s", user_id, e)
            return ""
        return pref.build_context()

    def get_proactive_context(self) -> str:
        """Return proactive topics for prompt injection."""
        return self.proactive.format_for_prompt()

    async def trigger_auto_dream(self) -> dict:
        """Manually trigger auto_dream pipeline."""
        return await self.auto_dream.run(
            max_units=self.settings.memory_auto_dream_max_units,
        )

    def check_auto_dream_threshold(self) -> bool:
        """Check if auto_dream should trigger based on daily card count."""
        return self.auto_dream.should_trigger(self.settings.memory_auto_dream_threshold)

    async def close(self) -> None:
        """Clean shutdown."""
        self.bm25.close()
        self.wikilink_expander.close()
        self.file_catalog.close()
        self.vector_index.close()
        logger.info("MemoryService closed")

    # ─── Graph data ────────────────────────────────────────────────────

    def get_graph_data(
        self,
        bucket: str | None = None,
        agent_id: str | None = None,
        min_degree: int = 0,
    ) -> dict:
        """Return full wikilink graph data (nodes + edges) for visualization.

        Aggregates frontmatter metadata for each node and computes degree
        (in + out edges). Supports optional filtering by bucket, agent_id,
        and min_degree.
        """
        from app.memory.file_store.markdown_io import read_markdown

        raw_edges = self.wikilink_expander.get_all_edges()

        # Collect unique node paths from edges
        node_paths: set[str] = set()
        for edge in raw_edges:
            node_paths.add(edge["source"])  # type: ignore[arg-type]
            node_paths.add(edge["target"])  # type: ignore[arg-type]

        # Compute degree for each node
        degree_map: dict[str, int] = {p: 0 for p in node_paths}
        for edge in raw_edges:
            degree_map[edge["source"]] = degree_map.get(edge["source"], 0) + 1  # type: ignore[index]
            degree_map[edge["target"]] = degree_map.get(edge["target"], 0) + 1  # type: ignore[index]

        # Build node objects with frontmatter
        nodes: list[dict] = []
        for path in sorted(node_paths):
            fm = self._read_frontmatter_for_graph(path, read_markdown)
            degree = degree_map.get(path, 0)

            # Apply agent_id filter
            if agent_id and fm.get("agent_id") != agent_id:
                continue

            # Apply bucket filter
            if bucket and fm.get("bucket") != bucket:
                continue

            # Apply min_degree filter
            if degree < min_degree:
                continue

            nodes.append({
                "path": path,
                "name": fm.get("name", path),
                "bucket": fm.get("bucket", "wiki"),
                "importance": fm.get("importance", 0.5),
                "tags": fm.get("tags", []),
                "description": fm.get("description", ""),
                "degree": degree,
            })

        # Filter edges: both endpoints must be in the returned node set
        node_path_set = {n["path"] for n in nodes}
        edges: list[dict] = [
            {
                "source": e["source"],
                "target": e["target"],
                "predicate": e["predicate"],
            }
            for e in raw_edges
            if e["source"] in node_path_set and e["target"] in node_path_set
        ]

        return {"nodes": nodes, "edges": edges}

    def _read_frontmatter_for_graph(self, path: str, read_fn) -> dict:
        """Read frontmatter for a graph node by its workspace-relative path."""
        filepath = self.workspace.root / path
        try:
            filepath.resolve().relative_to(self.workspace.root.resolve())
        except (ValueError, OSError):
            return {"name": path, "bucket": "wiki", "importance": 0.5, "tags": [], "description": "", "agent_id": None}

        mem = read_fn(filepath)
        if mem is None:
            return {"name": path, "bucket": "wiki", "importance": 0.5, "tags": [], "description": "", "agent_id": None}

        fm = mem.frontmatter
        return {
            "name": fm.name,
            "bucket": fm.bucket,
            "importance": fm.importance,
            "tags": list(fm.tags),
            "description": fm.description,
            "agent_id": fm.agent_id,
        }

    # ─── Internal safe wrappers ───────────────────────────────────────────

    async def _safe_auto_memory(
        self, user_msg: str, assistant_msg: str, agent_id: str, conversation_id: str,
    ) -> None:
        try:
            result = await self.auto_memory.run(
                user_msg, assistant_msg,
                conversation_id=conversation_id, agent_id=agent_id,
            )
            if result > 0:
                # Reindex the newly written/updated daily card (in thread to avoid blocking event loop)
                from datetime import date
                today = date.today().isoformat()
                source_marker = f"session/{conversation_id}.jsonl"
                today_dir = self.workspace.daily_dir / today
                if today_dir.exists():
                    from app.memory.file_store.markdown_io import read_markdown
                    for f in sorted(today_dir.glob("*.md")):
                        mem = read_markdown(f)
                        if mem and source_marker in (mem.frontmatter.source or ""):
                            await asyncio.to_thread(self.auto_index.index_file, f)
                            break
                    else:
                        # Fallback: try old naming convention
                        old_name = f"session_{conversation_id[:8]}"
                        old_path = self.workspace.daily_file_path(old_name, today)
                        if old_path.exists():
                            await asyncio.to_thread(self.auto_index.index_file, old_path)

                # Check auto_dream threshold
                if self.check_auto_dream_threshold():
                    asyncio.create_task(self._safe_auto_dream())
        except Exception as e:
            logger.warning("auto_memory failed: %s", e)

    async def _safe_auto_dream(self) -> None:
        try:
            result = await self.trigger_auto_dream()
            logger.info("auto_dream completed: %s", result)
            # Reindex after dream (in thread to avoid blocking event loop)
            await asyncio.to_thread(self.auto_index.full_reindex)
        except Exception as e:
            logger.warning("auto_dream failed: %s", e)

    async def _safe_extract_preference(
        self, content: str, *, user_id: str | None = None,
    ) -> None:
        pref = self._pref_for(user_id)
        if pref is None:
            logger.warning("Preference extraction skipped: missing user_id")
            return
        try:
            key, value, matched = await pref.extract_and_save(content)
            if matched:
                logger.info("Preference extracted for %s: %s=%s", pref.user_id, key, value)
        except Exception as e:
            logger.warning("Preference extraction failed: %s", e)

    async def _safe_llm_extract_preference(
        self, content: str, *, user_id: str | None = None,
    ) -> None:
        pref = self._pref_for(user_id)
        if pref is None:
            logger.warning("LLM preference extraction skipped: missing user_id")
            return
        try:
            from app.memory.memory_writer_compat import extract_preferences_compat

            try:
                await pref.load_from_storage()
            except Exception as e:
                logger.warning(
                    "Preference preload failed for user %s: %s", pref.user_id, e,
                )
            prefs = await extract_preferences_compat(
                self._generate_fn, content,
                existing_keys=list(pref.data.keys()),
            )
            if prefs:
                await pref.save_batch(prefs, source="extracted")
                logger.info(
                    "LLM preference overlay for %s: %d keys", pref.user_id, len(prefs),
                )
        except Exception as e:
            logger.warning("LLM preference extraction failed: %s", e)

    async def _safe_extract_session_memory(self, conversation_id: str) -> None:
        try:
            if await self.session_memory.should_extract(conversation_id):
                await self.session_memory.extract(conversation_id)
        except Exception as e:
            logger.warning("Session Memory extraction failed: %s", e)

    @staticmethod
    def _is_trivial_reply(content: str) -> bool:
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
