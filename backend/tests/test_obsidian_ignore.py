"""Unit tests for .obsidianignore exclusion rules."""

import tempfile
from pathlib import Path

from app.services.obsidian_sync_service import is_ignored, load_ignore_spec


class TestObsidianignore:
    def test_default_excludes_obsidian_dir(self) -> None:
        with tempfile.TemporaryDirectory() as vault:
            spec = load_ignore_spec(vault)
            assert is_ignored(".obsidian/config", spec)
            assert is_ignored(".obsidian/themes/theme.css", spec)

    def test_default_excludes_templates_dir(self) -> None:
        with tempfile.TemporaryDirectory() as vault:
            spec = load_ignore_spec(vault)
            assert is_ignored("Templates/daily.md", spec)

    def test_default_excludes_obsidianignore_itself(self) -> None:
        with tempfile.TemporaryDirectory() as vault:
            spec = load_ignore_spec(vault)
            assert is_ignored(".obsidianignore", spec)

    def test_custom_ignore_rules(self) -> None:
        with tempfile.TemporaryDirectory() as vault:
            ignore_file = Path(vault) / ".obsidianignore"
            ignore_file.write_text("*.draft.md\nArchive/\n", encoding="utf-8")
            spec = load_ignore_spec(vault)
            assert is_ignored("notes.draft.md", spec)
            assert is_ignored("Archive/old.md", spec)
            assert is_ignored(".obsidian/config", spec)
            assert is_ignored("Templates/template.md", spec)

    def test_non_ignored_files_pass(self) -> None:
        with tempfile.TemporaryDirectory() as vault:
            spec = load_ignore_spec(vault)
            assert not is_ignored("Projects/design.md", spec)
            assert not is_ignored("Daily Notes/2024-01-01.md", spec)
            assert not is_ignored("README.md", spec)

    def test_comment_lines_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as vault:
            ignore_file = Path(vault) / ".obsidianignore"
            ignore_file.write_text("# This is a comment\n*.tmp\n", encoding="utf-8")
            spec = load_ignore_spec(vault)
            assert is_ignored("file.tmp", spec)
            assert not is_ignored("# This is a comment", spec)

    def test_empty_lines_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as vault:
            ignore_file = Path(vault) / ".obsidianignore"
            ignore_file.write_text("\n\n*.log\n\n", encoding="utf-8")
            spec = load_ignore_spec(vault)
            assert is_ignored("debug.log", spec)

    def test_no_obsidianignore_file(self) -> None:
        with tempfile.TemporaryDirectory() as vault:
            spec = load_ignore_spec(vault)
            assert is_ignored(".obsidian/config", spec)
            assert not is_ignored("normal.md", spec)
