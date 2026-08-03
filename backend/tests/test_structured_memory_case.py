"""Unit tests for case memory extraction, memory_store tool validation,
and migration function.

Covers tasks 7.6–7.8 of the add-structured-memory-items change.
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import Settings
from app.memory.consolidation import Item
from app.memory.long_term import LongTerm
from app.tools.base import ToolContext
from app.tools.memory_store import memory_store_handler
from app.tools.rate_limiter import SimpleRateLimiter


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Reset the module-level rate limiter before each test."""
    from app.tools import memory_store as ms_module
    original = ms_module._rate_limiter
    ms_module._rate_limiter = SimpleRateLimiter()
    yield
    ms_module._rate_limiter = original


def _make_ctx(agent_id: str = "agent_1", run_id: str = "run_1") -> ToolContext:
    """Create a minimal ToolContext for testing."""
    return ToolContext(
        conversation_id="conv_1",
        workspace_path="/tmp/ws",
        agent_id=agent_id,
        run_id=run_id,
        cancel_event=asyncio.Event(),
    )


def _make_mock_memory_service(ltm_items=None, store_result=True):
    """Create a mock _memory_service with an LTM that has items and store_classified."""
    ltm = MagicMock()
    ltm.items = ltm_items or []
    ltm.store_classified = AsyncMock(return_value=store_result)

    svc = MagicMock()
    svc.ltm = ltm
    svc._embed_fn = None
    return svc


# ── Shared helpers ──────────────────────────────────────────────────────


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
    return LongTerm(settings)


# ── 7.6: Case memory extraction function ─────────────────────────────────


class TestCaseExtraction:
    """Test extract_case_memories() — produces case memories when
    reusable experience exists, returns empty when none."""

    @pytest.mark.asyncio
    async def test_extract_case_with_experience(self):
        """When LLM returns case memories, they should be stored in LTM."""
        from app.memory.memory_writer import extract_case_memories

        ltm = _make_ltm()
        # Use orthogonal embeddings per summary to avoid cosine dedup hits
        embed_fn = lambda text: [1.0, 0.0] if "重构" in text else [0.0, 1.0]

        llm_output = json.dumps({
            "cases": [
                {
                    "text": "重构时先跑全量测试确认基线再分步修改",
                    "summary": "重构先跑基线测试",
                    "keywords": ["重构", "测试", "基线"],
                    "outcome": "success",
                },
                {
                    "text": "Milvus 搜索前需要先 load collection",
                    "summary": "Milvus搜索需先load",
                    "keywords": ["Milvus", "collection", "load", "搜索"],
                    "outcome": "insight",
                },
            ]
        })

        generate_fn = MagicMock(return_value=llm_output)

        with patch("app.memory.long_term.get_remote_db") as mock_db:
            mock_db.side_effect = Exception("no db")
            count = await extract_case_memories(
                generate_fn=generate_fn,
                embed_fn=embed_fn,
                ltm=ltm,
                session_summary="用户重构了认证模块，先跑测试再改",
                task_result="成功完成重构",
            )

        assert count == 2
        assert len(ltm.items) == 2

        # Verify first case memory
        item = ltm.items[0]
        assert item.category == "case"
        assert item.importance == pytest.approx(0.6)
        assert "重构" in item.content
        assert item.summary == "重构先跑基线测试"
        assert "重构" in item.keywords

    @pytest.mark.asyncio
    async def test_extract_case_no_experience(self):
        """When LLM returns empty cases array, no memories should be stored."""
        from app.memory.memory_writer import extract_case_memories

        ltm = _make_ltm()
        generate_fn = MagicMock(return_value='{"cases": []}')
        embed_fn = lambda text: [0.1, 0.2]

        count = await extract_case_memories(
            generate_fn=generate_fn,
            embed_fn=embed_fn,
            ltm=ltm,
            session_summary="用户问了天气情况",
        )

        assert count == 0
        assert len(ltm.items) == 0

    @pytest.mark.asyncio
    async def test_extract_case_empty_session_summary(self):
        """Empty session_summary should return 0 immediately."""
        from app.memory.memory_writer import extract_case_memories

        ltm = _make_ltm()
        generate_fn = MagicMock()
        embed_fn = MagicMock()

        count = await extract_case_memories(
            generate_fn=generate_fn,
            embed_fn=embed_fn,
            ltm=ltm,
            session_summary="",
        )

        assert count == 0
        generate_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_extract_case_no_generate_fn(self):
        """No generate_fn should return 0 immediately."""
        from app.memory.memory_writer import extract_case_memories

        ltm = _make_ltm()

        count = await extract_case_memories(
            generate_fn=None,
            embed_fn=None,
            ltm=ltm,
            session_summary="some summary",
        )

        assert count == 0

    @pytest.mark.asyncio
    async def test_extract_case_llm_failure(self):
        """When LLM call fails, should return 0 without raising."""
        from app.memory.memory_writer import extract_case_memories

        ltm = _make_ltm()
        generate_fn = MagicMock(side_effect=RuntimeError("LLM unavailable"))

        count = await extract_case_memories(
            generate_fn=generate_fn,
            embed_fn=None,
            ltm=ltm,
            session_summary="some summary",
        )

        assert count == 0

    @pytest.mark.asyncio
    async def test_extract_case_invalid_json(self):
        """When LLM returns invalid JSON, should return 0."""
        from app.memory.memory_writer import extract_case_memories

        ltm = _make_ltm()
        generate_fn = MagicMock(return_value="not json at all")

        count = await extract_case_memories(
            generate_fn=generate_fn,
            embed_fn=None,
            ltm=ltm,
            session_summary="some summary",
        )

        assert count == 0

    @pytest.mark.asyncio
    async def test_extract_case_uses_summary_for_embedding(self):
        """Embedding should be computed from summary, not content."""
        from app.memory.memory_writer import extract_case_memories

        ltm = _make_ltm()
        embed_calls = []

        def embed_fn(text):
            embed_calls.append(text)
            return [0.5, 0.5]

        llm_output = json.dumps({
            "cases": [
                {
                    "text": "long content text here",
                    "summary": "短摘要",
                    "keywords": ["kw1"],
                    "outcome": "success",
                }
            ]
        })
        generate_fn = MagicMock(return_value=llm_output)

        with patch("app.memory.long_term.get_remote_db"):
            await extract_case_memories(
                generate_fn=generate_fn,
                embed_fn=embed_fn,
                ltm=ltm,
                session_summary="session summary",
            )

        assert len(embed_calls) == 1
        assert embed_calls[0] == "短摘要"

    @pytest.mark.asyncio
    async def test_extract_case_with_task_result(self):
        """Task result should be included in the LLM prompt."""
        from app.memory.memory_writer import extract_case_memories

        ltm = _make_ltm()
        llm_output = json.dumps({"cases": []})
        generate_fn = MagicMock(return_value=llm_output)

        await extract_case_memories(
            generate_fn=generate_fn,
            embed_fn=None,
            ltm=ltm,
            session_summary="session summary",
            task_result="task completed successfully",
        )

        # Verify the prompt was called with both summary and task result
        args = generate_fn.call_args
        user_msg = args[0][1]  # second positional arg
        assert "session summary" in user_msg.lower()
        assert "task completed successfully" in user_msg


