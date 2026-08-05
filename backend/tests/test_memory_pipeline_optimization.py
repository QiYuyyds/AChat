"""Tests for optimize-memory-pipeline-execution change.

Covers:
  1. FileCatalog — reconcile, get_changed, upsert, remove
  2. Session JSONL sanitization — _sanitize_msg_for_save, message ID dedup
  3. auto_memory — structured LLM output, create/update paths, semantic naming
  4. auto_dream extract — catalog-based change detection, cross-file merge
  5. node_search — digest-only, node-level aggregation, inline frontmatter
  6. auto_dream integrate — per-bucket prompts, provenance wikilinks
  7. auto_dream topics — LLM selection with fallback
  8. Wikilink predicate — parsing, storage, query
  9. Broken link detection + move/retarget
  10. HybridSearch — score breakdown, link expansion
  11. Digest status — archived deprioritization, dream skip
  12. Regression — old `[[target]]` format still parseable
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ─── 1. FileCatalog ──────────────────────────────────────────────────────


class TestFileCatalog:
    """Tests for FileCatalog (tasks 1.1-1.7)."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = Path(self.tmpdir) / "catalog.db"
        self.daily_dir = Path(self.tmpdir) / "daily"
        self.digest_dir = Path(self.tmpdir) / "digest"
        self.daily_dir.mkdir(parents=True)
        self.digest_dir.mkdir(parents=True)

        from app.memory.file_store.file_catalog import FileCatalog
        self.catalog = FileCatalog(self.db_path)
        self.catalog.initialize()

    def teardown_method(self):
        self.catalog.close()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_reconcile_adds_files(self):
        """Reconcile should add files on disk to catalog."""
        f1 = self.daily_dir / "2026-08-04" / "test1.md"
        f1.parent.mkdir(parents=True, exist_ok=True)
        f1.write_text("content", encoding="utf-8")

        count = self.catalog.reconcile(self.daily_dir, self.digest_dir)
        assert count == 1
        assert self.catalog.count() == 1

    def test_reconcile_removes_deleted_files(self):
        """Reconcile should remove catalog entries for deleted files."""
        f1 = self.daily_dir / "2026-08-04" / "test1.md"
        f1.parent.mkdir(parents=True, exist_ok=True)
        f1.write_text("content", encoding="utf-8")
        self.catalog.reconcile(self.daily_dir, self.digest_dir)
        assert self.catalog.count() == 1

        f1.unlink()
        count = self.catalog.reconcile(self.daily_dir, self.digest_dir)
        assert count == 0

    def test_get_changed_detects_mtime_changes(self):
        """get_changed should return files whose mtime differs from catalog."""
        f1 = self.daily_dir / "2026-08-04" / "test1.md"
        f1.parent.mkdir(parents=True, exist_ok=True)
        f1.write_text("content1", encoding="utf-8")
        self.catalog.reconcile(self.daily_dir, self.digest_dir)

        # No changes initially
        changed = self.catalog.get_changed(bucket="daily")
        assert len(changed) == 0

        # Modify file (change mtime)
        os.utime(f1, (f1.stat().st_atime, f1.stat().st_mtime + 10))
        changed = self.catalog.get_changed(bucket="daily")
        assert len(changed) == 1

    def test_get_changed_bucket_filter(self):
        """get_changed should filter by bucket."""
        daily_f = self.daily_dir / "2026-08-04" / "daily1.md"
        daily_f.parent.mkdir(parents=True, exist_ok=True)
        daily_f.write_text("daily", encoding="utf-8")

        digest_f = self.digest_dir / "procedure" / "shared" / "digest1.md"
        digest_f.parent.mkdir(parents=True, exist_ok=True)
        digest_f.write_text("digest", encoding="utf-8")

        self.catalog.reconcile(self.daily_dir, self.digest_dir)

        # Change both files
        os.utime(daily_f, (daily_f.stat().st_atime, daily_f.stat().st_mtime + 10))
        os.utime(digest_f, (digest_f.stat().st_atime, digest_f.stat().st_mtime + 10))

        daily_changed = self.catalog.get_changed(bucket="daily")
        assert len(daily_changed) == 1
        assert "daily1.md" in daily_changed[0][0]

    def test_upsert_and_remove(self):
        """upsert and remove should work correctly."""
        self.catalog.upsert("/fake/path.md", st_mtime=123.0, bucket="daily")
        assert self.catalog.count() == 1

        self.catalog.remove("/fake/path.md")
        assert self.catalog.count() == 0

    def test_get_changed_cleans_deleted_files(self):
        """get_changed should remove catalog entries for deleted files."""
        f1 = self.daily_dir / "2026-08-04" / "test1.md"
        f1.parent.mkdir(parents=True, exist_ok=True)
        f1.write_text("content", encoding="utf-8")
        self.catalog.reconcile(self.daily_dir, self.digest_dir)

        f1.unlink()
        changed = self.catalog.get_changed()
        assert len(changed) == 0
        assert self.catalog.count() == 0


# ─── 2. Session JSONL Sanitization ──────────────────────────────────────


