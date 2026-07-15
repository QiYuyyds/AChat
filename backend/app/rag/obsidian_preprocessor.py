"""ObsidianPreprocessor — Obsidian-flavored Markdown preprocessing pipeline.

8-step pipeline executed in order:
1. Frontmatter extraction — parse YAML --- block, extract title/tags/aliases
2. Transclusion expansion — ![[note]] recursive embed (max depth 2, cycle detection)
3. Wikilink normalization — [[Target]] → "Target", [[Target|Alias]] → "Alias"
4. Comment stripping — %%comment%% removal
5. Dataview stripping — ```dataview code block removal
6. Callout simplification — > [!type] Title → **Title**
7. Inline tag extraction — #tag preserved inline, also collected to metadata
8. Highlight simplification — ==text== → **text**
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# Maximum recursive transclusion depth
_DEFAULT_MAX_DEPTH = 2

# Image file extensions recognized in transclusion
_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp"})

# Regex patterns
_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?\n)?---\s*\n?", re.DOTALL)
_WIKILINK_RE = re.compile(r"\[\[([^\]]+?)\]\]")
_TRANSCLUSION_RE = re.compile(r"!\[\[([^\]]+?)\]\]")
_COMMENT_RE = re.compile(r"%%.*?%%", re.DOTALL)
_DATAVIEW_RE = re.compile(r"```dataview\s*\n.*?```\n?", re.DOTALL)
_CALLOUT_RE = re.compile(r"^>\s*\[!(\w+)\]\s*(.*?)\s*\n((?:>\s*.*\n?)*)", re.MULTILINE)
_INLINE_TAG_RE = re.compile(r"(?:^|\s)#([a-zA-Z][\w/-]*)")
_HIGHLIGHT_RE = re.compile(r"==(.+?)==")


class ObsidianPreprocessor:
    """Obsidian-flavored Markdown preprocessor.

    Usage::

        proc = ObsidianPreprocessor(max_embed_depth=2)
        content, metadata = proc.process(raw_md, vault_path)
    """

    def __init__(self, max_embed_depth: int = _DEFAULT_MAX_DEPTH) -> None:
        self.max_embed_depth = max_embed_depth

    def process(self, content: str, vault_path: str | Path | None = None) -> tuple[str, dict[str, Any]]:
        """Run the full 8-step pipeline. Returns (processed_content, metadata)."""
        metadata: dict[str, Any] = {
            "title": None,
            "tags": [],
            "aliases": [],
            "linked_entities": [],
        }

        # Step 1: Frontmatter extraction
        content = self._extract_frontmatter(content, metadata)

        # Step 2: Transclusion expansion
        content = self._expand_transclusions(content, vault_path, metadata, depth=0)

        # Step 3: Wikilink normalization
        content = self._normalize_wikilinks(content, metadata)

        # Step 4: Comment stripping
        content = self._strip_comments(content)

        # Step 5: Dataview stripping
        content = self._strip_dataview(content)

        # Step 6: Callout simplification
        content = self._simplify_callouts(content)

        # Step 7: Inline tag extraction
        content = self._extract_inline_tags(content, metadata)

        # Step 8: Highlight simplification
        content = self._simplify_highlights(content)

        return content, metadata

    # ─── Step 1: Frontmatter ──────────────────────────────────────────────

    def _extract_frontmatter(self, content: str, metadata: dict[str, Any]) -> str:
        """Parse YAML frontmatter, extract title/tags/aliases, strip from content."""
        m = _FRONTMATTER_RE.match(content)
        if m is None:
            return content

        yaml_text = m.group(1) or ""
        try:
            import yaml

            parsed = yaml.safe_load(yaml_text)
        except Exception:
            parsed = None

        if isinstance(parsed, dict):
            if "title" in parsed and parsed["title"]:
                metadata["title"] = str(parsed["title"])
            if "tags" in parsed:
                tags = parsed["tags"]
                if isinstance(tags, list):
                    metadata["tags"] = [str(t).lstrip("#") for t in tags if t]
                elif isinstance(tags, str):
                    metadata["tags"] = [t.strip().lstrip("#") for t in tags.split(",") if t.strip()]
            if "aliases" in parsed:
                aliases = parsed["aliases"]
                if isinstance(aliases, list):
                    metadata["aliases"] = [str(a) for a in aliases if a]
                elif isinstance(aliases, str):
                    metadata["aliases"] = [a.strip() for a in aliases.split(",") if a.strip()]

        return content[m.end():]

    # ─── Step 2: Transclusion ──────────────────────────────────────────────

    def _expand_transclusions(
        self,
        content: str,
        vault_path: str | Path | None,
        metadata: dict[str, Any],
        depth: int,
        visited: set[str] | None = None,
    ) -> str:
        """Expand ![[note]] embeds recursively with cycle detection."""
        if visited is None:
            visited = set()

        def _replace_embed(m: re.Match) -> str:
            target = m.group(1)
            # Normalize: strip extension for matching
            base_name = Path(target).stem

            # Image embeds → placeholder
            ext = Path(target).suffix.lower()
            if ext in _IMAGE_EXTENSIONS or not ext and self._is_image(target):
                return f"[image: {Path(target).name}]"

            # Non-.md non-image embeds → neutral placeholder (avoids wikilink re-match)
            if ext and ext != ".md":
                return f"[embed: {target}]"

            # Cycle detection (checked before depth limit)
            if base_name in visited:
                return f"[circular: {base_name}]"

            # Depth limit — return name without further expansion
            if depth >= self.max_embed_depth:
                return base_name

            # Try to read the embedded note
            if vault_path is None:
                return f"[embed: {base_name}]"

            embedded_content = self._read_vault_note(vault_path, target)
            if embedded_content is None:
                return m.group(0)

            # Recursively expand the embedded content
            new_visited = visited | {base_name}
            expanded = self._expand_transclusions(
                embedded_content, vault_path, metadata, depth + 1, new_visited
            )
            return expanded

        return _TRANSCLUSION_RE.sub(_replace_embed, content)

    @staticmethod
    def _is_image(target: str) -> bool:
        """Heuristic: if target looks like an image filename without extension."""
        name = target.lower()
        return any(name.endswith(ext) for ext in _IMAGE_EXTENSIONS)

    @staticmethod
    def _read_vault_note(vault_path: str | Path, target: str) -> str | None:
        """Read a .md note from the vault by name/path."""
        vault = Path(vault_path)
        # Try exact path first
        candidate = vault / target
        if not candidate.suffix:
            candidate = candidate.with_suffix(".md")

        if candidate.is_file():
            try:
                return candidate.read_text(encoding="utf-8")
            except Exception:
                return None

        # Search by filename (without subpath)
        filename = Path(target).name
        if not Path(filename).suffix:
            filename += ".md"

        # Try root-level
        root_candidate = vault / filename
        if root_candidate.is_file():
            try:
                return root_candidate.read_text(encoding="utf-8")
            except Exception:
                return None

        return None

    # ─── Step 3: Wikilink normalization ────────────────────────────────────

    @staticmethod
    def _normalize_wikilinks(content: str, metadata: dict[str, Any]) -> str:
        """Convert [[Target]] → "Target", [[Target|Alias]] → "Alias"; collect entities."""

        def _replace(m: re.Match) -> str:
            inner = m.group(1)
            if "|" in inner:
                parts = inner.split("|", 1)
                entity = parts[0].strip()
                alias = parts[1].strip()
                metadata["linked_entities"].append(entity)
                return alias
            else:
                entity = inner.strip()
                metadata["linked_entities"].append(entity)
                return entity

        return _WIKILINK_RE.sub(_replace, content)

    # ─── Step 4: Comment stripping ─────────────────────────────────────────

    @staticmethod
    def _strip_comments(content: str) -> str:
        """Remove %%comment%% blocks."""
        return _COMMENT_RE.sub("", content)

    # ─── Step 5: Dataview stripping ───────────────────────────────────────

    @staticmethod
    def _strip_dataview(content: str) -> str:
        """Remove ```dataview code blocks."""
        return _DATAVIEW_RE.sub("", content)

    # ─── Step 6: Callout simplification ────────────────────────────────────

    @staticmethod
    def _simplify_callouts(content: str) -> str:
        """Convert > [!type] Title\\n> content → **Title**\\ncontent."""

        def _replace(m: re.Match) -> str:
            title = m.group(2).strip()
            body_lines = m.group(3)
            # Strip leading "> " from each line
            body = re.sub(r"^>\s?", "", body_lines, flags=re.MULTILINE).strip()
            if title:
                return f"**{title}**\n{body}"
            return body

        return _CALLOUT_RE.sub(_replace, content)

    # ─── Step 7: Inline tag extraction ────────────────────────────────────

    @staticmethod
    def _extract_inline_tags(content: str, metadata: dict[str, Any]) -> str:
        """Keep #tag inline, also collect to metadata.tags (deduplicated)."""
        existing = set(metadata.get("tags", []))
        for m in _INLINE_TAG_RE.finditer(content):
            tag = m.group(1)
            if tag not in existing:
                metadata["tags"].append(tag)
                existing.add(tag)
        return content

    # ─── Step 8: Highlight simplification ─────────────────────────────────

    @staticmethod
    def _simplify_highlights(content: str) -> str:
        """Convert ==text== → **text**."""
        return _HIGHLIGHT_RE.sub(r"**\1**", content)