# ── 7.7: memory_store tool category="case" validation ───────────────────


class TestMemoryStoreCaseValidation:
    """Test memory_store tool handler — category="case" requires
    summary and keywords."""

    @pytest.mark.asyncio
    async def test_case_requires_summary(self):
        """category=case without summary should error."""
        ctx = _make_ctx()
        args = {
            "content": "Some case experience",
            "category": "case",
            "importance": 0.6,
            "keywords": ["test", "case"],
            # Missing summary
        }

        result = await memory_store_handler(args, ctx)
        assert not result.ok
        assert "summary" in result.error.lower()

    @pytest.mark.asyncio
    async def test_case_requires_keywords(self):
        """category=case without keywords should error."""
        ctx = _make_ctx()
        args = {
            "content": "Some case experience",
            "category": "case",
            "importance": 0.6,
            "summary": "Case summary",
            # Missing keywords
        }

        result = await memory_store_handler(args, ctx)
        assert not result.ok
        assert "keywords" in result.error.lower()

    @pytest.mark.asyncio
    async def test_case_requires_nonempty_keywords(self):
        """category=case with empty keywords list should error."""
        ctx = _make_ctx()
        args = {
            "content": "Some case experience",
            "category": "case",
            "importance": 0.6,
            "summary": "Case summary",
            "keywords": [],
        }

        result = await memory_store_handler(args, ctx)
        assert not result.ok

    @pytest.mark.asyncio
    async def test_non_case_without_summary_ok(self):
        """Non-case categories should work without summary/keywords."""
        svc = _make_mock_memory_service(ltm_items=[], store_result=True)
        ctx = _make_ctx()
        args = {
            "content": "A fact about the project",
            "category": "fact",
            "importance": 0.5,
        }

        with patch("app.main._memory_service", svc):
            result = await memory_store_handler(args, ctx)

        assert result.ok

    @pytest.mark.asyncio
    async def test_case_with_summary_and_keywords_validated(self):
        """category=case with both summary and keywords should pass validation."""
        svc = _make_mock_memory_service(ltm_items=[], store_result=True)
        ctx = _make_ctx()
        args = {
            "content": "重构认证模块的经验",
            "category": "case",
            "importance": 0.6,
            "summary": "认证重构策略",
            "keywords": ["重构", "认证", "测试"],
            "tags": ["success"],
        }

        with patch("app.main._memory_service", svc):
            result = await memory_store_handler(args, ctx)

        assert result.ok
        # Verify store_classified was called with summary and keywords
        call_kwargs = svc.ltm.store_classified.call_args.kwargs
        assert call_kwargs.get("summary") == "认证重构策略"
        assert "重构" in call_kwargs.get("keywords", [])

    @pytest.mark.asyncio
    async def test_keywords_max_10_enforced(self):
        """Keywords list with more than 10 entries should error."""
        ctx = _make_ctx()
        args = {
            "content": "Some content",
            "category": "fact",
            "importance": 0.5,
            "summary": "Summary",
            "keywords": [f"kw{i}" for i in range(11)],
        }

        result = await memory_store_handler(args, ctx)
        assert not result.ok
        assert "at most 10" in result.error.lower()