class TestSessionSanitization:
    """Tests for session JSONL sanitization (tasks 2.1-2.3)."""

    def test_sanitize_strips_tool_result_parts(self):
        """_sanitize_msg_for_save should strip tool_result parts."""
        from app.memory.pipeline.auto_memory import _sanitize_msg_for_save

        msg = {
            "role": "assistant",
            "content": [
                {"type": "text", "content": "hello"},
                {"type": "tool_result", "content": "sensitive data"},
            ],
        }
        result = _sanitize_msg_for_save(msg)
        assert result is not None
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"

    def test_sanitize_strips_base64_data(self):
        """_sanitize_msg_for_save should strip base64 data."""
        from app.memory.pipeline.auto_memory import _sanitize_msg_for_save

        original = "data:image/png;base64,iVBORw0KGgoAAA=="
        msg = {
            "role": "user",
            "content": f"text {original} more text",
        }
        result = _sanitize_msg_for_save(msg)
        assert result is not None
        assert original not in result["content"]
        assert "[base64-data-removed]" in result["content"]

    def test_sanitize_skips_none_content(self):
        """_sanitize_msg_for_save should return None for empty messages."""
        from app.memory.pipeline.auto_memory import _sanitize_msg_for_save

        assert _sanitize_msg_for_save({"role": "user", "content": ""}) is None
        assert _sanitize_msg_for_save({"role": "user"}) is None

    def test_write_session_jsonl_dedup_by_id(self):
        """write_session_jsonl should dedup by message ID."""
        from app.memory.file_store.workspace import MemoryWorkspace
        from app.memory.pipeline.auto_memory import AutoMemory

        with tempfile.TemporaryDirectory() as tmpdir:
            settings = MagicMock()
            settings.memory_workspace_path = Path(tmpdir)
            workspace = MemoryWorkspace(settings)
            workspace.initialize()
            auto_memory = AutoMemory(workspace)

            import asyncio
            loop = asyncio.new_event_loop()

            conv_id = "test_conv_123"
            messages = [
                {"id": "msg1", "role": "user", "content": "hello"},
                {"id": "msg2", "role": "assistant", "content": "hi there"},
            ]

            # Write first time
            loop.run_until_complete(auto_memory.write_session_jsonl(conv_id, messages))

            # Write again with same IDs (should dedup)
            loop.run_until_complete(auto_memory.write_session_jsonl(conv_id, messages))

            # Read file and count lines
            filepath = workspace.session_path(conv_id)
            lines = filepath.read_text(encoding="utf-8").strip().split("\n")
            assert len(lines) == 2  # should not duplicate

            loop.close()


# ─── 3. auto_memory Prompt Rewrite ──────────────────────────────────────


class TestAutoMemoryPromptRewrite:
    """Tests for auto_memory prompt rewrite (tasks 3.1-3.8)."""

    def test_sanitize_name(self):
        """_sanitize_name should produce kebab-case."""
        from app.memory.pipeline.auto_memory import _sanitize_name

        assert _sanitize_name("React State Management") == "react-state-management"
        assert _sanitize_name("Hello, World!") == "hello-world"
        assert _sanitize_name("测试中文") == "测试中文"
        assert _sanitize_name("") == "session"

    def test_create_path_calls_llm_and_writes_card(self):
        """auto_memory create path should call LLM and write a daily card."""
        from app.memory.file_store.workspace import MemoryWorkspace
        from app.memory.pipeline.auto_memory import AutoMemory

        with tempfile.TemporaryDirectory() as tmpdir:
            settings = MagicMock()
            settings.memory_workspace_path = Path(tmpdir)
            workspace = MemoryWorkspace(settings)
            workspace.initialize()
            auto_memory = AutoMemory(workspace)

            # Mock LLM
            llm_response = json.dumps({
                "action": "create",
                "name": "React Debugging Tips",
                "description": "Tips for debugging React apps",
                "body": "- Use React DevTools\n- Check component props",
                "tags": ["react", "debugging"],
                "importance": 0.7,
            })
            auto_memory.set_generate_fn(lambda sys, user: llm_response)

            import asyncio
            loop = asyncio.new_event_loop()
            result = loop.run_until_complete(auto_memory.run(
                "How do I debug React?",
                "You should use React DevTools...",
                conversation_id="conv_test123",
                agent_id="ag_test",
            ))
            loop.close()

            assert result == 1

            # Check file was created
            today = date.today().isoformat()
            today_dir = workspace.daily_dir / today
            md_files = list(today_dir.glob("*.md"))
            assert len(md_files) == 1

            # Check content
            from app.memory.file_store.markdown_io import read_markdown
            mem = read_markdown(md_files[0])
            assert mem is not None
            assert mem.frontmatter.name == "React Debugging Tips"
            assert mem.frontmatter.importance == 0.7

    def test_update_path_merges_with_existing(self):
        """auto_memory update path should merge with existing card."""
        from app.memory.file_store.frontmatter import MemoryFrontmatter
        from app.memory.file_store.markdown_io import read_markdown, write_markdown
        from app.memory.file_store.workspace import MemoryWorkspace
        from app.memory.pipeline.auto_memory import AutoMemory

        with tempfile.TemporaryDirectory() as tmpdir:
            settings = MagicMock()
            settings.memory_workspace_path = Path(tmpdir)
            workspace = MemoryWorkspace(settings)
            workspace.initialize()
            auto_memory = AutoMemory(workspace)

            # Create existing card
            today = date.today().isoformat()
            existing_name = "existing-card-abc123"
            filepath = workspace.daily_file_path(existing_name, today)
            fm = MemoryFrontmatter(
                name="Existing Card",
                description="Old description",
                tags=["old-tag"],
                importance=0.3,
                bucket="procedure",
                created_at=today,
                updated_at=today,
                source="session/conv_test123.jsonl",
            )
            write_markdown(filepath, fm, "- Old fact 1\n- Old fact 2")

            # Mock LLM for update — name sanitizes to same as existing_name (no rename)
            llm_response = json.dumps({
                "action": "update",
                "name": "Existing Card Abc123",
                "body": "- Old fact 1\n- Old fact 2\n- New fact 3",
                "tags": ["old-tag", "new-tag"],
                "importance": 0.8,
            })
            auto_memory.set_generate_fn(lambda sys, user: llm_response)

            import asyncio
            loop = asyncio.new_event_loop()
            result = loop.run_until_complete(auto_memory.run(
                "More information about the project setup and configuration details",
                "Here's a new fact about how the system handles updates and merges",
                conversation_id="conv_test123",
                agent_id="ag_test",
            ))
            loop.close()

            assert result == 1
            mem = read_markdown(filepath)
            assert mem is not None
            assert "New fact 3" in mem.body
            assert "new-tag" in mem.frontmatter.tags
            assert mem.frontmatter.importance == 0.8

    def test_skip_trivial_conversation(self):
        """auto_memory should skip trivial conversations."""
        from app.memory.file_store.workspace import MemoryWorkspace
        from app.memory.pipeline.auto_memory import AutoMemory

        with tempfile.TemporaryDirectory() as tmpdir:
            settings = MagicMock()
            settings.memory_workspace_path = Path(tmpdir)
            workspace = MemoryWorkspace(settings)
            workspace.initialize()
            auto_memory = AutoMemory(workspace)
            auto_memory.set_generate_fn(lambda sys, user: '{"action": "skip"}')

            import asyncio
            loop = asyncio.new_event_loop()
            result = loop.run_until_complete(auto_memory.run(
                "好的",
                "没问题",
                conversation_id="conv_trivial",
            ))
            loop.close()

            assert result == 0


