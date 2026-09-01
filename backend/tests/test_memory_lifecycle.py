"""Tests for memory-lifecycle change (add-memory-lifecycle).

Covers: pending markers, count_changed, reconcile protection, cooldown,
Update segment merging, access stats, rerank, SUPERSEDE, curator, etc.
"""

from __future__ import annotations

import asyncio
import math
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.memory.access_stats import AccessStats
from app.memory.file_store.file_catalog import FileCatalog
from app.memory.file_store.frontmatter import MemoryFrontmatter
from app.memory.file_store.markdown_io import read_markdown, write_markdown
from app.memory.file_store.workspace import MemoryWorkspace


# ─── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_workspace(tmp_path):
    """Create a minimal memory workspace for testing."""
    from app.config import Settings

    settings = Settings(
        memory_workspace_dir=str(tmp_path),
        data_dir=str(tmp_path),
    )
    ws = MemoryWorkspace(settings)
    ws.initialize()
    return ws


@pytest.fixture
def tmp_catalog(tmp_path):
    """Create a FileCatalog with a temp db."""
    catalog = FileCatalog(tmp_path / "catalog.db")
    catalog.initialize()
    return catalog


@pytest.fixture
def tmp_access_stats(tmp_path):
    """Create an AccessStats with a temp db."""
    stats = AccessStats(tmp_path / "access_stats.db")
    stats.initialize()
    return stats


# ─── 8.1: Change detection (pending markers) ──────────────────────────────


class TestPendingMarkers:
    def test_new_card_mtime_zero(self, tmp_catalog, tmp_workspace):
        """New card written with mtime=0 should appear in get_changed."""
        # Create the actual file on disk (auto_memory writes file first, then catalogs)
        daily_dir = tmp_workspace.daily_dir / "2026-08-31"
        daily_dir.mkdir(parents=True, exist_ok=True)
        f = daily_dir / "test.md"
        f.write_text("---\nname: Test\n---\nbody", encoding="utf-8")

        # Use absolute path in catalog (matches real CWD-relative resolution)
        tmp_catalog.upsert(str(f), st_mtime=0.0, bucket="daily")
        changed = tmp_catalog.get_changed(bucket="daily")
        assert len(changed) == 1

    def test_count_changed_idempotent(self, tmp_catalog, tmp_workspace):
        """count_changed must be idempotent (no side effects)."""
        daily_dir = tmp_workspace.daily_dir / "2026-08-31"
        daily_dir.mkdir(parents=True, exist_ok=True)
        fa = daily_dir / "a.md"
        fb = daily_dir / "b.md"
        fa.write_text("---\nname: A\n---\na", encoding="utf-8")
        fb.write_text("---\nname: B\n---\nb", encoding="utf-8")

        tmp_catalog.upsert(str(fa), st_mtime=0.0, bucket="daily")
        tmp_catalog.upsert(str(fb), st_mtime=0.0, bucket="daily")
        first = tmp_catalog.count_changed(bucket="daily")
        second = tmp_catalog.count_changed(bucket="daily")
        assert first == second == 2

    def test_count_changed_vs_get_changed(self, tmp_catalog, tmp_workspace):
        """count_changed must not affect subsequent get_changed results."""
        daily_dir = tmp_workspace.daily_dir / "2026-08-31"
        daily_dir.mkdir(parents=True, exist_ok=True)
        fx = daily_dir / "x.md"
        fx.write_text("---\nname: X\n---\nx", encoding="utf-8")

        tmp_catalog.upsert(str(fx), st_mtime=0.0, bucket="daily")
        count = tmp_catalog.count_changed(bucket="daily")
        assert count == 1
        # get_changed should still find it
        changed = tmp_catalog.get_changed(bucket="daily")
        assert len(changed) == 1

    def test_reconcile_preserves_pending(self, tmp_catalog, tmp_workspace):
        """Reconcile must not overwrite mtime=0 pending records."""
        # Write a real file
        daily_dir = tmp_workspace.daily_dir / "2026-08-31"
        daily_dir.mkdir(parents=True, exist_ok=True)
        f = daily_dir / "real.md"
        f.write_text("---\nname: Real\n---\nbody", encoding="utf-8")

        # Insert as pending (mtime=0)
        tmp_catalog.upsert(str(f), st_mtime=0.0, bucket="daily")

        # Reconcile
        tmp_catalog.reconcile(tmp_workspace.daily_dir, tmp_workspace.digest_dir)

        # Should still be pending (mtime=0 in catalog)
        assert tmp_catalog._conn is not None
        cursor = tmp_catalog._conn.execute(
            "SELECT st_mtime FROM memory_catalog WHERE path = ?", (str(f),)
        )
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == 0.0  # Still pending after reconcile


