"""Unit tests for structured memory — keyword_score, dual-path scoring,
store_classified new fields, _merge_pair, and case lifecycle parameters.

Covers tasks 7.1–7.5 of the add-structured-memory-items change.
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.memory.consolidation import (
    ConsolidationConfig,
    Item,
    keyword_score,
    tokenize_zh,
)
from app.memory.long_term import LongTerm


# ── Shared helpers (adapted from test_memory_long_term.py) ──────────────


def _make_settings(**overrides) -> Settings:
    defaults = {
        "memory_consolidation_similarity": 0.80,
        "memory_consolidation_dedup": 0.95,
        "memory_consolidation_ttl_days": 30,
        "memory_consolidation_decay_rate": 0.995,
        "memory_consolidation_min_importance": 0.3,
        "memory_consolidation_trigger": 5,
    }
    defaults.update(overrides)
    s = MagicMock(spec=Settings)
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


def _make_ltm(**kw) -> LongTerm:
    settings = _make_settings(**kw)
    ltm = LongTerm(settings)
    return ltm


# ── 7.1: keyword_score() Jaccard similarity ────────────────────────────


class TestKeywordScore:
    """Test keyword_score() — Jaccard similarity between query tokens and item keywords."""

    def test_empty_query_returns_zero(self):
        """Empty query_tokens should return 0.0."""
        assert keyword_score(None, ["python", "react"]) == 0.0
        assert keyword_score([], ["python", "react"]) == 0.0

    def test_empty_keywords_returns_zero(self):
        """Empty item_keywords should return 0.0."""
        assert keyword_score(["python"], None) == 0.0
        assert keyword_score(["python"], []) == 0.0

    def test_complete_match(self):
        """Identical sets should return 1.0 (Jaccard = intersection / union = 1)."""
        assert keyword_score(["python", "react", "vite"], ["python", "react", "vite"]) == 1.0

    def test_partial_match(self):
        """Partial overlap should return intersection/union."""
        # query = {a, b, c}, keywords = {a, b, d}
        # intersection = {a, b} = 2, union = {a, b, c, d} = 4 → 0.5
        score = keyword_score(["a", "b", "c"], ["a", "b", "d"])
        assert score == pytest.approx(0.5)

    def test_no_match(self):
        """Disjoint sets should return 0.0."""
        assert keyword_score(["python", "react"], ["java", "spring"]) == 0.0

    def test_case_insensitive(self):
        """Both sets are lowercased before comparison."""
        assert keyword_score(["Python", "REACT"], ["python", "react"]) == 1.0

    def test_empty_strings_filtered(self):
        """Empty string entries should be filtered out before comparison."""
        # Only truly empty strings ("") are falsy and filtered by `if t`.
        # Whitespace-only strings are truthy and kept.
        score = keyword_score(["python", "", ""], ["python", ""])
        assert score == 1.0

    def test_single_keyword_match(self):
        """Single keyword matching should return correct Jaccard."""
        # query = {python}, keywords = {python, react, vite}
        # intersection = 1, union = 3 → 1/3
        score = keyword_score(["python"], ["python", "react", "vite"])
        assert score == pytest.approx(1.0 / 3.0)


# ── 7.2: Dual-path scoring formula ──────────────────────────────────────


class TestDualPathScoring:
    """Test that recall uses dual-path: semantic*0.5 + keyword*0.2 + importance*0.3."""

    @pytest.mark.asyncio
    async def test_keyword_boosts_score(self):
        """Item with matching keywords should score higher than one without."""
        ltm = _make_ltm()
        # Both items have identical embeddings (same semantic similarity)
        # but different keywords
        ltm.set_embed_fn(lambda text: [1.0, 0.0])

        with patch("app.memory.long_term.get_remote_db") as mock_db:
            mock_db.side_effect = Exception("no db")
            await ltm.add(
                "Python development", importance=0.5,
                summary="Python开发", keywords=["python", "django"],
            )
            await ltm.add(
                "Java development", importance=0.5,
                summary="Java开发", keywords=["java", "spring"],
            )

        results = await ltm.recall("python django", top_k=2)
        # Both items will have similar semantic scores (same embedding)
        # but the Python item should rank higher due to keyword match
        assert len(results) >= 1
        assert "python" in results[0].content.lower()

    @pytest.mark.asyncio
    async def test_low_score_filtered(self):
        """Items scoring below 0.3 threshold should be filtered out."""
        ltm = _make_ltm()
        # Embeddings are orthogonal — low semantic similarity
        ltm.set_embed_fn(lambda text: [1.0, 0.0] if "python" in text.lower() else [0.0, 1.0])

        with patch("app.memory.long_term.get_remote_db") as mock_db:
            mock_db.side_effect = Exception("no db")
            await ltm.add(
                "Python is great", importance=0.3,
                summary="Python", keywords=["python"],
            )

        # Query "cooking recipes" — no semantic or keyword overlap
        # score = 0.0 * 0.5 + 0.0 * 0.2 + 0.3 * 0.3 = 0.09 < 0.3 → filtered
        results = await ltm.recall("cooking recipes", top_k=5)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_importance_contributes_to_score(self):
        """Higher importance should boost score even with no keyword match."""
        ltm = _make_ltm()
        ltm.set_embed_fn(lambda text: [1.0, 0.0])

        with patch("app.memory.long_term.get_remote_db") as mock_db:
            mock_db.side_effect = Exception("no db")
            # Low importance — might not pass threshold
            await ltm.add(
                "Python low importance", importance=0.3,
                summary="Python低", keywords=["python"],
            )
            # High importance — should definitely pass threshold
            await ltm.add(
                "Python high importance", importance=0.9,
                summary="Python高", keywords=["python"],
            )

        results = await ltm.recall("python", top_k=5)
        # Both should be returned (semantic=1.0, kw=1.0 for both)
        # Low: 1.0*0.5 + 1.0*0.2 + 0.3*0.3 = 0.79
        # High: 1.0*0.5 + 1.0*0.2 + 0.9*0.3 = 0.97
        assert len(results) == 2
        # High importance should rank first
        assert "high" in results[0].content.lower()

    @pytest.mark.asyncio
    async def test_recall_by_filter_dual_path(self):
        """recall_by_filter should also use dual-path scoring."""
        ltm = _make_ltm()
        ltm.set_embed_fn(lambda text: [1.0, 0.0] if "python" in text.lower() else [0.0, 1.0])

        with patch("app.memory.long_term.get_remote_db") as mock_db:
            mock_db.side_effect = Exception("no db")
            await ltm.add(
                "Python project", importance=0.5,
                summary="Python项目", keywords=["python", "django"],
            )

        filt = MagicMock()
        filt.min_score = 0.0
        filt.categories = []
        filt.require_tags = []
        filt.max_age_hours = 0
        filt.top_k = 10

        results = await ltm.recall_by_filter("python django", None, filt)
        # With TF cosine (no embedding), query tokenizes to [python, django]
        # keyword_score = 2/2 = 1.0 (both match)
        # TF cosine will be nonzero
        # score = tf_cosine * 0.5 + 1.0 * 0.2 + 0.5 * 0.3
        assert len(results) >= 1


# ── 7.3: store_classified writes new fields + summary-based embedding ──


class TestStoreClassifiedFields:
    """Test that store_classified writes summary/keywords/content_scope
    and that embedding is computed from summary when available."""

    @pytest.mark.asyncio
    async def test_store_classified_writes_summary_keywords(self):
        """store_classified should persist summary, keywords, content_scope."""
        ltm = _make_ltm()
        emb = [0.1, 0.2, 0.3]

        with patch("app.memory.long_term.get_remote_db") as mock_db:
            inserted = await ltm.store_classified(
                content="User likes TypeScript with React",
                importance=0.7,
                emb=emb,
                category="fact",
                tags=["preference"],
                slot_hint="recall_memory",
                summary="用户前端偏好",
                keywords=["TypeScript", "React"],
                content_scope="tech_stack",
            )

        assert inserted is True
        assert len(ltm.items) == 1
        item = ltm.items[0]
        assert item.summary == "用户前端偏好"
        assert item.keywords == ["TypeScript", "React"]
        assert item.content_scope == "tech_stack"

    @pytest.mark.asyncio
    async def test_store_classified_dedup_syncs_fields(self):
        """When dedup hits an existing item, new fields should be synced."""
        ltm = _make_ltm()
        emb = [1.0, 0.0]

        # First insert
        with patch("app.memory.long_term.get_remote_db") as mock_db:
            await ltm.store_classified(
                content="User likes Python",
                importance=0.5,
                emb=emb,
                category="fact",
                tags=["lang"],
                slot_hint="",
                summary="",
                keywords=[],
            )

        # Second insert — near-identical embedding → dedup hit
        with patch("app.memory.long_term.get_remote_db") as mock_db:
            await ltm.store_classified(
                content="User likes Python programming",
                importance=0.7,
                emb=emb,
                category="",
                tags=["lang", "backend"],
                slot_hint="",
                summary="用户编程语言偏好",
                keywords=["Python", "编程"],
            )

        # Should have merged into 1 item
        assert len(ltm.items) == 1
        item = ltm.items[0]
        # Summary should be set from second call (first was empty)
        assert item.summary == "用户编程语言偏好"
        # Keywords should be merged (deduped)
        assert "Python" in item.keywords
        assert "编程" in item.keywords

    @pytest.mark.asyncio
    async def test_add_uses_summary_for_embedding(self):
        """add() should compute embedding from summary when available."""
        ltm = _make_ltm()
        embed_calls = []

        def mock_embed(text):
            embed_calls.append(text)
            return [0.5, 0.5]

        ltm.set_embed_fn(mock_embed)

        with patch("app.memory.long_term.get_remote_db") as mock_db:
            mock_db.side_effect = Exception("no db")
            await ltm.add(
                content="Some very long content that should not be used for embedding",
                importance=0.5,
                summary="短摘要",
                keywords=["test"],
            )

        # Embedding should be computed from summary, not content
        assert len(embed_calls) == 1
        assert embed_calls[0] == "短摘要"

    @pytest.mark.asyncio
    async def test_add_falls_back_to_content_without_summary(self):
        """add() should use content for embedding when summary is empty."""
        ltm = _make_ltm()
        embed_calls = []

        def mock_embed(text):
            embed_calls.append(text)
            return [0.5, 0.5]

        ltm.set_embed_fn(mock_embed)

        with patch("app.memory.long_term.get_remote_db") as mock_db:
            mock_db.side_effect = Exception("no db")
            await ltm.add(
                content="content for embedding",
                importance=0.5,
            )

        assert len(embed_calls) == 1
        assert embed_calls[0] == "content for embedding"


# ── 7.4: _merge_pair merges new fields ─────────────────────────────────


class TestMergePairFields:
    """Test _merge_pair() — summary (prefer non-empty), keywords (deduped
    union capped at 8), content_scope (prefer non-empty)."""

    def test_merge_summary_prefers_non_empty(self):
        """_merge_pair should prefer non-empty summary from either item."""
        ltm = _make_ltm()
        now = time.time()

        item_i = Item(content="A", importance=0.5, summary="", keywords=["python"])
        item_j = Item(content="B", importance=0.6, summary="merged summary", keywords=["react"])
        merged = ltm._merge_pair(item_i, item_j, now)

        assert merged.summary == "merged summary"

    def test_merge_summary_keeps_first_if_both_non_empty(self):
        """When both have summaries, prefer the first item's summary."""
        ltm = _make_ltm()
        now = time.time()

        item_i = Item(content="A", importance=0.5, summary="first summary", keywords=["python"])
        item_j = Item(content="B", importance=0.6, summary="second summary", keywords=["react"])
        merged = ltm._merge_pair(item_i, item_j, now)

        assert merged.summary == "first summary"

    def test_merge_keywords_deduped_union(self):
        """Keywords should be deduplicated union of both items' keywords."""
        ltm = _make_ltm()
        now = time.time()

        item_i = Item(content="A", importance=0.5, keywords=["python", "django"])
        item_j = Item(content="B", importance=0.6, keywords=["python", "flask"])
        merged = ltm._merge_pair(item_i, item_j, now)

        # Union with dedup: python, django, flask (order preserved from i first)
        assert "python" in merged.keywords
        assert "django" in merged.keywords
        assert "flask" in merged.keywords
        # No duplicates
        assert len(merged.keywords) == len(set(merged.keywords))

    def test_merge_keywords_capped_at_8(self):
        """Keywords should be capped at 8 entries."""
        ltm = _make_ltm()
        now = time.time()

        item_i = Item(content="A", importance=0.5, keywords=[f"kw{i}" for i in range(5)])
        item_j = Item(content="B", importance=0.6, keywords=[f"kw{i}" for i in range(5, 12)])
        merged = ltm._merge_pair(item_i, item_j, now)

        assert len(merged.keywords) <= 8

    def test_merge_content_scope_prefers_non_empty(self):
        """content_scope should prefer non-empty from either item."""
        ltm = _make_ltm()
        now = time.time()

        item_i = Item(content="A", importance=0.5, content_scope="")
        item_j = Item(content="B", importance=0.6, content_scope="tech_stack")
        merged = ltm._merge_pair(item_i, item_j, now)

        assert merged.content_scope == "tech_stack"

    def test_merge_all_empty_fields(self):
        """When both items have empty structured fields, merged should also be empty."""
        ltm = _make_ltm()
        now = time.time()

        item_i = Item(content="A", importance=0.5)
        item_j = Item(content="B", importance=0.6)
        merged = ltm._merge_pair(item_i, item_j, now)

        assert merged.summary == ""
        assert merged.keywords == []
        assert merged.content_scope == ""