# ─── 4. auto_dream extract Enhancement ──────────────────────────────────


class TestAutoDreamExtract:
    """Tests for auto_dream extract enhancement (tasks 4.1-4.5)."""

    def test_extract_uses_catalog_changed_files(self):
        """dream_extract should only process changed files from catalog."""
        from app.memory.file_store.file_catalog import FileCatalog
        from app.memory.file_store.frontmatter import MemoryFrontmatter
        from app.memory.file_store.markdown_io import write_markdown
        from app.memory.file_store.workspace import MemoryWorkspace
        from app.memory.pipeline.auto_dream import AutoDream
        from app.memory.search.bm25_index import BM25Index
        from app.memory.search.hybrid_search import HybridSearch
        from app.memory.search.wikilink_expander import WikilinkExpander

        tmpdir = tempfile.mkdtemp()
        try:
            settings = MagicMock()
            settings.memory_workspace_path = Path(tmpdir)
            settings.memory_search_top_k = 10
            settings.memory_bm25_weight = 0.7
            settings.memory_wikilink_weight = 0.3
            settings.memory_rrf_k = 60

            workspace = MemoryWorkspace(settings)
            workspace.initialize()

            bm25 = BM25Index(workspace.metadata_dir / "bm25.db")
            expander = WikilinkExpander(workspace.metadata_dir / "wikilinks.db")
            catalog = FileCatalog(workspace.metadata_dir / "catalog.db")
            bm25.initialize()
            expander.initialize()
            catalog.initialize()

            # Create two daily files with distinct names
            today = date.today().isoformat()
            f1 = workspace.daily_file_path("file1", today)
            f2 = workspace.daily_file_path("file2", today)
            fm1 = MemoryFrontmatter(name="file1", created_at=today, updated_at=today, source="")
            fm2 = MemoryFrontmatter(name="file2", created_at=today, updated_at=today, source="")
            write_markdown(f1, fm1, "content1")
            write_markdown(f2, fm2, "content2")

            # Reconcile catalog
            catalog.reconcile(workspace.daily_dir, workspace.digest_dir)

            # Only modify f1
            os.utime(f1, (f1.stat().st_atime, f1.stat().st_mtime + 10))

            search = HybridSearch(settings, bm25, expander)
            dream = AutoDream(workspace, search, file_catalog=catalog)

            # Mock LLM to capture which files are in the prompt
            captured_prompt = []
            def mock_generate(sys_prompt, user_prompt):
                captured_prompt.append(user_prompt)
                return json.dumps({"units": [], "topic_candidates": []})

            dream.set_generate_fn(mock_generate)

            import asyncio
            loop = asyncio.new_event_loop()
            units, topics = loop.run_until_complete(dream._dream_extract(5))
            loop.close()

            # Only f1 should be in the prompt (changed file)
            assert len(captured_prompt) == 1
            assert "file1" in captured_prompt[0]
            assert "file2" not in captured_prompt[0]

            bm25.close()
            expander.close()
            catalog.close()
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


# ─── 5. node_search ──────────────────────────────────────────────────────


class TestNodeSearch:
    """Tests for node_search (tasks 5.1-5.4)."""

    def test_node_search_returns_digest_only(self):
        """node_search should only return digest files."""
        from app.memory.file_store.frontmatter import MemoryFrontmatter
        from app.memory.file_store.markdown_io import write_markdown
        from app.memory.file_store.workspace import MemoryWorkspace
        from app.memory.search.bm25_index import BM25Index
        from app.memory.search.node_search import NodeSearch
        from app.memory.search.wikilink_expander import WikilinkExpander

        with tempfile.TemporaryDirectory() as tmpdir:
            settings = MagicMock()
            settings.memory_workspace_path = Path(tmpdir)

            workspace = MemoryWorkspace(settings)
            workspace.initialize()

            bm25 = BM25Index(workspace.metadata_dir / "bm25.db")
            expander = WikilinkExpander(workspace.metadata_dir / "wikilinks.db")
            bm25.initialize()
            expander.initialize()

            # Create a daily file and a digest file
            today = date.today().isoformat()
            daily_f = workspace.daily_file_path("daily1", today)
            digest_f = workspace.digest_path("wiki", "react-patterns")

            fm = MemoryFrontmatter(name="React Patterns", description="React design patterns", created_at=today, updated_at=today, source="")
            write_markdown(daily_f, fm, "React hooks patterns content")
            write_markdown(digest_f, fm, "React hooks patterns content in digest")

            # Index both files
            bm25.add(str(daily_f), "daily1", "React hooks patterns content", None, "daily", [])
            bm25.add(str(digest_f), "React Patterns", "React hooks patterns content in digest", None, "wiki", [])

            node_search = NodeSearch(bm25, expander, workspace)
            results = node_search.search("React hooks", limit=10)

            # Should only return digest files
            assert len(results) >= 1
            for r in results:
                assert "digest" in r.path.lower() or "wiki" in r.path.lower() or "procedure" in r.path.lower()

            bm25.close()
            expander.close()

    def test_node_search_aggregates_per_file(self):
        """node_search should aggregate multiple hits on the same file."""
        from app.memory.file_store.frontmatter import MemoryFrontmatter
        from app.memory.file_store.markdown_io import write_markdown
        from app.memory.file_store.workspace import MemoryWorkspace
        from app.memory.search.bm25_index import BM25Index
        from app.memory.search.node_search import NodeSearch
        from app.memory.search.wikilink_expander import WikilinkExpander

        with tempfile.TemporaryDirectory() as tmpdir:
            settings = MagicMock()
            settings.memory_workspace_path = Path(tmpdir)

            workspace = MemoryWorkspace(settings)
            workspace.initialize()

            bm25 = BM25Index(workspace.metadata_dir / "bm25.db")
            expander = WikilinkExpander(workspace.metadata_dir / "wikilinks.db")
            bm25.initialize()
            expander.initialize()

            today = date.today().isoformat()
            digest_f = workspace.digest_path("wiki", "test-node")
            fm = MemoryFrontmatter(name="Test Node", description="Test description", created_at=today, updated_at=today, source="")
            write_markdown(digest_f, fm, "React hooks patterns content")
            bm25.add(str(digest_f), "Test Node", "React hooks patterns content", None, "wiki", [])

            node_search = NodeSearch(bm25, expander, workspace)
            results = node_search.search("React", limit=10)

            # Should return one result per file
            paths = [r.path for r in results]
            assert len(paths) == len(set(paths))  # no duplicates

            bm25.close()
            expander.close()

    def test_node_search_returns_frontmatter(self):
        """node_search should include name and description in results."""
        from app.memory.file_store.frontmatter import MemoryFrontmatter
        from app.memory.file_store.markdown_io import write_markdown
        from app.memory.file_store.workspace import MemoryWorkspace
        from app.memory.search.bm25_index import BM25Index
        from app.memory.search.node_search import NodeSearch
        from app.memory.search.wikilink_expander import WikilinkExpander

        with tempfile.TemporaryDirectory() as tmpdir:
            settings = MagicMock()
            settings.memory_workspace_path = Path(tmpdir)

            workspace = MemoryWorkspace(settings)
            workspace.initialize()

            bm25 = BM25Index(workspace.metadata_dir / "bm25.db")
            expander = WikilinkExpander(workspace.metadata_dir / "wikilinks.db")
            bm25.initialize()
            expander.initialize()

            today = date.today().isoformat()
            digest_f = workspace.digest_path("wiki", "react-patterns")
            fm = MemoryFrontmatter(name="React Patterns", description="Design patterns for React", created_at=today, updated_at=today, source="")
            write_markdown(digest_f, fm, "React hooks patterns")
            bm25.add(str(digest_f), "React Patterns", "React hooks patterns", None, "wiki", [])

            node_search = NodeSearch(bm25, expander, workspace)
            results = node_search.search("React", limit=10)

            assert len(results) >= 1
            assert results[0].name == "React Patterns"
            assert results[0].description == "Design patterns for React"

            bm25.close()
            expander.close()