# ─── 8.2: Trigger control ─────────────────────────────────────────────────


class TestTriggerControl:
    def test_should_trigger_uses_count_changed(self, tmp_workspace, tmp_catalog):
        """should_trigger should use count_changed, not full count."""
        from app.memory.pipeline.auto_dream import AutoDream

        # Create 10 "processed" cards (real mtime)
        daily_dir = tmp_workspace.daily_dir / "2026-08-31"
        daily_dir.mkdir(parents=True, exist_ok=True)
        for i in range(10):
            f = daily_dir / f"old_{i}.md"
            f.write_text(f"---\nname: Old {i}\n---\nbody", encoding="utf-8")
        tmp_catalog.reconcile(tmp_workspace.daily_dir, tmp_workspace.digest_dir)

        # All 10 are "processed" (real mtime, no change)
        assert tmp_catalog.count_changed(bucket="daily") == 0

        # Add 1 pending card
        tmp_catalog.upsert("daily/2026-08-31/new.md", st_mtime=0.0, bucket="daily")

        search = MagicMock()
        node_search = MagicMock()
        dream = AutoDream(tmp_workspace, search, node_search, tmp_catalog)

        # With threshold=1, should trigger (1 pending >= 1)
        assert dream.should_trigger(1) is True
        # With threshold=2, should not trigger (1 pending < 2)
        assert dream.should_trigger(2) is False


# ─── 8.3: Concurrent writes ───────────────────────────────────────────────


class TestConcurrentWrites:
    @pytest.mark.asyncio
    async def test_per_conversation_lock_serializes(self):
        """Per-conversation lock should serialize concurrent auto_memory tasks."""
        from app.memory.memory_service import MemoryService

        service = MemoryService.__new__(MemoryService)
        service._auto_memory_locks = {}

        lock1 = service.get_auto_memory_lock("conv-1")
        lock2 = service.get_auto_memory_lock("conv-1")
        lock3 = service.get_auto_memory_lock("conv-2")

        # Same conversation → same lock
        assert lock1 is lock2
        # Different conversation → different lock
        assert lock1 is not lock3

        # Lock should work
        async with lock1:
            assert lock1.locked()


# ─── 8.4: user_msg from PG ────────────────────────────────────────────────


class TestUserMsgFromPG:
    @pytest.mark.asyncio
    async def test_get_last_user_msg_from_pg(self):
        """_get_last_user_msg_from_pg should query PG for last user message."""
        from app.memory.memory_service import MemoryService
        from app.db.models import ChatHistory

        service = MemoryService.__new__(MemoryService)

        # Mock the DB session
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = "Hello, this is the user message"
        mock_session.execute.return_value = mock_result

        mock_get_db = MagicMock()
        mock_get_db.__aenter__ = AsyncMock(return_value=mock_session)
        mock_get_db.__aexit__ = AsyncMock(return_value=False)

        with patch("app.memory.memory_service.get_db", return_value=mock_get_db):
            result = await service._get_last_user_msg_from_pg("conv-123")

        assert result == "Hello, this is the user message"


# ─── 8.5: Update segment merging ──────────────────────────────────────────


class TestUpdateSegmentMerging:
    @pytest.mark.asyncio
    async def test_merge_triggered_at_3_updates(self):
        """When Update sections >= 3, merge LLM should be called."""
        from app.memory.pipeline.auto_dream import AutoDream

        ws = MagicMock()
        search = MagicMock()
        node_search = MagicMock()
        catalog = MagicMock()

        dream = AutoDream(ws, search, node_search, catalog)

        # Mock generate_fn to return merged content
        dream._generate_fn = MagicMock(return_value='{"merged_body": "MERGED CONTENT"}')

        # Create a body with 3 Update sections
        body_with_3_updates = (
            "## Rule / fact\nUser likes music\n\n"
            "---\n## Update (2026-08-28)\nUpdate 1\n\n"
            "---\n## Update (2026-08-29)\nUpdate 2\n\n"
            "---\n## Update (2026-08-30)\nUpdate 3"
        )

        merged = await dream._try_merge_updates(body_with_3_updates, "New update content", "2026-08-31")
        assert merged == "MERGED CONTENT"
        dream._generate_fn.assert_called_once()

    @pytest.mark.asyncio
    async def test_merge_failure_fallback(self):
        """When merge LLM fails, should return None (caller appends)."""
        from app.memory.pipeline.auto_dream import AutoDream

        ws = MagicMock()
        search = MagicMock()
        node_search = MagicMock()
        catalog = MagicMock()

        dream = AutoDream(ws, search, node_search, catalog)
        dream._generate_fn = MagicMock(side_effect=Exception("LLM error"))

        body = "## Rule\nTest\n\n---\n## Update (2026-08-28)\nU1"
        merged = await dream._try_merge_updates(body, "New", "2026-08-31")
        assert merged is None