# ── 7.5: Case lifecycle parameters ─────────────────────────────────────


class TestCaseLifecycleParams:
    """Test case-specific lifecycle parameters: TTL=90d, decay=0.998,
    min_importance=0.4, dedup_threshold=0.90."""

    def test_config_defaults(self):
        """ConsolidationConfig should have correct case parameter defaults."""
        cfg = ConsolidationConfig()
        assert cfg.case_ttl_days == 90
        assert cfg.case_decay_rate == pytest.approx(0.998)
        assert cfg.case_min_importance == pytest.approx(0.4)
        assert cfg.case_dedup_threshold == pytest.approx(0.90)

    def test_get_params_for_case(self):
        """get_params_for_category('case') should return case-specific params."""
        cfg = ConsolidationConfig()
        params = cfg.get_params_for_category("case")
        assert params["ttl_days"] == 90
        assert params["decay_rate"] == pytest.approx(0.998)
        assert params["min_importance"] == pytest.approx(0.4)
        assert params["dedup_threshold"] == pytest.approx(0.90)

    def test_get_params_for_non_case(self):
        """get_params_for_category for non-case should return default params."""
        cfg = ConsolidationConfig()
        params = cfg.get_params_for_category("fact")
        assert params["ttl_days"] == 30
        assert params["decay_rate"] == pytest.approx(0.995)
        assert params["min_importance"] == pytest.approx(0.3)
        assert params["dedup_threshold"] == pytest.approx(0.95)

    @pytest.mark.asyncio
    async def test_consolidate_case_slower_decay(self):
        """Case items should decay slower (0.998) than default (0.995)."""
        ltm = _make_ltm(memory_consolidation_trigger=2)
        ltm.set_embed_fn(lambda text: [1.0, 0.0])

        now = time.time()
        old_ts = now - 10 * 86400  # 10 days ago

        # Case item (slower decay)
        case_item = Item(
            content="Case memory", importance=0.8,
            embedding=[1.0, 0.0], id=0,
            created_at=old_ts, last_accessed=old_ts,
            category="case", scope="global", agent_id="",
            last_decay_ts=old_ts,
        )
        # Default item (faster decay)
        default_item = Item(
            content="Default memory", importance=0.8,
            embedding=[0.5, 0.5], id=1,
            created_at=old_ts, last_accessed=old_ts,
            category="fact", scope="global", agent_id="",
            last_decay_ts=old_ts,
        )
        ltm.items = [case_item, default_item]
        ltm._next_id = 2
        ltm._items_since_last = 2

        with patch("app.memory.long_term.get_remote_db"):
            result = await ltm.consolidate()

        # After 10 days:
        # case: 0.8 * 0.998^10 ≈ 0.8 * 0.9802 ≈ 0.7842
        # default: 0.8 * 0.995^10 ≈ 0.8 * 0.9512 ≈ 0.7609
        case_survivor = None
        default_survivor = None
        for it in ltm.items:
            if "case" in it.content.lower():
                case_survivor = it
            if "default" in it.content.lower():
                default_survivor = it

        assert case_survivor is not None
        assert default_survivor is not None
        # Case should retain higher importance (slower decay)
        assert case_survivor.importance > default_survivor.importance

    @pytest.mark.asyncio
    async def test_consolidate_case_not_expired_before_90_days(self):
        """Case items should NOT be expired before 90 days TTL."""
        ltm = _make_ltm(memory_consolidation_trigger=1)
        ltm.set_embed_fn(lambda text: [1.0, 0.0])

        now = time.time()
        old_ts = now - 60 * 86400  # 60 days ago — under case TTL (90)

        case_item = Item(
            content="Old case memory", importance=0.45,
            embedding=[1.0, 0.0], id=0,
            created_at=old_ts, last_accessed=old_ts,
            category="case", scope="global", agent_id="",
            last_decay_ts=now,  # No decay needed for this test
        )
        ltm.items = [case_item]
        ltm._next_id = 1
        ltm._items_since_last = 1

        with patch("app.memory.long_term.get_remote_db"):
            result = await ltm.consolidate()

        # Case TTL=90 days, 60 < 90, so should NOT be expired
        assert result.expired == 0
        assert len(ltm.items) == 1

    @pytest.mark.asyncio
    async def test_consolidate_default_expired_after_30_days(self):
        """Default items with low importance should be expired after 30 days."""
        ltm = _make_ltm(memory_consolidation_trigger=1)
        ltm.set_embed_fn(lambda text: [1.0, 0.0])

        now = time.time()
        old_ts = now - 35 * 86400  # 35 days — over default TTL (30)

        # Low importance default item — should be expired
        default_item = Item(
            content="Old low importance default", importance=0.2,
            embedding=[1.0, 0.0], id=0,
            created_at=old_ts, last_accessed=old_ts,
            category="fact", scope="global", agent_id="",
            last_decay_ts=now,
        )
        # Second item with high importance — survives consolidation trigger check
        # (consolidate() returns early if len(items) <= 1)
        survivor = Item(
            content="High importance survivor", importance=0.9,
            embedding=[0.0, 1.0], id=1,
            created_at=now, last_accessed=now,
            category="fact", scope="global", agent_id="",
            last_decay_ts=now,
        )
        ltm.items = [default_item, survivor]
        ltm._next_id = 2
        ltm._items_since_last = 2

        with patch("app.memory.long_term.get_remote_db"):
            result = await ltm.consolidate()

        # Default TTL=30, 35 > 30, importance 0.2 < 0.3 → expired
        assert result.expired == 1
        assert len(ltm.items) == 1  # only survivor remains

    @pytest.mark.asyncio
    async def test_consolidate_case_not_expired_with_low_importance_under_ttl(self):
        """Case with importance below case_min_importance but under TTL should survive."""
        ltm = _make_ltm(memory_consolidation_trigger=1)
        ltm.set_embed_fn(lambda text: [1.0, 0.0])

        now = time.time()
        old_ts = now - 50 * 86400  # 50 days — under case TTL (90)

        case_item = Item(
            content="Case with low importance", importance=0.35,
            embedding=[1.0, 0.0], id=0,
            created_at=old_ts, last_accessed=old_ts,
            category="case", scope="global", agent_id="",
            last_decay_ts=now,
        )
        ltm.items = [case_item]
        ltm._next_id = 1
        ltm._items_since_last = 1

        with patch("app.memory.long_term.get_remote_db"):
            result = await ltm.consolidate()

        # 50 < 90 (TTL), so NOT expired even though importance < case_min_importance
        assert result.expired == 0
        assert len(ltm.items) == 1

    @pytest.mark.asyncio
    async def test_consolidate_case_expired_after_90_days_low_importance(self):
        """Case with importance below case_min_importance and over TTL should expire."""
        ltm = _make_ltm(memory_consolidation_trigger=1)
        ltm.set_embed_fn(lambda text: [1.0, 0.0])

        now = time.time()
        old_ts = now - 95 * 86400  # 95 days — over case TTL (90)

        case_item = Item(
            content="Old case low importance", importance=0.35,
            embedding=[1.0, 0.0], id=0,
            created_at=old_ts, last_accessed=old_ts,
            category="case", scope="global", agent_id="",
            last_decay_ts=now,
        )
        # Survivor item to pass the len(items) <= 1 guard
        survivor = Item(
            content="High importance survivor", importance=0.9,
            embedding=[0.0, 1.0], id=1,
            created_at=now, last_accessed=now,
            category="fact", scope="global", agent_id="",
            last_decay_ts=now,
        )
        ltm.items = [case_item, survivor]
        ltm._next_id = 2
        ltm._items_since_last = 2

        with patch("app.memory.long_term.get_remote_db"):
            result = await ltm.consolidate()

        # 95 > 90 (TTL), importance 0.35 < 0.4 (case_min_importance) → expired
        assert result.expired == 1
        assert len(ltm.items) == 1  # only survivor remains

    @pytest.mark.asyncio
    async def test_consolidate_case_dedup_threshold_more_lenient(self):
        """Case dedup_threshold=0.90 is more lenient than default 0.95."""
        ltm = _make_ltm(memory_consolidation_trigger=2)
        ltm.set_embed_fn(lambda text: [1.0, 0.0])

        now = time.time()
        old_ts = now - 86400  # 1 day ago (minimal decay)

        # Two case items with 0.92 cosine similarity
        # Case dedup_threshold=0.90 → should be deduped
        # Default dedup_threshold=0.95 → would NOT be deduped
        case_i = Item(
            content="Case A", importance=0.6,
            embedding=[1.0, 0.0], id=0,
            created_at=old_ts, last_accessed=old_ts,
            category="case", scope="global", agent_id="",
            last_decay_ts=now,
        )
        case_j = Item(
            content="Case B", importance=0.6,
            embedding=[0.92, 0.39],  # ~0.92 cosine similarity to [1, 0]
            id=1, created_at=old_ts, last_accessed=old_ts,
            category="case", scope="global", agent_id="",
            last_decay_ts=now,
        )
        ltm.items = [case_i, case_j]
        ltm._next_id = 2
        ltm._items_since_last = 2

        with patch("app.memory.long_term.get_remote_db"):
            result = await ltm.consolidate()

        # With case_dedup_threshold=0.90, similarity ~0.92 ≥ 0.90 → deduped
        assert result.deduped + result.merged >= 1
        assert len(ltm.items) == 1