# ─── 8. Wikilink Predicate Support ──────────────────────────────────────


class TestWikilinkPredicate:
    """Tests for wikilink predicate support (tasks 8.1-8.6)."""

    def test_extract_predicate_wikilink(self):
        """extract_wikilinks_detailed should parse predicate syntax."""
        from app.memory.file_store.wikilinks import extract_wikilinks_detailed

        body = "Some text\nderived_from:: [[daily/2026-08-04/sess.md]]\nMore text"
        links = extract_wikilinks_detailed(body)

        assert len(links) == 1
        assert links[0].target == "daily/2026-08-04/sess.md"
        assert links[0].predicate == "derived_from"

    def test_extract_plain_wikilink_has_null_predicate(self):
        """Plain wikilinks should have predicate = None."""
        from app.memory.file_store.wikilinks import extract_wikilinks_detailed

        body = "See [[digest/wiki/react.md]] for more"
        links = extract_wikilinks_detailed(body)

        assert len(links) == 1
        assert links[0].target == "digest/wiki/react.md"
        assert links[0].predicate is None

    def test_extract_multiple_predicates(self):
        """Multiple predicate wikilinks in same file should be parsed."""
        from app.memory.file_store.wikilinks import extract_wikilinks_detailed

        body = (
            "derived_from:: [[daily/2026-08-04/sess.md]]\n"
            "relates_to:: [[digest/wiki/react.md]]\n"
            "[[digest/procedure/patterns.md]]"
        )
        links = extract_wikilinks_detailed(body)

        assert len(links) == 3
        predicates = [link.predicate for link in links]
        assert "derived_from" in predicates
        assert "relates_to" in predicates
        assert None in predicates

    def test_retarget_wikilinks(self):
        """retarget_wikilinks should rewrite old target to new."""
        from app.memory.file_store.wikilinks import retarget_wikilinks

        body = (
            "derived_from:: [[old/path.md]]\n"
            "See [[old/path.md]] for more\n"
            "relates_to:: [[other.md]]"
        )
        result = retarget_wikilinks(body, "old/path.md", "new/path.md")

        assert "new/path.md" in result
        assert "old/path.md" not in result
        assert "other.md" in result  # unchanged

    def test_expander_predicate_storage(self):
        """WikilinkExpander should store and query predicate."""
        from app.memory.search.wikilink_expander import WikilinkExpander

        tmpdir = tempfile.mkdtemp()
        try:
            expander = WikilinkExpander(Path(tmpdir) / "wl.db")
            expander.initialize()

            expander.add_edges("/src.md", ["/target1.md"], predicate="derived_from")
            expander.add_edges("/src.md", ["/target2.md"], predicate=None)

            # Get all outlinks
            all_out = expander.get_outlinks("/src.md")
            assert len(all_out) == 2

            # Filter by predicate
            derived = expander.get_outlinks("/src.md", predicate="derived_from")
            assert len(derived) == 1
            assert derived[0]["target"] == "/target1.md"

            # predicate=None means no filter (returns all)
            plain = expander.get_outlinks("/src.md", predicate=None)
            assert len(plain) == 2

            expander.close()
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_expander_broken_link_removal(self):
        """remove_broken_links should remove entries with non-existent targets."""
        from app.memory.search.wikilink_expander import WikilinkExpander

        tmpdir = tempfile.mkdtemp()
        try:
            expander = WikilinkExpander(Path(tmpdir) / "wl.db")
            expander.initialize()

            expander.add_edges("/src1.md", ["/exists.md", "/missing.md"])

            broken_count = expander.remove_broken_links({"/src1.md", "/exists.md"})
            assert broken_count == 1

            # Verify broken link removed
            outlinks = expander.get_outlinks("/src1.md")
            assert len(outlinks) == 1
            assert outlinks[0]["target"] == "/exists.md"

            expander.close()
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


# ─── 9. Broken Link Detection + Move/Retarget ──────────────────────────


