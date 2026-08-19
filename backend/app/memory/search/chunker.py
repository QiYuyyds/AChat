"""Markdown semantic chunker — heading-based splitting with breadcrumb context.

Splits memory Markdown files by ## / ### headings into semantic chunks.
Each chunk includes:
  1. Frontmatter name + description as global prefix
  2. Breadcrumb title path (e.g. "项目配置 > 前端框架 > React 19")
  3. Section body content

Short adjacent sections (< min_chunk_size) are merged; long sections
(> chunk_size) are split at paragraph boundaries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.memory.file_store.markdown_io import MemoryFile

_HEADING_RE = re.compile(r"^(#{2,3})\s+(.+)$", re.MULTILINE)


@dataclass
class Chunk:
    """A single chunk of a memory file."""

    text: str  # full chunk text (prefix + breadcrumb + body)
    section_path: str  # "标题1 > 标题2"
    char_count: int


class MarkdownChunker:
    """Heading-based Markdown chunker with breadcrumb and frontmatter injection."""

    def __init__(self, chunk_size: int = 512, min_chunk_size: int = 100):
        self.chunk_size = chunk_size
        self.min_chunk_size = min_chunk_size

    def chunk(self, mem_file: MemoryFile) -> list[Chunk]:
        """Split a memory file into semantic chunks."""
        # Build frontmatter prefix
        prefix_parts = [
            p for p in (
                mem_file.frontmatter.name,
                mem_file.frontmatter.description,
            ) if p and p.strip()
        ]
        prefix = "\n".join(prefix_parts) if prefix_parts else ""

        # Split body into sections by headings
        sections = self._split_by_headings(mem_file.body)

        # Merge short adjacent sections
        sections = self._merge_short(sections)

        # Build final chunks with prefix + breadcrumb
        chunks: list[Chunk] = []
        for section_path, body in sections:
            # Split over-long sections at paragraph boundaries
            sub_texts = self._split_long(body)
            for sub_text in sub_texts:
                parts: list[str] = []
                if prefix:
                    parts.append(prefix)
                if section_path:
                    parts.append(section_path)
                parts.append(sub_text)
                text = "\n".join(parts)
                chunks.append(Chunk(
                    text=text,
                    section_path=section_path,
                    char_count=len(text),
                ))

        return chunks if chunks else [Chunk(
            text="\n".join(p for p in [prefix, mem_file.body] if p),
            section_path="",
            char_count=len(mem_file.body),
        )]

    def _split_by_headings(self, body: str) -> list[tuple[str, str]]:
        """Split body by ## and ### headings. Returns list of (section_path, body).

        # is treated as file title, not a section boundary.
        """
        if not body or not body.strip():
            return [("", "")]

        # Find all heading positions
        matches = list(_HEADING_RE.finditer(body))
        if not matches:
            return [("", body.strip())]

        sections: list[tuple[str, str]] = []
        heading_stack: list[tuple[int, str]] = []  # (level, title)

        for i, match in enumerate(matches):
            level = len(match.group(1))  # 2 for ##, 3 for ###
            title = match.group(2).strip()

            # Update heading stack: pop deeper or same-level headings
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, title))

            # Build breadcrumb from stack
            section_path = " > ".join(t for _, t in heading_stack)

            # Extract section body (from end of this heading line to start of next heading)
            body_start = match.end()
            if i + 1 < len(matches):
                body_end = matches[i + 1].start()
            else:
                body_end = len(body)
            section_body = body[body_start:body_end].strip()

            sections.append((section_path, section_body))

        return sections

    def _merge_short(self, sections: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """Merge adjacent sections shorter than min_chunk_size."""
        if not sections:
            return sections

        merged: list[tuple[str, str]] = []
        for section_path, body in sections:
            if merged and len(merged[-1][1]) < self.min_chunk_size and len(body) < self.min_chunk_size:
                # Merge into previous section
                prev_path, prev_body = merged[-1]
                merged[-1] = (
                    prev_path,
                    prev_body + "\n\n" + body,
                )
            else:
                merged.append((section_path, body))

        # Final pass: if the last section is too short, merge it into the previous one
        if len(merged) >= 2 and len(merged[-1][1]) < self.min_chunk_size:
            prev_path, prev_body = merged[-2]
            last_path, last_body = merged[-1]
            merged[-2] = (
                prev_path,
                prev_body + "\n\n" + last_body,
            )
            merged.pop()

        return merged

    def _split_long(self, text: str) -> list[str]:
        """Split text longer than chunk_size at paragraph boundaries."""
        if len(text) <= self.chunk_size:
            return [text]

        paragraphs = text.split("\n\n")
        if len(paragraphs) <= 1:
            # No paragraph boundary — split by lines as fallback
            return self._split_by_lines(text)

        result: list[str] = []
        current = ""
        for para in paragraphs:
            if not current:
                current = para
            elif len(current) + len(para) + 2 <= self.chunk_size:
                current = current + "\n\n" + para
            else:
                result.append(current)
                current = para
        if current:
            result.append(current)

        return result if result else [text]

    def _split_by_lines(self, text: str) -> list[str]:
        """Fallback: split by lines when no paragraph boundaries exist."""
        lines = text.split("\n")
        result: list[str] = []
        current = ""
        for line in lines:
            if not current:
                current = line
            elif len(current) + len(line) + 1 <= self.chunk_size:
                current = current + "\n" + line
            else:
                result.append(current)
                current = line
        if current:
            result.append(current)
        return result if result else [text]
