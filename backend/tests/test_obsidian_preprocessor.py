"""Unit tests for ObsidianPreprocessor — 8-step pipeline + edge cases."""

import tempfile
from pathlib import Path

import pytest

from app.rag.obsidian_preprocessor import ObsidianPreprocessor


@pytest.fixture
def proc() -> ObsidianPreprocessor:
    return ObsidianPreprocessor(max_embed_depth=2)


# ─── Step 1: Frontmatter extraction ─────────────────────────────────────────


class TestFrontmatter:
    def test_extracts_title_and_tags(self, proc: ObsidianPreprocessor) -> None:
        content = "---\ntitle: 设计\ntags: [项目, 架构]\n---\n# 正文"
        result, meta = proc.process(content)
        assert result.strip() == "# 正文"
        assert meta["title"] == "设计"
        assert "项目" in meta["tags"]
        assert "架构" in meta["tags"]

    def test_extracts_aliases(self, proc: ObsidianPreprocessor) -> None:
        content = "---\ntitle: Note\naliases: [N1, N2]\n---\nBody"
        _, meta = proc.process(content)
        assert "N1" in meta["aliases"]
        assert "N2" in meta["aliases"]

    def test_empty_frontmatter(self, proc: ObsidianPreprocessor) -> None:
        content = "---\n---\nBody"
        result, meta = proc.process(content)
        assert result.strip() == "Body"
        assert meta["title"] is None

    def test_no_frontmatter(self, proc: ObsidianPreprocessor) -> None:
        content = "Just plain text"
        result, meta = proc.process(content)
        assert result == "Just plain text"
        assert meta["title"] is None

    def test_tags_as_comma_string(self, proc: ObsidianPreprocessor) -> None:
        content = "---\ntags: 项目, 架构\n---\nBody"
        _, meta = proc.process(content)
        assert "项目" in meta["tags"]


# ─── Step 2: Transclusion expansion ──────────────────────────────────────────


class TestTransclusion:
    def test_expands_md_note(self, proc: ObsidianPreprocessor) -> None:
        with tempfile.TemporaryDirectory() as vault:
            (Path(vault) / "B.md").write_text("Content of B", encoding="utf-8")
            content = "Before\n![[B]]\nAfter"
            result, _ = proc.process(content, vault)
            assert "Content of B" in result

    def test_image_placeholder(self, proc: ObsidianPreprocessor) -> None:
        content = "See ![[screenshot.png]] here"
        result, _ = proc.process(content, "/nonexistent")
        assert "[image: screenshot.png]" in result

    def test_recursive_expansion_depth_2(self, proc: ObsidianPreprocessor) -> None:
        with tempfile.TemporaryDirectory() as vault:
            (Path(vault) / "B.md").write_text("![[C]]", encoding="utf-8")
            (Path(vault) / "C.md").write_text("Deep content", encoding="utf-8")
            content = "![[B]]"
            result, _ = proc.process(content, vault)
            assert "Deep content" in result

    def test_cycle_detection(self, proc: ObsidianPreprocessor) -> None:
        with tempfile.TemporaryDirectory() as vault:
            (Path(vault) / "A.md").write_text("![[B]]", encoding="utf-8")
            (Path(vault) / "B.md").write_text("![[A]]", encoding="utf-8")
            # Processing A's content will detect the cycle when trying to embed A again
            content = "![[A]]"
            result, _ = proc.process(content, vault)
            # B expands, then A is found in visited → circular marker
            assert "[circular: A]" in result

    def test_depth_limit(self, proc: ObsidianPreprocessor) -> None:
        with tempfile.TemporaryDirectory() as vault:
            (Path(vault) / "A.md").write_text("![[B]]", encoding="utf-8")
            (Path(vault) / "B.md").write_text("![[C]]", encoding="utf-8")
            (Path(vault) / "C.md").write_text("![[D]]", encoding="utf-8")
            (Path(vault) / "D.md").write_text("Should not reach", encoding="utf-8")
            content = "![[A]]"
            result, _ = proc.process(content, vault)
            # D should not be expanded because max depth is 2
            assert "Should not reach" not in result

    def test_non_md_non_image_embed_placeholder(self, proc: ObsidianPreprocessor) -> None:
        content = "![[data.csv]]"
        result, _ = proc.process(content, "/nonexistent")
        # Non-.md non-image embeds → neutral placeholder (avoids wikilink re-match)
        assert "[embed: data.csv]" in result