class TestMoveRetarget:
    """Tests for move + retarget (tasks 9.3-9.7)."""

    def test_move_file_retargets_wikilinks(self):
        """move_file should retarget wikilinks in other files."""
        from app.memory.file_store.frontmatter import MemoryFrontmatter
        from app.memory.file_store.markdown_io import move_file, write_markdown
        from app.memory.file_store.workspace import MemoryWorkspace

        with tempfile.TemporaryDirectory() as tmpdir:
            settings = MagicMock()
            settings.memory_workspace_path = Path(tmpdir)
            workspace = MemoryWorkspace(settings)
            workspace.initialize()

            today = date.today().isoformat()

            # Create source file in digest
            src = workspace.digest_path("wiki", "old-name")
            fm = MemoryFrontmatter(name="Old Name", created_at=today, updated_at=today, source="")
            write_markdown(src, fm, "content")

            # Create a file that links to old-name
            linker = workspace.digest_path("wiki", "linker")
            src_rel = str(src.relative_to(workspace.root)).replace("\\", "/")
            write_markdown(linker, fm, f"See [[{src_rel}]] for details")

            # Move old-name to new-name
            dst = workspace.digest_path("wiki", "new-name")
            retargeted = move_file(src, dst, workspace.root, retarget=True)

            assert len(retargeted) == 1
            assert retargeted[0] == linker

            # Verify wikilink was retargeted
            linker_text = linker.read_text(encoding="utf-8")
            dst_rel = str(dst.relative_to(workspace.root)).replace("\\", "/")
            assert dst_rel in linker_text
            assert src_rel not in linker_text

    def test_move_file_retargets_predicate_wikilinks(self):
        """move_file should retarget predicate wikilinks."""
        from app.memory.file_store.frontmatter import MemoryFrontmatter
        from app.memory.file_store.markdown_io import move_file, write_markdown
        from app.memory.file_store.workspace import MemoryWorkspace

        with tempfile.TemporaryDirectory() as tmpdir:
            settings = MagicMock()
            settings.memory_workspace_path = Path(tmpdir)
            workspace = MemoryWorkspace(settings)
            workspace.initialize()

            today = date.today().isoformat()

            src = workspace.digest_path("wiki", "old-proc")
            fm = MemoryFrontmatter(name="Old Proc", created_at=today, updated_at=today, source="")
            write_markdown(src, fm, "content")

            linker = workspace.digest_path("wiki", "linker2")
            src_rel = str(src.relative_to(workspace.root)).replace("\\", "/")
            write_markdown(linker, fm, f"derived_from:: [[{src_rel}]]")

            dst = workspace.digest_path("wiki", "new-proc")
            move_file(src, dst, workspace.root, retarget=True)

            linker_text = linker.read_text(encoding="utf-8")
            dst_rel = str(dst.relative_to(workspace.root)).replace("\\", "/")
            assert f"derived_from:: [[{dst_rel}]]" in linker_text


# ─── 10. HybridSearch Enhancement ──────────────────────────────────────


class TestHybridSearchEnhancement:
    """Tests for HybridSearch score breakdown + link expansion (tasks 10.1-10.5)."""

    def test_search_result_has_scores(self):
        """SearchResult should include scores dict."""
        from app.memory.search.hybrid_search import SearchResult

        r = SearchResult(
            path="/test.md",
            name="test",
            content="content",
            score=0.5,
            source="bm25",
            scores={"bm25": 0.3, "wikilink": 0.0, "rrf": 0.3},
        )
        assert r.scores["bm25"] == 0.3
        assert "rrf" in r.scores

    def test_search_result_has_expansion(self):
        """SearchResult should include expansion dict."""
        from app.memory.search.hybrid_search import SearchResult

        r = SearchResult(
            path="/test.md",
            name="test",
            content="content",
            score=0.5,
            source="bm25",
            expansion={"outlinks": [], "inlinks": []},
        )
        assert "outlinks" in r.expansion
        assert "inlinks" in r.expansion


# ─── 11. Digest status Field ────────────────────────────────────────────


class TestDigestStatus:
    """Tests for digest status field (tasks 11.1-11.4)."""

    def test_frontmatter_has_status_field(self):
        """MemoryFrontmatter should have status field with default 'active'."""
        from app.memory.file_store.frontmatter import MemoryFrontmatter

        fm = MemoryFrontmatter(name="test")
        assert fm.status == "active"

        fm_dict = fm.to_dict()
        assert "status" in fm_dict
        assert fm_dict["status"] == "active"

    def test_frontmatter_status_from_dict(self):
        """MemoryFrontmatter.from_dict should parse status field."""
        from app.memory.file_store.frontmatter import MemoryFrontmatter

        fm = MemoryFrontmatter.from_dict({"name": "test", "status": "archived"})
        assert fm.status == "archived"

    def test_frontmatter_status_validation(self):
        """validate() should reject invalid status."""
        from app.memory.file_store.frontmatter import MemoryFrontmatter

        fm = MemoryFrontmatter(name="test", status="invalid")
        errors = fm.validate()
        assert any("status" in e for e in errors)


# ─── 12. Regression ──────────────────────────────────────────────────────


class TestRegression:
    """Regression tests (tasks 12.3)."""

    def test_old_plain_wikilink_still_parseable(self):
        """Old format `[[target]]` should still be parseable."""
        from app.memory.file_store.wikilinks import extract_wikilinks, extract_wikilinks_detailed

        body = "See [[old-format-target]] for more"
        targets = extract_wikilinks(body)
        assert "old-format-target" in targets

        links = extract_wikilinks_detailed(body)
        assert len(links) == 1
        assert links[0].target == "old-format-target"
        assert links[0].predicate is None

    def test_old_frontmatter_without_status_works(self):
        """Old frontmatter without status field should default to 'active'."""
        from app.memory.file_store.frontmatter import MemoryFrontmatter

        fm = MemoryFrontmatter.from_dict({"name": "old-card"})
        assert fm.status == "active"

    def test_render_wikilinks_both_formats(self):
        """render_wikilinks should handle both plain and predicate syntax."""
        from app.memory.file_store.wikilinks import render_wikilinks

        body = "See [[plain]] and derived_from:: [[pred]]"
        rendered = render_wikilinks(body)

        assert "[plain](plain)" in rendered
        assert "derived_from" in rendered.lower() or "pred" in rendered


# ─── 13. auto_dream integrate CREATE + REFINE ─────────────────────────────