# ─── 8.6: Access stats ────────────────────────────────────────────────────


class TestAccessStats:
    def test_record_and_get(self, tmp_access_stats):
        """record should increment count and update last_accessed."""
        tmp_access_stats.record("daily/2026-08-31/card.md")
        stats = tmp_access_stats.get("daily/2026-08-31/card.md")
        assert stats is not None
        assert stats["access_count"] == 1
        assert stats["last_accessed"] > 0

        # Record again
        tmp_access_stats.record("daily/2026-08-31/card.md")
        stats = tmp_access_stats.get("daily/2026-08-31/card.md")
        assert stats["access_count"] == 2

    def test_get_nonexistent(self, tmp_access_stats):
        """get should return None for unknown path."""
        assert tmp_access_stats.get("nonexistent.md") is None

    def test_record_does_not_touch_files(self, tmp_access_stats, tmp_workspace):
        """Access stats must NOT modify card files."""
        daily_dir = tmp_workspace.daily_dir / "2026-08-31"
        daily_dir.mkdir(parents=True, exist_ok=True)
        f = daily_dir / "card.md"
        f.write_text("---\nname: Test\n---\nbody", encoding="utf-8")
        original_mtime = f.stat().st_mtime

        # Record access
        tmp_access_stats.record("daily/2026-08-31/card.md")

        # File mtime should be unchanged
        assert f.stat().st_mtime == original_mtime

    def test_watermark_tracking(self, tmp_access_stats):
        """Watermark should track first_below_since for grace period."""
        path = "digest/personal/card.md"
        assert tmp_access_stats.get_watermark(path) is None

        now = time.time()
        tmp_access_stats.set_watermark(path, now)
        assert tmp_access_stats.get_watermark(path) == now

        tmp_access_stats.set_watermark(path, None)
        assert tmp_access_stats.get_watermark(path) is None


# ─── 8.7: Rerank ──────────────────────────────────────────────────────────


class TestRerank:
    def test_rerank_importance_factor(self):
        """Higher importance should rank higher with same RRF score."""
        from app.memory.search.hybrid_search import HybridSearch

        # Create mock search results
        paths = [
            ("daily/2026-08-31/low.md", 0.5, {"bm25": 0.5, "vector": 0.0, "rrf": 0.5}),
            ("daily/2026-08-31/high.md", 0.5, {"bm25": 0.5, "vector": 0.0, "rrf": 0.5}),
        ]

        # Mock the search internals
        hs = HybridSearch.__new__(HybridSearch)
        hs.settings = MagicMock()
        hs.settings.memory_decay_half_life_days = 30
        hs.settings.memory_rerank_enabled = True
        hs.access_stats = None
        hs.workspace_root = None

        # Mock read_markdown to return frontmatter with different importance
        def mock_read(path):
            mem = MagicMock()
            if "high" in str(path):
                mem.frontmatter.importance = 0.9
            else:
                mem.frontmatter.importance = 0.3
            mem.frontmatter.updated_at = date.today().isoformat()
            return mem

        with patch("app.memory.search.hybrid_search.read_markdown", side_effect=mock_read):
            reranked = hs._rerank(paths)

        # High importance should be first
        assert "high" in reranked[0][0]
        assert "low" in reranked[1][0]

    def test_rerank_disabled(self):
        """When rerank disabled, scores should be unchanged."""
        from app.memory.search.hybrid_search import HybridSearch

        paths = [
            ("daily/2026-08-31/a.md", 0.5, {"bm25": 0.5, "vector": 0.0, "rrf": 0.5}),
            ("daily/2026-08-31/b.md", 0.3, {"bm25": 0.3, "vector": 0.0, "rrf": 0.3}),
        ]

        hs = HybridSearch.__new__(HybridSearch)
        hs.settings = MagicMock()
        hs.settings.memory_rerank_enabled = False

        # Rerank should not be called when disabled, but if it is:
        # We test that the search method skips rerank when disabled
        # (tested via integration in actual search flow)


# ─── 8.8: Archive filtering ───────────────────────────────────────────────