# ─── Step 3: Wikilink normalization ──────────────────────────────────────────


class TestWikilink:
    def test_simple_wikilink(self, proc: ObsidianPreprocessor) -> None:
        content = "参考 [[设计文档|设计]] 和 [[API规范]]"
        result, meta = proc.process(content)
        assert "设计" in result
        assert "API规范" in result
        assert "设计文档" in meta["linked_entities"]
        assert "API规范" in meta["linked_entities"]

    def test_wikilink_no_alias(self, proc: ObsidianPreprocessor) -> None:
        content = "See [[Target]]"
        result, meta = proc.process(content)
        assert "Target" in result
        assert "Target" in meta["linked_entities"]


# ─── Step 4: Comment stripping ───────────────────────────────────────────────


class TestComments:
    def test_strips_percent_comments(self, proc: ObsidianPreprocessor) -> None:
        content = "Before%%这是注释%%After"
        result, _ = proc.process(content)
        assert result == "BeforeAfter"

    def test_multiline_comment(self, proc: ObsidianPreprocessor) -> None:
        content = "Before%%line1\nline2%%After"
        result, _ = proc.process(content)
        assert result == "BeforeAfter"


# ─── Step 5: Dataview stripping ──────────────────────────────────────────────


class TestDataview:
    def test_removes_dataview_block(self, proc: ObsidianPreprocessor) -> None:
        content = "Before\n```dataview\nLIST\n```\nAfter"
        result, _ = proc.process(content)
        assert "dataview" not in result
        assert "Before" in result
        assert "After" in result


# ─── Step 6: Callout simplification ─────────────────────────────────────────


class TestCallout:
    def test_simplifies_callout(self, proc: ObsidianPreprocessor) -> None:
        content = "> [!warning] 注意\n> 有 breaking change"
        result, _ = proc.process(content)
        assert "**注意**" in result
        assert "有 breaking change" in result

    def test_callout_no_title(self, proc: ObsidianPreprocessor) -> None:
        content = "> [!note]\n> Just a note"
        result, _ = proc.process(content)
        assert "Just a note" in result


# ─── Step 7: Inline tag extraction ──────────────────────────────────────────


class TestInlineTags:
    def test_extracts_inline_tags(self, proc: ObsidianPreprocessor) -> None:
        content = "This has #project and #arch tags"
        _, meta = proc.process(content)
        assert "project" in meta["tags"]
        assert "arch" in meta["tags"]

    def test_tags_preserved_inline(self, proc: ObsidianPreprocessor) -> None:
        content = "This has #project tag"
        result, _ = proc.process(content)
        assert "#project" in result

    def test_no_heading_confusion(self, proc: ObsidianPreprocessor) -> None:
        content = "# Heading\n## Sub heading"
        _, meta = proc.process(content)
        # Markdown headings should NOT be parsed as tags
        assert "Heading" not in meta["tags"]


# ─── Step 8: Highlight simplification ───────────────────────────────────────


class TestHighlight:
    def test_converts_highlight(self, proc: ObsidianPreprocessor) -> None:
        content = "This is ==important== text"
        result, _ = proc.process(content)
        assert result == "This is **important** text"

    def test_multiple_highlights(self, proc: ObsidianPreprocessor) -> None:
        content = "==a== and ==b=="
        result, _ = proc.process(content)
        assert result == "**a** and **b**"


# ─── Integration: full pipeline ──────────────────────────────────────────────


class TestFullPipeline:
    def test_combined_obsidian_syntax(self, proc: ObsidianPreprocessor) -> None:
        content = (
            "---\ntitle: 设计\ntags: [项目]\n---\n"
            "# 设计文档\n\n"
            "参考 [[API规范]] 和 [[设计文档|设计]]\n\n"
            "%%这是注释%%\n\n"
            "重要：==这个==\n\n"
            "> [!warning] 注意\n> 有 breaking change\n"
        )
        result, meta = proc.process(content)
        assert meta["title"] == "设计"
        assert "项目" in meta["tags"]
        assert "API规范" in meta["linked_entities"]
        assert "设计文档" in meta["linked_entities"]
        assert "这是注释" not in result
        assert "**这个**" in result
        assert "**注意**" in result