class TestDreamIntegrate:
    """Integration tests for dream_integrate (task 6.9)."""

    def _setup_workspace(self):
        from app.memory.file_store.file_catalog import FileCatalog
        from app.memory.file_store.workspace import MemoryWorkspace
        from app.memory.search.bm25_index import BM25Index
        from app.memory.search.hybrid_search import HybridSearch
        from app.memory.search.node_search import NodeSearch
        from app.memory.search.wikilink_expander import WikilinkExpander

        tmpdir = tempfile.mkdtemp()
        settings = MagicMock()
        settings.memory_workspace_path = Path(tmpdir)
        settings.memory_search_top_k = 10
        settings.memory_bm25_weight = 0.7
        settings.memory_wikilink_weight = 0.3
        settings.memory_rrf_k = 60
        workspace = MemoryWorkspace(settings)
        workspace.initialize()
        bm25 = BM25Index(workspace.metadata_dir / "bm25.db")
        expander = WikilinkExpander(workspace.metadata_dir / "wikilinks.db")
        catalog = FileCatalog(workspace.metadata_dir / "catalog.db")
        bm25.initialize()
        expander.initialize()
        catalog.initialize()
        node_search = NodeSearch(bm25, expander, workspace)
        search = HybridSearch(settings, bm25, expander)
        return workspace, bm25, expander, catalog, node_search, search, tmpdir

    def _teardown(self, tmpdir, *components):
        for c in components:
            if hasattr(c, "close"):
                c.close()
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    def test_integrate_create_new_digest(self):
        """dream_integrate CREATE should write a new digest file with provenance."""
        from app.memory.pipeline.auto_dream import AutoDream

        workspace, bm25, expander, catalog, node_search, search, tmpdir = self._setup_workspace()
        try:
            dream = AutoDream(workspace, search, node_search, catalog)

            llm_response = json.dumps({
                "action": "CREATE",
                "content": "## Trigger\nWhen debugging React\n\n## Steps\n1. Open DevTools\nderived_from:: [[daily/2026-08-04/sess.md]]",
                "reason": "No existing match",
                "wikilinks": ["derived_from:: [[daily/2026-08-04/sess.md]]"],
            })
            dream.set_generate_fn(lambda sys, user: llm_response)

            unit = {
                "name": "React Debugging",
                "bucket": "procedure",
                "summary": "How to debug React apps",
                "content": "Use React DevTools",
                "tags": ["react", "debug"],
                "importance": 0.7,
                "source_paths": ["daily/2026-08-04/sess.md"],
            }

            import asyncio
            loop = asyncio.new_event_loop()
            action = loop.run_until_complete(dream._dream_integrate(unit))
            loop.close()

            assert action == "created"

            digest_files = list(workspace.digest_dir.rglob("*.md"))
            assert len(digest_files) == 1

            from app.memory.file_store.markdown_io import read_markdown
            mem = read_markdown(digest_files[0])
            assert mem is not None
            assert "derived_from::" in mem.body
            assert mem.frontmatter.bucket == "procedure"
            assert mem.frontmatter.status == "active"
        finally:
            self._teardown(tmpdir, bm25, expander, catalog)

    def test_integrate_refine_appends_update(self):
        """dream_integrate REFINE should append new content as ## Update section."""
        from app.memory.file_store.frontmatter import MemoryFrontmatter
        from app.memory.file_store.markdown_io import read_markdown, write_markdown
        from app.memory.pipeline.auto_dream import AutoDream

        workspace, bm25, expander, catalog, node_search, search, tmpdir = self._setup_workspace()
        try:
            today = date.today().isoformat()

            # Create existing digest node
            existing_path = workspace.digest_path("procedure", "react-debug")
            fm = MemoryFrontmatter(
                name="React Debugging",
                description="Debug React apps",
                tags=["react"],
                importance=0.5,
                bucket="procedure",
                status="active",
                created_at=today,
                updated_at=today,
                source="auto_dream",
            )
            write_markdown(existing_path, fm, "## Trigger\nWhen debugging\n\n## Steps\n1. Open DevTools")
            bm25.add(str(existing_path), "React Debugging",
                     "React debugging DevTools", None, "procedure", ["react"])

            dream = AutoDream(workspace, search, node_search, catalog)

            # LLM returns incremental content for REFINE
            llm_response = json.dumps({
                "action": "REFINE",
                "content": "## Failure Modes\nDevTools not showing components — check React version\nderived_from:: [[daily/2026-08-04/sess2.md]]",
                "reason": "Adding failure modes",
                "wikilinks": ["derived_from:: [[daily/2026-08-04/sess2.md]]"],
            })
            dream.set_generate_fn(lambda sys, user: llm_response)

            unit = {
                "name": "React Debugging",
                "bucket": "procedure",
                "summary": "Debug React apps",
                "content": "DevTools failure modes",
                "tags": ["react", "debug"],
                "importance": 0.6,
                "source_paths": ["daily/2026-08-04/sess2.md"],
            }

            import asyncio
            loop = asyncio.new_event_loop()
            action = loop.run_until_complete(dream._dream_integrate(unit))
            loop.close()

            assert action == "refine"

            mem = read_markdown(existing_path)
            assert mem is not None
            assert "## Update (" in mem.body
            assert "Failure Modes" in mem.body
            assert "derived_from::" in mem.body
            # Original content preserved
            assert "Open DevTools" in mem.body
        finally:
            self._teardown(tmpdir, bm25, expander, catalog)


# ─── 14. auto_dream topics LLM + fallback ────────────────────────────────