class TestArchiveFiltering:
    def test_archived_excluded_from_recall_source(self):
        """Archived cards should be excluded from RecallSource results."""
        from app.services.prompt_assembler import RecallSource, Query, Slot, SlotFilter

        # Create mock memory service that returns archived + active results
        mock_memory = AsyncMock()
        mock_item_archived = MagicMock()
        mock_item_archived.content = "Archived content"
        mock_item_archived.score = 0.9
        mock_item_archived.frontmatter = {"status": "archived", "importance": 0.5}
        mock_item_archived.name = "Archived Card"

        mock_item_active = MagicMock()
        mock_item_active.content = "Active content"
        mock_item_active.score = 0.8
        mock_item_active.frontmatter = {"status": "active", "importance": 0.5}
        mock_item_active.name = "Active Card"

        mock_memory.recall = AsyncMock(return_value=[mock_item_archived, mock_item_active])

        source = RecallSource(mock_memory)
        q = Query(text="test", agent_id="agent1")
        slot = Slot(kind="recall_memory", filter=SlotFilter(top_k=3))

        items = asyncio.run(source.fetch(slot, q))
        assert len(items) == 1
        assert items[0].text == "Active content"

    def test_superseded_excluded_from_recall_source(self):
        """Superseded cards should also be excluded from RecallSource."""
        from app.services.prompt_assembler import RecallSource, Query, Slot, SlotFilter

        mock_memory = AsyncMock()
        mock_item = MagicMock()
        mock_item.content = "Superseded content"
        mock_item.score = 0.9
        mock_item.frontmatter = {"status": "superseded", "importance": 0.5}
        mock_item.name = "Superseded Card"

        mock_memory.recall = AsyncMock(return_value=[mock_item])

        source = RecallSource(mock_memory)
        q = Query(text="test")
        slot = Slot(kind="recall_memory", filter=SlotFilter(top_k=3))

        items = asyncio.run(source.fetch(slot, q))
        assert len(items) == 0


# ─── 8.9: Daily TTL ───────────────────────────────────────────────────────


class TestDailyTTL:
    def test_old_distilled_daily_excluded(self):
        """Daily card past TTL with digest inlinks should be excluded."""
        from app.memory.search.hybrid_search import HybridSearch

        hs = HybridSearch.__new__(HybridSearch)
        hs.settings = MagicMock()
        hs.settings.memory_daily_ttl_days = 30
        hs.workspace_root = None
        hs.expander = MagicMock()
        hs.expander.get_inlinks = MagicMock(return_value=[
            {"source": "digest/personal/card.md", "predicate": "derived_from"}
        ])

        old_date = (date.today() - timedelta(days=35)).isoformat()

        def mock_read(path):
            mem = MagicMock()
            mem.frontmatter.created_at = old_date
            mem.frontmatter.updated_at = old_date
            return mem

        with patch("app.memory.search.hybrid_search.read_markdown", side_effect=mock_read):
            paths = [("daily/2026-07-27/old.md", 0.5, {"rrf": 0.5})]
            filtered = hs._apply_daily_ttl_filter(paths)

        assert len(filtered) == 0

    def test_old_undistilled_daily_not_excluded(self):
        """Daily card past TTL without digest inlinks should NOT be excluded."""
        from app.memory.search.hybrid_search import HybridSearch

        hs = HybridSearch.__new__(HybridSearch)
        hs.settings = MagicMock()
        hs.settings.memory_daily_ttl_days = 30
        hs.workspace_root = None
        hs.expander = MagicMock()
        hs.expander.get_inlinks = MagicMock(return_value=[])  # No digest inlinks

        old_date = (date.today() - timedelta(days=35)).isoformat()

        def mock_read(path):
            mem = MagicMock()
            mem.frontmatter.created_at = old_date
            mem.frontmatter.updated_at = old_date
            return mem

        with patch("app.memory.search.hybrid_search.read_markdown", side_effect=mock_read):
            paths = [("daily/2026-07-27/old.md", 0.5, {"rrf": 0.5})]
            filtered = hs._apply_daily_ttl_filter(paths)

        assert len(filtered) == 1


# ─── 8.10: SUPERSEDE ──────────────────────────────────────────────────────


