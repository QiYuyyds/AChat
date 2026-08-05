"""Tests for enhance-memory-pipeline-quality change.

Covers:
  1. Personal bucket CREATE path (digest/personal/ directory)
  2. Personal bucket integrate uses correct system prompt
  3. Two-round search deduplication (same path in both rounds → one result, higher score)
  4. dream_extract skips .yaml files
  5. source_paths validation (hallucinated path filtered + empty fallback)
  6. File rename retarget (old wikilink updated to new path)
  7. Topic title normalization dedup ("Memory Pipeline!" vs "memory pipeline")
  8. Timestamp alias normalization (timestamp → created_at + existing preserved)
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ─── Helper ───────────────────────────────────────────────────────────────


def _setup_workspace():
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


def _teardown(tmpdir, *components):
    for c in components:
        if hasattr(c, "close"):
            c.close()
    import shutil
    shutil.rmtree(tmpdir, ignore_errors=True)


# ─── 8.1 Personal bucket CREATE path ─────────────────────────────────────


class TestPersonalBucketCreate:
    """Test that personal bucket files are written to digest/personal/."""

    def test_personal_bucket_create_writes_to_personal_dir(self):
        from app.memory.pipeline.auto_dream import AutoDream

        workspace, bm25, expander, catalog, node_search, search, tmpdir = _setup_workspace()
        try:
            dream = AutoDream(workspace, search, node_search, catalog)

            llm_response = json.dumps({
                "action": "CREATE",
                "content": "## Rule / fact\nUser prefers small PRs\n\n## Why\nEasier to review\n\n## How to apply\nKeep PRs under 300 lines\nderived_from:: [[daily/2026-08-04/sess.md]]",
                "reason": "No existing match",
                "wikilinks": ["derived_from:: [[daily/2026-08-04/sess.md]]"],
            })
            dream.set_generate_fn(lambda sys, user: llm_response)

            unit = {
                "name": "Small PR Preference",
                "bucket": "personal",
                "summary": "User prefers small PRs",
                "content": "User prefers small PRs for easier review",
                "tags": ["preferences", "pr"],
                "importance": 0.8,
                "source_paths": ["daily/2026-08-04/sess.md"],
            }

            loop = asyncio.new_event_loop()
            action = loop.run_until_complete(dream._dream_integrate(unit))
            loop.close()

            assert action == "created"

            personal_files = list((workspace.digest_dir / "personal").glob("*.md"))
            assert len(personal_files) == 1

            from app.memory.file_store.markdown_io import read_markdown
            mem = read_markdown(personal_files[0])
            assert mem is not None
            assert mem.frontmatter.bucket == "personal"
            assert mem.frontmatter.status == "active"
        finally:
            _teardown(tmpdir, bm25, expander, catalog)

    def test_personal_bucket_digest_path(self):
        """workspace.digest_path should route personal bucket to digest/personal/."""
        from app.memory.file_store.workspace import MemoryWorkspace

        with tempfile.TemporaryDirectory() as tmpdir:
            settings = MagicMock()
            settings.memory_workspace_path = Path(tmpdir)
            workspace = MemoryWorkspace(settings)
            workspace.initialize()

            path = workspace.digest_path("personal", "test-pref")
            assert "personal" in str(path)
            assert path.parent == workspace.digest_dir / "personal"


# ─── 8.2 Personal bucket integrate uses correct system prompt ────────────


class TestPersonalBucketPrompt:
    """Test that personal bucket uses _INTEGRATE_SYSTEM_PROMPT_PERSONAL."""

    def test_personal_bucket_uses_personal_prompt(self):
        from app.memory.pipeline.auto_dream import (
            _INTEGRATE_SYSTEM_PROMPT_PERSONAL,
            AutoDream,
        )

        workspace, bm25, expander, catalog, node_search, search, tmpdir = _setup_workspace()
        try:
            dream = AutoDream(workspace, search, node_search, catalog)

            captured_prompt = []
            def mock_generate(sys_prompt, user_prompt):
                captured_prompt.append(sys_prompt)
                return json.dumps({
                    "action": "CREATE",
                    "content": "## Rule / fact\nTest rule\nderived_from:: [[daily/2026-08-04/sess.md]]",
                    "reason": "new",
                    "wikilinks": [],
                })

            dream.set_generate_fn(mock_generate)

            unit = {
                "name": "Test Preference",
                "bucket": "personal",
                "summary": "Test summary",
                "content": "Test content",
                "tags": ["test"],
                "importance": 0.5,
                "source_paths": ["daily/2026-08-04/sess.md"],
            }

            loop = asyncio.new_event_loop()
            loop.run_until_complete(dream._dream_integrate(unit))
            loop.close()

            assert len(captured_prompt) == 1
            assert captured_prompt[0] == _INTEGRATE_SYSTEM_PROMPT_PERSONAL
        finally:
            _teardown(tmpdir, bm25, expander, catalog)

    def test_unknown_bucket_falls_back_to_wiki(self):
        """Unknown bucket value should fall back to wiki prompt."""
        from app.memory.pipeline.auto_dream import (
            _INTEGRATE_SYSTEM_PROMPT_WIKI,
            AutoDream,
        )

        workspace, bm25, expander, catalog, node_search, search, tmpdir = _setup_workspace()
        try:
            dream = AutoDream(workspace, search, node_search, catalog)

            captured_prompt = []
            def mock_generate(sys_prompt, user_prompt):
                captured_prompt.append(sys_prompt)
                return json.dumps({
                    "action": "CREATE",
                    "content": "Test content\nderived_from:: [[daily/2026-08-04/sess.md]]",
                    "reason": "new",
                    "wikilinks": [],
                })

            dream.set_generate_fn(mock_generate)

            unit = {
                "name": "Test Unknown",
                "bucket": "unknown_value",
                "summary": "Test",
                "content": "Test",
                "tags": [],
                "importance": 0.5,
                "source_paths": [],
            }

            loop = asyncio.new_event_loop()
            loop.run_until_complete(dream._dream_integrate(unit))
            loop.close()

            assert captured_prompt[0] == _INTEGRATE_SYSTEM_PROMPT_WIKI
        finally:
            _teardown(tmpdir, bm25, expander, catalog)


# ─── 8.3 Two-round search deduplication ──────────────────────────────────


class TestTwoRoundSearchDedup:
    """Test that _dedupe_by_path merges hits from two rounds correctly."""

    def test_dedupe_by_path_keeps_higher_score(self):
        from app.memory.pipeline.auto_dream import _dedupe_by_path

        hit_a = MagicMock(path="digest/wiki/test.md", score=0.5)
        hit_b = MagicMock(path="digest/wiki/test.md", score=0.8)
        hit_c = MagicMock(path="digest/wiki/other.md", score=0.3)

        result = _dedupe_by_path([hit_a, hit_c], [hit_b])

        paths = [getattr(r, 'path', '') for r in result]
        assert len(result) == 2
        assert "digest/wiki/test.md" in paths
        assert "digest/wiki/other.md" in paths

        test_hit = next(r for r in result if getattr(r, 'path', '') == "digest/wiki/test.md")
        assert getattr(test_hit, 'score', 0) == 0.8

    def test_dedupe_by_path_returns_top_5(self):
        from app.memory.pipeline.auto_dream import _dedupe_by_path

        hits_1 = [MagicMock(path=f"path_{i}.md", score=float(i)) for i in range(8)]
        hits_2 = [MagicMock(path=f"path_{i}.md", score=float(i + 0.1)) for i in range(8)]

        result = _dedupe_by_path(hits_1, hits_2)
        assert len(result) == 5

        scores = [getattr(r, 'score', 0) for r in result]
        assert scores == sorted(scores, reverse=True)


# ─── 8.4 dream_extract skips .yaml files ─────────────────────────────────


class TestExtractSkipsYaml:
    """Test that dream_extract skips .yaml files."""

    def test_yaml_files_excluded_from_extract(self):
        from app.memory.file_store.frontmatter import MemoryFrontmatter
        from app.memory.file_store.markdown_io import write_markdown
        from app.memory.pipeline.auto_dream import AutoDream

        workspace, bm25, expander, catalog, node_search, search, tmpdir = _setup_workspace()
        try:
            today = date.today().isoformat()

            md_file = workspace.daily_file_path("test-card", today)
            yaml_file = workspace.daily_dir / today / "interests.yaml"
            yaml_file.parent.mkdir(parents=True, exist_ok=True)

            fm = MemoryFrontmatter(name="test-card", created_at=today, updated_at=today, source="")
            write_markdown(md_file, fm, "Important memory content about React")
            yaml_file.write_text("- title: Some Interest\n", encoding="utf-8")

            catalog.reconcile(workspace.daily_dir, workspace.digest_dir)

            os.utime(md_file, (md_file.stat().st_atime, md_file.stat().st_mtime + 10))
            os.utime(yaml_file, (yaml_file.stat().st_atime, yaml_file.stat().st_mtime + 10))

            dream = AutoDream(workspace, search, file_catalog=catalog)

            captured_prompt = []
            def mock_generate(sys_prompt, user_prompt):
                captured_prompt.append(user_prompt)
                return json.dumps({"units": [], "topic_candidates": []})

            dream.set_generate_fn(mock_generate)

            loop = asyncio.new_event_loop()
            loop.run_until_complete(dream._dream_extract(5))
            loop.close()

            assert len(captured_prompt) == 1
            assert "Important memory content" in captured_prompt[0]
            assert "Some Interest" not in captured_prompt[0]
        finally:
            _teardown(tmpdir, bm25, expander, catalog)


# ─── 8.5 source_paths validation ─────────────────────────────────────────


class TestSourcePathsValidation:
    """Test that hallucinated source_paths are filtered and empty fallback works."""

    def test_hallucinated_path_filtered(self):
        from app.memory.file_store.frontmatter import MemoryFrontmatter
        from app.memory.file_store.markdown_io import write_markdown
        from app.memory.pipeline.auto_dream import AutoDream

        workspace, bm25, expander, catalog, node_search, search, tmpdir = _setup_workspace()
        try:
            today = date.today().isoformat()
            real_file = workspace.daily_file_path("real-card", today)
            fm = MemoryFrontmatter(name="real-card", created_at=today, updated_at=today, source="")
            write_markdown(real_file, fm, "Real content about deployments")

            catalog.reconcile(workspace.daily_dir, workspace.digest_dir)
            os.utime(real_file, (real_file.stat().st_atime, real_file.stat().st_mtime + 10))

            dream = AutoDream(workspace, search, file_catalog=catalog)

            real_path_str = str(real_file)
            fake_path_str = str(workspace.daily_dir / "2026-01-01" / "fake.md")
            llm_response = json.dumps({
                "units": [{
                    "name": "Deploy Guide",
                    "bucket": "procedure",
                    "summary": "How to deploy",
                    "content": "Deploy steps",
                    "tags": ["deploy"],
                    "importance": 0.7,
                    "source_paths": [real_path_str, fake_path_str],
                }],
                "topic_candidates": [],
            })
            dream.set_generate_fn(lambda sys, user: llm_response)

            loop = asyncio.new_event_loop()
            units, _ = loop.run_until_complete(dream._dream_extract(5))
            loop.close()

            assert len(units) == 1
            assert fake_path_str not in units[0]["source_paths"]
            assert real_path_str in units[0]["source_paths"]
        finally:
            _teardown(tmpdir, bm25, expander, catalog)

    def test_empty_source_paths_fallback_to_changed_paths(self):
        from app.memory.file_store.frontmatter import MemoryFrontmatter
        from app.memory.file_store.markdown_io import write_markdown
        from app.memory.pipeline.auto_dream import AutoDream

        workspace, bm25, expander, catalog, node_search, search, tmpdir = _setup_workspace()
        try:
            today = date.today().isoformat()
            real_file = workspace.daily_file_path("real-card", today)
            fm = MemoryFrontmatter(name="real-card", created_at=today, updated_at=today, source="")
            write_markdown(real_file, fm, "Real content")

            catalog.reconcile(workspace.daily_dir, workspace.digest_dir)
            os.utime(real_file, (real_file.stat().st_atime, real_file.stat().st_mtime + 10))

            dream = AutoDream(workspace, search, file_catalog=catalog)

            fake_path_str = str(workspace.daily_dir / "2026-01-01" / "nonexistent.md")
            llm_response = json.dumps({
                "units": [{
                    "name": "Test",
                    "bucket": "wiki",
                    "summary": "Test summary",
                    "content": "Test content",
                    "tags": [],
                    "importance": 0.5,
                    "source_paths": [fake_path_str],
                }],
                "topic_candidates": [],
            })
            dream.set_generate_fn(lambda sys, user: llm_response)

            loop = asyncio.new_event_loop()
            units, _ = loop.run_until_complete(dream._dream_extract(5))
            loop.close()

            assert len(units) == 1
            assert len(units[0]["source_paths"]) > 0
            assert str(real_file) in units[0]["source_paths"]
        finally:
            _teardown(tmpdir, bm25, expander, catalog)


# ─── 8.6 File rename retarget ────────────────────────────────────────────


class TestFileRenameRetarget:
    """Test that renaming a daily card retargets wikilinks in other files."""

    def test_rename_retargets_wikilinks(self):
        from app.memory.file_store.frontmatter import MemoryFrontmatter
        from app.memory.file_store.markdown_io import write_markdown
        from app.memory.file_store.workspace import MemoryWorkspace
        from app.memory.pipeline.auto_memory import AutoMemory
        from app.memory.search.wikilink_expander import WikilinkExpander

        tmpdir = tempfile.mkdtemp()
        try:
            settings = MagicMock()
            settings.memory_workspace_path = Path(tmpdir)
            workspace = MemoryWorkspace(settings)
            workspace.initialize()

            expander = WikilinkExpander(workspace.metadata_dir / "wikilinks.db")
            expander.initialize()

            auto_memory = AutoMemory(workspace, wikilink_expander=expander)

            today = date.today().isoformat()
            existing_name = "old-card-abc123"
            filepath = workspace.daily_file_path(existing_name, today)
            fm = MemoryFrontmatter(
                name="Old Card",
                description="Old description",
                tags=["old-tag"],
                importance=0.3,
                bucket="procedure",
                created_at=today,
                updated_at=today,
                source="session/conv_test_rename.jsonl",
            )
            write_markdown(filepath, fm, "- Old fact")

            linker_path = workspace.daily_file_path("linker-card", today)
            linker_fm = MemoryFrontmatter(
                name="Linker",
                created_at=today,
                updated_at=today,
                source="",
            )
            old_rel = str(filepath.relative_to(workspace.root))
            write_markdown(linker_path, linker_fm, f"See [[{old_rel}]] for details")

            linker_rel = str(linker_path.relative_to(workspace.root))
            expander.add_edges(linker_rel, [old_rel])

            llm_response = json.dumps({
                "action": "update",
                "name": "New Card Name",
                "body": "- Updated fact\nderived_from:: [[session/conv_test_rename.jsonl]]",
                "tags": ["new-tag"],
                "importance": 0.8,
            })
            auto_memory.set_generate_fn(lambda sys, user: llm_response)

            loop = asyncio.new_event_loop()
            result = loop.run_until_complete(auto_memory.run(
                "More info about the project",
                "Here's a new fact about the system",
                conversation_id="conv_test_rename",
                agent_id="ag_test",
            ))
            loop.close()

            assert result == 1

            new_filepath = workspace.daily_file_path("new-card-name", today)
            new_rel = str(new_filepath.relative_to(workspace.root))

            linker_text = linker_path.read_text(encoding="utf-8")
            assert new_rel in linker_text
            assert old_rel not in linker_text

            assert not filepath.exists()
            assert new_filepath.exists()

            old_edges = expander.get_outlinks(linker_rel)
            old_targets = [e["target"] for e in old_edges]
            assert old_rel not in old_targets
        finally:
            expander.close()
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_rename_noop_when_name_unchanged(self):
        from app.memory.file_store.frontmatter import MemoryFrontmatter
        from app.memory.file_store.markdown_io import write_markdown
        from app.memory.file_store.workspace import MemoryWorkspace
        from app.memory.pipeline.auto_memory import AutoMemory

        tmpdir = tempfile.mkdtemp()
        try:
            settings = MagicMock()
            settings.memory_workspace_path = Path(tmpdir)
            workspace = MemoryWorkspace(settings)
            workspace.initialize()
            auto_memory = AutoMemory(workspace)

            today = date.today().isoformat()
            existing_name = "same-name"
            filepath = workspace.daily_file_path(existing_name, today)
            fm = MemoryFrontmatter(
                name="Same Name",
                description="Description",
                tags=["tag"],
                importance=0.5,
                bucket="procedure",
                created_at=today,
                updated_at=today,
                source="session/conv_same.jsonl",
            )
            write_markdown(filepath, fm, "- Old fact")

            llm_response = json.dumps({
                "action": "update",
                "name": "Same Name",
                "body": "- Updated fact with more details about the project",
                "tags": ["tag"],
                "importance": 0.6,
            })
            auto_memory.set_generate_fn(lambda sys, user: llm_response)

            loop = asyncio.new_event_loop()
            result = loop.run_until_complete(auto_memory.run(
                "More information about the project setup and configuration details",
                "Here's a new fact about how the system handles updates and merges",
                conversation_id="conv_same",
                agent_id="ag_test",
            ))
            loop.close()

            assert result == 1
            assert filepath.exists()
        finally:
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


# ─── 8.7 Topic title normalization dedup ─────────────────────────────────


class TestTopicNormalization:
    """Test that topic titles are normalized for deduplication."""

    def test_normalize_topic_title(self):
        from app.memory.pipeline.auto_dream import _normalize_topic_title

        assert _normalize_topic_title("Memory Pipeline!") == "memory pipeline"
        assert _normalize_topic_title("Memory Pipeline") == "memory pipeline"
        assert _normalize_topic_title("memory pipeline") == "memory pipeline"
        assert _normalize_topic_title("  Memory   Pipeline  ") == "memory pipeline"
        assert _normalize_topic_title("C++ vs C") == "c vs c"

    def test_near_duplicate_topics_deduplicated(self):
        import yaml

        from app.memory.file_store.workspace import MemoryWorkspace
        from app.memory.pipeline.auto_dream import AutoDream
        from app.memory.search.bm25_index import BM25Index
        from app.memory.search.hybrid_search import HybridSearch
        from app.memory.search.wikilink_expander import WikilinkExpander

        tmpdir = tempfile.mkdtemp()
        bm25 = None
        expander = None
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
            bm25.initialize()
            expander.initialize()
            search = HybridSearch(settings, bm25, expander)

            yesterday_ordinal = date.today().toordinal() - 1
            yesterday_date = date.fromordinal(yesterday_ordinal).isoformat()
            ipath = workspace.interests_path(yesterday_date)
            ipath.parent.mkdir(parents=True, exist_ok=True)
            with open(ipath, "w", encoding="utf-8") as f:
                yaml.dump(
                    [{"title": "Memory Pipeline", "reason": "x", "keywords": [], "evidence": ""}],
                    f,
                    allow_unicode=True,
                )

            dream = AutoDream(workspace, search)

            dream.set_generate_fn(lambda sys, user: (_ for _ in ()).throw(RuntimeError("LLM unavailable")))

            candidates = [{"title": "Memory Pipeline!", "reason": "x", "keywords": [], "evidence": "u1"}]

            loop = asyncio.new_event_loop()
            count = loop.run_until_complete(dream._dream_topics([], candidates))
            loop.close()

            assert count == 0
        finally:
            if bm25:
                bm25.close()
            if expander:
                expander.close()
            import shutil
            shutil.rmtree(tmpdir, ignore_errors=True)


# ─── 8.8 Timestamp alias normalization ───────────────────────────────────


class TestTimestampNormalization:
    """Test that timestamp aliases are normalized to created_at."""

    def test_alias_timestamp_normalized(self):
        from app.memory.pipeline.auto_memory import _normalize_timestamp

        msg = {"role": "user", "content": "hello", "timestamp": "2026-08-05T10:00:00Z"}
        result = _normalize_timestamp(msg)
        assert result["created_at"] == "2026-08-05T10:00:00Z"

    def test_time_created_alias_normalized(self):
        from app.memory.pipeline.auto_memory import _normalize_timestamp

        msg = {"role": "user", "content": "hello", "time_created": "2026-08-05T10:00:00Z"}
        result = _normalize_timestamp(msg)
        assert result["created_at"] == "2026-08-05T10:00:00Z"

    def test_created_at_alias_normalized(self):
        from app.memory.pipeline.auto_memory import _normalize_timestamp

        msg = {"role": "user", "content": "hello", "createdAt": "2026-08-05T10:00:00Z"}
        result = _normalize_timestamp(msg)
        assert result["created_at"] == "2026-08-05T10:00:00Z"

    def test_existing_created_at_preserved(self):
        from app.memory.pipeline.auto_memory import _normalize_timestamp

        msg = {
            "role": "user",
            "content": "hello",
            "created_at": "2026-08-05T10:00:00Z",
            "timestamp": "2026-01-01T00:00:00Z",
        }
        result = _normalize_timestamp(msg)
        assert result["created_at"] == "2026-08-05T10:00:00Z"

    def test_no_timestamp_keeps_unchanged(self):
        from app.memory.pipeline.auto_memory import _normalize_timestamp

        msg = {"role": "user", "content": "hello"}
        result = _normalize_timestamp(msg)
        assert "created_at" not in result

    def test_write_session_jsonl_normalizes_timestamp(self):
        from app.memory.file_store.workspace import MemoryWorkspace
        from app.memory.pipeline.auto_memory import AutoMemory

        with tempfile.TemporaryDirectory() as tmpdir:
            settings = MagicMock()
            settings.memory_workspace_path = Path(tmpdir)
            workspace = MemoryWorkspace(settings)
            workspace.initialize()
            auto_memory = AutoMemory(workspace)

            conv_id = "test_conv_ts"
            messages = [
                {"id": "msg1", "role": "user", "content": "hello", "timestamp": "2026-08-05T10:00:00Z"},
            ]

            loop = asyncio.new_event_loop()
            loop.run_until_complete(auto_memory.write_session_jsonl(conv_id, messages))
            loop.close()

            filepath = workspace.session_path(conv_id)
            line = filepath.read_text(encoding="utf-8").strip()
            msg = json.loads(line)
            assert msg["created_at"] == "2026-08-05T10:00:00Z"


# ─── Prompt content verification ─────────────────────────────────────────


class TestPromptContent:
    """Verify that prompts contain the required quality gate language."""

    def test_extract_prompt_contains_quality_gate(self):
        from app.memory.pipeline.auto_dream import _EXTRACT_SYSTEM_PROMPT

        assert "gate for" in _EXTRACT_SYSTEM_PROMPT
        assert "fewer, richer units" in _EXTRACT_SYSTEM_PROMPT
        assert "passing mentions" in _EXTRACT_SYSTEM_PROMPT
        assert "merge evidence from multiple files" in _EXTRACT_SYSTEM_PROMPT.lower()

    def test_extract_prompt_contains_personal_bucket(self):
        from app.memory.pipeline.auto_dream import _EXTRACT_SYSTEM_PROMPT

        assert "personal" in _EXTRACT_SYSTEM_PROMPT.lower()

    def test_integrate_prompts_contain_only_add_principle(self):
        from app.memory.pipeline.auto_dream import (
            _INTEGRATE_SYSTEM_PROMPT_PERSONAL,
            _INTEGRATE_SYSTEM_PROMPT_PROCEDURE,
            _INTEGRATE_SYSTEM_PROMPT_WIKI,
        )

        for prompt in (_INTEGRATE_SYSTEM_PROMPT_PROCEDURE, _INTEGRATE_SYSTEM_PROMPT_WIKI, _INTEGRATE_SYSTEM_PROMPT_PERSONAL):
            assert "additive" in prompt
            assert "weaving more" in prompt

    def test_personal_prompt_has_rule_of_engagement(self):
        from app.memory.pipeline.auto_dream import _INTEGRATE_SYSTEM_PROMPT_PERSONAL

        assert "Rule / fact" in _INTEGRATE_SYSTEM_PROMPT_PERSONAL
        assert "Why" in _INTEGRATE_SYSTEM_PROMPT_PERSONAL
        assert "How to apply" in _INTEGRATE_SYSTEM_PROMPT_PERSONAL


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