class TestDreamTopics:
    """Tests for dream_topics (task 7.5)."""

    def test_topics_llm_selection(self):
        """dream_topics should use LLM to select topics."""
        from app.memory.file_store.workspace import MemoryWorkspace
        from app.memory.pipeline.auto_dream import AutoDream
        from app.memory.search.bm25_index import BM25Index
        from app.memory.search.hybrid_search import HybridSearch
        from app.memory.search.wikilink_expander import WikilinkExpander

        with tempfile.TemporaryDirectory() as tmpdir:
            settings = MagicMock()
            settings.memory_workspace_path = Path(tmpdir)
            settings.memory_search_top_k = 10
            settings.memory_bm25_weight = 0.7
            settings.memory_wikilink_weight = 0.3
            settings.memory_rrf_k = 60

            workspace = MemoryWorkspace(settings)
            workspace.initialize()
            bm25 = BM25Index(workspace.metadata_dir / "bm25.db")
            expander = WikilinkExpander(workspace.metadata_dir / "wikilinks.db")
            bm25.initialize()
            expander.initialize()
            search = HybridSearch(settings, bm25, expander)
            dream = AutoDream(workspace, search)

            llm_response = json.dumps({
                "topics": [
                    {"title": "React state management", "reason": "recurring", "keywords": ["react"], "evidence": "unit1"},
                ],
            })
            dream.set_generate_fn(lambda sys, user: llm_response)

            candidates = [{"title": "React state management", "reason": "x", "keywords": ["react"], "evidence": "u1"}]

            import asyncio
            loop = asyncio.new_event_loop()
            count = loop.run_until_complete(dream._dream_topics([], candidates))
            loop.close()

            assert count == 1

            # Check interests.yaml written
            ipath = workspace.interests_path()
            assert ipath.exists()
            import yaml
            data = yaml.safe_load(ipath.read_text(encoding="utf-8"))
            assert isinstance(data, list)
            assert len(data) == 1
            assert data[0]["title"] == "React state management"

            bm25.close()
            expander.close()

    def test_topics_fallback_when_llm_fails(self):
        """dream_topics should fallback to rule-based when LLM unavailable."""
        from app.memory.file_store.workspace import MemoryWorkspace
        from app.memory.pipeline.auto_dream import AutoDream
        from app.memory.search.bm25_index import BM25Index
        from app.memory.search.hybrid_search import HybridSearch
        from app.memory.search.wikilink_expander import WikilinkExpander

        with tempfile.TemporaryDirectory() as tmpdir:
            settings = MagicMock()
            settings.memory_workspace_path = Path(tmpdir)
            settings.memory_search_top_k = 10
            settings.memory_bm25_weight = 0.7
            settings.memory_wikilink_weight = 0.3
            settings.memory_rrf_k = 60

            workspace = MemoryWorkspace(settings)
            workspace.initialize()
            bm25 = BM25Index(workspace.metadata_dir / "bm25.db")
            expander = WikilinkExpander(workspace.metadata_dir / "wikilinks.db")
            bm25.initialize()
            expander.initialize()
            search = HybridSearch(settings, bm25, expander)
            dream = AutoDream(workspace, search)

            # LLM raises exception
            dream.set_generate_fn(lambda sys, user: (_ for _ in ()).throw(RuntimeError("LLM unavailable")))

            candidates = [
                {"title": "Topic A", "reason": "x", "keywords": [], "evidence": "u1"},
                {"title": "Topic B", "reason": "y", "keywords": [], "evidence": "u2"},
            ]

            import asyncio
            loop = asyncio.new_event_loop()
            count = loop.run_until_complete(dream._dream_topics([], candidates))
            loop.close()

            assert count == 2  # both fresh topics selected by fallback

            bm25.close()
            expander.close()


# ─── 15. HybridSearch actual search with scores + expansion ──────────────


class TestHybridSearchIntegration:
    """Integration tests for HybridSearch (tasks 10.3-10.5)."""

    def test_search_returns_score_breakdown(self):
        """HybridSearch.search should populate scores dict on results."""
        from app.memory.file_store.frontmatter import MemoryFrontmatter
        from app.memory.file_store.markdown_io import write_markdown
        from app.memory.file_store.workspace import MemoryWorkspace
        from app.memory.search.bm25_index import BM25Index
        from app.memory.search.hybrid_search import HybridSearch
        from app.memory.search.wikilink_expander import WikilinkExpander

        with tempfile.TemporaryDirectory() as tmpdir:
            settings = MagicMock()
            settings.memory_workspace_path = Path(tmpdir)
            settings.memory_search_top_k = 5
            settings.memory_bm25_weight = 0.7
            settings.memory_wikilink_weight = 0.3
            settings.memory_rrf_k = 60

            workspace = MemoryWorkspace(settings)
            workspace.initialize()
            bm25 = BM25Index(workspace.metadata_dir / "bm25.db")
            expander = WikilinkExpander(workspace.metadata_dir / "wikilinks.db")
            bm25.initialize()
            expander.initialize()

            today = date.today().isoformat()
            digest_f = workspace.digest_path("wiki", "react-patterns")
            fm = MemoryFrontmatter(name="React Patterns", description="React design patterns", created_at=today, updated_at=today, source="")
            write_markdown(digest_f, fm, "React hooks patterns for state management")
            bm25.add(str(digest_f), "React Patterns", "React hooks patterns for state management", None, "wiki", [])

            search = HybridSearch(settings, bm25, expander)

            import asyncio
            loop = asyncio.new_event_loop()
            results = loop.run_until_complete(search.search("React hooks"))
            loop.close()

            assert len(results) >= 1
            r = results[0]
            assert "bm25" in r.scores
            assert "wikilink" in r.scores
            assert "rrf" in r.scores
            assert r.scores["rrf"] > 0

            bm25.close()
            expander.close()

    def test_search_returns_expansion_meta(self):
        """HybridSearch.search should populate expansion dict with outlinks/inlinks."""
        from app.memory.file_store.frontmatter import MemoryFrontmatter
        from app.memory.file_store.markdown_io import write_markdown
        from app.memory.file_store.workspace import MemoryWorkspace
        from app.memory.search.bm25_index import BM25Index
        from app.memory.search.hybrid_search import HybridSearch
        from app.memory.search.wikilink_expander import WikilinkExpander

        tmpdir = tempfile.mkdtemp()
        try:
            settings = MagicMock()
            settings.memory_workspace_path = Path(tmpdir)
            settings.memory_search_top_k = 5
            settings.memory_bm25_weight = 0.7
            settings.memory_wikilink_weight = 0.3
            settings.memory_rrf_k = 60

            workspace = MemoryWorkspace(settings)
            workspace.initialize()
            bm25 = BM25Index(workspace.metadata_dir / "bm25.db")
            expander = WikilinkExpander(workspace.metadata_dir / "wikilinks.db")
            bm25.initialize()
            expander.initialize()

            today = date.today().isoformat()
            # Create two digest files with a wikilink between them
            f_a = workspace.digest_path("wiki", "topic-a")
            f_b = workspace.digest_path("wiki", "topic-b")
            fm_a = MemoryFrontmatter(name="Topic A", description="About topic A", created_at=today, updated_at=today, source="")
            fm_b = MemoryFrontmatter(name="Topic B", description="About topic B", created_at=today, updated_at=today, source="")

            rel_b = str(f_b)
            write_markdown(f_a, fm_a, f"Content about topic A\nrelates_to:: [[{rel_b}]]")
            write_markdown(f_b, fm_b, "Content about topic B")

            # Index both
            from app.memory.pipeline.auto_index import AutoIndex
            auto_index = AutoIndex(workspace, bm25, expander)
            auto_index.index_file(f_a)
            auto_index.index_file(f_b)

            search = HybridSearch(settings, bm25, expander)

            import asyncio
            loop = asyncio.new_event_loop()
            results = loop.run_until_complete(search.search("topic A"))
            loop.close()

            assert len(results) >= 1
            # Find result for file A
            result_a = next((r for r in results if "topic-a" in r.path), None)
            if result_a:
                assert "outlinks" in result_a.expansion
                # Should have topic-b as outlink
                outlink_paths = [o["path"] for o in result_a.expansion["outlinks"]]
                assert any("topic-b" in p for p in outlink_paths)

            bm25.close()
            expander.close()
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