# ── 7.8: Migration function (integration) ────────────────────────────────


class TestMigrationFunction:
    """Integration test: _safe_migrate_existing_memories —
    successful migration + LLM failure skips without blocking."""

    @pytest.mark.asyncio
    async def test_migration_skips_items_with_summary(self):
        """Items that already have a summary should be skipped."""
        from app.memory.memory_service import MemoryService

        settings = MagicMock(spec=Settings)
        settings.database_url = "sqlite+aiosqlite:///:memory:"
        settings.memory_short_term_max_turns = 10
        settings.case_extraction_enabled = True
        for attr in ("memory_consolidation_similarity", "memory_consolidation_dedup",
                     "memory_consolidation_ttl_days", "memory_consolidation_decay_rate",
                     "memory_consolidation_min_importance", "memory_consolidation_trigger"):
            setattr(settings, attr, None)

        svc = MemoryService(settings)
        svc._generate_fn = MagicMock(return_value='{"summary": "should_not_be_called"}')

        # Pre-populate ltm.items with items that already have summaries
        item_with_summary = Item(
            content="Already has summary", importance=0.5,
            id=1, category="fact", summary="existing summary",
            keywords=["existing"],
        )
        item_without_summary = Item(
            content="Needs migration", importance=0.5,
            id=2, category="fact", summary="",
            keywords=[],
        )
        svc.ltm.items = [item_with_summary, item_without_summary]

        # Mock the LLM to generate summary for the item without one
        llm_call_count = [0]
        def mock_generate(system_prompt, user_msg):
            llm_call_count[0] += 1
            return json.dumps({"summary": "migrated summary", "keywords": ["migrated", "test"]})

        svc._generate_fn = mock_generate
        svc._embed_fn = lambda text: [0.1, 0.2, 0.3]

        # Patch get_db to avoid real DB writes
        with patch("app.memory.memory_service.get_db") as mock_db:
            mock_session = AsyncMock()
            mock_session.execute = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            mock_db.return_value = mock_session
            await svc._safe_migrate_existing_memories()

        # Item with summary should be unchanged
        assert item_with_summary.summary == "existing summary"
        # Item without summary should be migrated
        assert item_without_summary.summary == "migrated summary"
        assert "migrated" in item_without_summary.keywords

    @pytest.mark.asyncio
    async def test_migration_llm_failure_skips_item(self):
        """When LLM fails for one item, migration should continue to the next."""
        from app.memory.memory_service import MemoryService

        settings = MagicMock(spec=Settings)
        settings.database_url = "sqlite+aiosqlite:///:memory:"
        settings.memory_short_term_max_turns = 10
        settings.case_extraction_enabled = True
        for attr in ("memory_consolidation_similarity", "memory_consolidation_dedup",
                     "memory_consolidation_ttl_days", "memory_consolidation_decay_rate",
                     "memory_consolidation_min_importance", "memory_consolidation_trigger"):
            setattr(settings, attr, None)

        svc = MemoryService(settings)
        svc._embed_fn = None

        # Three items: first will cause LLM failure, second will succeed, third has summary
        item1 = Item(content="Item 1 needs migration", importance=0.5, id=1, summary="")
        item2 = Item(content="Item 2 needs migration", importance=0.5, id=2, summary="")
        item3 = Item(content="Item 3 has summary", importance=0.5, id=3, summary="already done")
        svc.ltm.items = [item1, item2, item3]

        call_count = [0]
        def mock_generate(system_prompt, user_msg):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("LLM error for item 1")
            return json.dumps({"summary": "item2 summary", "keywords": ["kw2"]})

        svc._generate_fn = mock_generate

        with patch("app.memory.memory_service.get_db") as mock_db:
            mock_session = AsyncMock()
            mock_session.execute = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            mock_db.return_value = mock_session
            await svc._safe_migrate_existing_memories()

        # Item 1 should remain unmigrated (LLM failed)
        assert item1.summary == ""
        # Item 2 should be migrated
        assert item2.summary == "item2 summary"
        # Item 3 should remain as-is (already had summary)
        assert item3.summary == "already done"

    @pytest.mark.asyncio
    async def test_migration_no_generate_fn_returns_early(self):
        """When no generate_fn is available, migration should return immediately."""
        from app.memory.memory_service import MemoryService

        settings = MagicMock(spec=Settings)
        settings.database_url = "sqlite+aiosqlite:///:memory:"
        settings.memory_short_term_max_turns = 10
        settings.case_extraction_enabled = True
        for attr in ("memory_consolidation_similarity", "memory_consolidation_dedup",
                     "memory_consolidation_ttl_days", "memory_consolidation_decay_rate",
                     "memory_consolidation_min_importance", "memory_consolidation_trigger"):
            setattr(settings, attr, None)

        svc = MemoryService(settings)
        svc._generate_fn = None  # No LLM available

        item = Item(content="Needs migration", importance=0.5, id=1, summary="")
        svc.ltm.items = [item]

        # Should not raise
        await svc._safe_migrate_existing_memories()
        # Item should remain unchanged
        assert item.summary == ""

    @pytest.mark.asyncio
    async def test_migration_invalid_json_skips_item(self):
        """When LLM returns invalid JSON for an item, it should be skipped."""
        from app.memory.memory_service import MemoryService

        settings = MagicMock(spec=Settings)
        settings.database_url = "sqlite+aiosqlite:///:memory:"
        settings.memory_short_term_max_turns = 10
        settings.case_extraction_enabled = True
        for attr in ("memory_consolidation_similarity", "memory_consolidation_dedup",
                     "memory_consolidation_ttl_days", "memory_consolidation_decay_rate",
                     "memory_consolidation_min_importance", "memory_consolidation_trigger"):
            setattr(settings, attr, None)

        svc = MemoryService(settings)
        svc._embed_fn = None

        item1 = Item(content="Item 1", importance=0.5, id=1, summary="")
        item2 = Item(content="Item 2", importance=0.5, id=2, summary="")
        svc.ltm.items = [item1, item2]

        call_count = [0]
        def mock_generate(system_prompt, user_msg):
            call_count[0] += 1
            if call_count[0] == 1:
                return "not valid json"
            return json.dumps({"summary": "item2 ok", "keywords": ["kw2"]})

        svc._generate_fn = mock_generate

        with patch("app.memory.memory_service.get_db") as mock_db:
            mock_session = AsyncMock()
            mock_session.execute = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            mock_db.return_value = mock_session
            await svc._safe_migrate_existing_memories()

        # Item 1 should remain unmigrated (invalid JSON)
        assert item1.summary == ""
        # Item 2 should be migrated
        assert item2.summary == "item2 ok"

    @pytest.mark.asyncio
    async def test_migration_empty_summary_skips_item(self):
        """When LLM returns empty summary, the item should be skipped."""
        from app.memory.memory_service import MemoryService

        settings = MagicMock(spec=Settings)
        settings.database_url = "sqlite+aiosqlite:///:memory:"
        settings.memory_short_term_max_turns = 10
        settings.case_extraction_enabled = True
        for attr in ("memory_consolidation_similarity", "memory_consolidation_dedup",
                     "memory_consolidation_ttl_days", "memory_consolidation_decay_rate",
                     "memory_consolidation_min_importance", "memory_consolidation_trigger"):
            setattr(settings, attr, None)

        svc = MemoryService(settings)
        svc._embed_fn = None

        item = Item(content="Trivial content", importance=0.5, id=1, summary="")
        svc.ltm.items = [item]

        # LLM returns empty summary (content too trivial)
        svc._generate_fn = MagicMock(
            return_value=json.dumps({"summary": "", "keywords": []})
        )

        with patch("app.memory.memory_service.get_db") as mock_db:
            mock_session = AsyncMock()
            mock_session.execute = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            mock_db.return_value = mock_session
            await svc._safe_migrate_existing_memories()

        # Item should remain unmigrated
        assert item.summary == ""