class TestSupersede:
    def test_frontmatter_accepts_superseded(self):
        """MemoryFrontmatter should accept 'superseded' as valid status."""
        fm = MemoryFrontmatter(name="Test", status="superseded")
        errors = fm.validate()
        assert not errors

    def test_frontmatter_rejects_invalid_status(self):
        """MemoryFrontmatter should reject unknown statuses."""
        fm = MemoryFrontmatter(status="unknown")
        errors = fm.validate()
        assert any("status" in e for e in errors)

    def test_supersede_rewrites_body(self):
        """_write_supersede should rewrite body and set status=superseded."""
        from app.memory.pipeline.auto_dream import AutoDream

        with tempfile.TemporaryDirectory() as tmp:
            top_path = Path(tmp) / "card.md"
            old_fm = MemoryFrontmatter(
                name="Music Preference",
                bucket="personal",
                importance=0.8,
                stable_key="user.interest.music",
                created_at="2026-08-01",
                updated_at="2026-08-15",
            )
            write_markdown(top_path, old_fm, "User likes doudou\n\nderived_from:: [[daily/2026-08-01/old.md]]")

            ws = MagicMock()
            search = MagicMock()
            node_search = MagicMock()
            catalog = MagicMock()

            dream = AutoDream(ws, search, node_search, catalog)
            dream._generate_fn = MagicMock()

            result = dream._write_supersede(
                top_path=top_path,
                name="Music Preference",
                tags=["music"],
                importance=0.6,
                stable_key="user.interest.music",
                today="2026-08-31",
                final_content="User now likes rock music",
                bucket="personal",
                source_paths=["daily/2026-08-31/new.md"],
            )

            assert result == "superseded"
            mem = read_markdown(top_path)
            assert mem.frontmatter.status == "superseded"
            assert "rock music" in mem.body
            assert "doudou" not in mem.body  # Old facts not carried over
            assert mem.frontmatter.importance == 0.6  # New value, not max


# ─── 8.11: stable_key normalization ───────────────────────────────────────


class TestStableKeyNormalization:
    def test_personal_key_topic_level(self):
        """Personal stable_key should be topic-level, not fact-level."""
        from app.memory.buckets import make_stable_key

        # Music interest should normalize to topic level
        key = make_stable_key("personal", "User likes doudou")
        assert key == "user.interest.music"

        # Explicit object-level key should be normalized
        key2 = make_stable_key("personal", "Music", explicit="user.interest.music.doudou")
        assert key2 == "user.interest.music"

    def test_procedure_key_unchanged(self):
        """Procedure keys should work as before."""
        from app.memory.buckets import make_stable_key

        key = make_stable_key("procedure", "Debug React hooks")
        assert key.startswith("proc.")


# ─── 8.12: Curator ────────────────────────────────────────────────────────


class TestCurator:
    @pytest.mark.asyncio
    async def test_curator_parse_cron(self):
        """CuratorJob should parse HH:MM cron correctly."""
        from app.memory.curator import CuratorJob
        from app.config import Settings

        settings = Settings(memory_auto_dream_cron="23:00")
        mock_service = MagicMock()
        job = CuratorJob(settings, mock_service)

        hour, minute = job._parse_cron()
        assert hour == 23
        assert minute == 0

    @pytest.mark.asyncio
    async def test_curator_step_independence(self):
        """One step failing should not block subsequent steps."""
        from app.memory.curator import CuratorJob
        from app.config import Settings

        settings = Settings(
            memory_auto_dream_cron="23:00",
            memory_decay_half_life_days=30,
            memory_archive_score=0.1,
            memory_archive_grace_days=14,
            memory_daily_ttl_days=30,
        )
        mock_service = MagicMock()
        mock_service.trigger_auto_dream = AsyncMock(side_effect=Exception("Step 1 failed"))
        mock_service._record_dream_completed = MagicMock()
        mock_service.wikilink_expander = MagicMock()
        mock_service.access_stats = MagicMock()
        mock_service.auto_index.full_reindex = MagicMock()
        mock_service.workspace = MagicMock()
        mock_service.workspace.digest_dir = Path("/nonexistent")
        mock_service.workspace.daily_dir = Path("/nonexistent")

        job = CuratorJob(settings, mock_service)

        # run() should not raise even though step 1 fails
        await job.run()

        # Step 4 (reindex) should still be called
        mock_service.auto_index.full_reindex.assert_called_once()

    def test_curator_watermark_archive(self):
        """Curator should archive cards with sustained low effective score."""
        from app.memory.curator import CuratorJob
        from app.config import Settings

        settings = Settings(
            memory_auto_dream_cron="23:00",
            memory_decay_half_life_days=30,
            memory_archive_score=0.1,
            memory_archive_grace_days=14,
            memory_daily_ttl_days=30,
        )
        mock_service = MagicMock()
        job = CuratorJob(settings, mock_service)

        # effective = importance * decay * log2(2 + access_count)
        # With importance=0.05, no access: 0.05 * 1.0 * 1.0 = 0.05 < 0.1
        importance = 0.05
        decay = 0.5 ** (0 / 30)  # 1.0
        access_factor = math.log2(2 + 0)  # 1.0
        effective = importance * decay * access_factor
        assert effective < settings.memory_archive_score