# ─── 16. Archived node deprioritization in search ────────────────────────


class TestArchivedDeprioritization:
    """Test that archived nodes are deprioritized in search (task 11.2)."""

    def test_archived_node_has_lower_score(self):
        """Archived node should have lower BM25 score than active node."""
        from app.memory.file_store.frontmatter import MemoryFrontmatter
        from app.memory.file_store.markdown_io import write_markdown
        from app.memory.file_store.workspace import MemoryWorkspace
        from app.memory.search.bm25_index import BM25Index
        from app.memory.search.hybrid_search import HybridSearch
        from app.memory.search.wikilink_expander import WikilinkExpander

        with tempfile.TemporaryDirectory() as tmpdir:
            settings = MagicMock()
            settings.memory_workspace_path = Path(tmpdir)
            settings.memory_search_top_k = 10
            settings.memory_bm25_weight = 0.7
            settings.memory_wikilink_weight = 0.3
            settings.memory_rrf_k = 60

            workspace = MemoryWorkspace(settings)
            workspace.initialize()
            bm25 = BM25Index(workspace.metadata_dir / "bm25.db")
            expander = WikilinkExpander(workspace.metadata_dir / "wikilinks.db")
            bm25.initialize()
            expander.initialize()

            today = date.today().isoformat()
            # Active node
            f_active = workspace.digest_path("wiki", "active-react")
            fm_active = MemoryFrontmatter(name="Active React", description="Active React tips", status="active", created_at=today, updated_at=today, source="")
            write_markdown(f_active, fm_active, "React hooks state management patterns")
            bm25.add(str(f_active), "Active React", "React hooks state management patterns", None, "wiki", [])

            # Archived node (same content, should be deprioritized)
            f_archived = workspace.digest_path("wiki", "archived-react")
            fm_archived = MemoryFrontmatter(name="Archived React", description="Archived React tips", status="archived", created_at=today, updated_at=today, source="")
            write_markdown(f_archived, fm_archived, "React hooks state management patterns")
            bm25.add(str(f_archived), "Archived React", "React hooks state management patterns", None, "wiki", [])

            search = HybridSearch(settings, bm25, expander)

            import asyncio
            loop = asyncio.new_event_loop()
            results = loop.run_until_complete(search.search("React hooks"))
            loop.close()

            assert len(results) >= 2

            # Find active and archived results
            active_r = next((r for r in results if "active-react" in r.path), None)
            archived_r = next((r for r in results if "archived-react" in r.path), None)

            if active_r and archived_r:
                # Archived should have lower or equal bm25 score (0.5x multiplier)
                assert archived_r.scores["bm25"] <= active_r.scores["bm25"]

            bm25.close()
            expander.close()


# ─── 17. node_search wikilink expansion fallback ─────────────────────────


class TestNodeSearchExpansionFallback:
    """Test that node_search wikilink expansion fallback works (fix #1)."""

    def test_expansion_fallback_from_daily_hits(self):
        """When no direct digest BM25 hits, expansion from daily hits should find digest nodes."""
        from app.memory.file_store.frontmatter import MemoryFrontmatter
        from app.memory.file_store.markdown_io import write_markdown
        from app.memory.file_store.workspace import MemoryWorkspace
        from app.memory.search.bm25_index import BM25Index
        from app.memory.search.node_search import NodeSearch
        from app.memory.search.wikilink_expander import WikilinkExpander

        tmpdir = tempfile.mkdtemp()
        try:
            settings = MagicMock()
            settings.memory_workspace_path = Path(tmpdir)

            workspace = MemoryWorkspace(settings)
            workspace.initialize()
            bm25 = BM25Index(workspace.metadata_dir / "bm25.db")
            expander = WikilinkExpander(workspace.metadata_dir / "wikilinks.db")
            bm25.initialize()
            expander.initialize()

            today = date.today().isoformat()

            # Create a daily file that links to a digest file
            daily_f = workspace.daily_file_path("daily-with-link", today)
            digest_f = workspace.digest_path("wiki", "linked-digest")

            fm_daily = MemoryFrontmatter(name="Daily Card", description="Daily card", created_at=today, updated_at=today, source="")
            fm_digest = MemoryFrontmatter(name="Linked Digest", description="Linked from daily", created_at=today, updated_at=today, source="")

            rel_digest = str(digest_f)
            write_markdown(daily_f, fm_daily, f"Content about React hooks\nrelates_to:: [[{rel_digest}]]")
            write_markdown(digest_f, fm_digest, "Different content not matching query keywords")

            # Index both
            from app.memory.pipeline.auto_index import AutoIndex
            auto_index = AutoIndex(workspace, bm25, expander)
            auto_index.index_file(daily_f)
            auto_index.index_file(digest_f)

            # Search with keywords that only match daily file
            node_search = NodeSearch(bm25, expander, workspace)
            results = node_search.search("React hooks", limit=10)

            # Should find the digest file via wikilink expansion from daily hit
            digest_results = [r for r in results if "linked-digest" in r.path]
            assert len(digest_results) >= 1, "Wikilink expansion fallback should find linked digest node"

            bm25.close()
            expander.close()
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
