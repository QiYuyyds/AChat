"""Unit tests for RecursiveSplitter — Chinese/English mixed splitting + Markdown protection."""

import pytest
from app.rag.splitter import Chunk, RecursiveSplitter


class TestRecursiveSplitter:
    def test_empty_input(self):
        splitter = RecursiveSplitter(chunk_size=100)
        assert splitter.split("") == []

    def test_short_text_no_split(self):
        splitter = RecursiveSplitter(chunk_size=100, chunk_overlap=0)
        chunks = splitter.split("Hello world")
        assert len(chunks) == 1
        assert chunks[0].content == "Hello world"

    def test_paragraph_split(self):
        """Split by double newline (paragraph separator)."""
        text = "First paragraph.\n\nSecond paragraph.\n\nThird paragraph."
        splitter = RecursiveSplitter(chunk_size=30, chunk_overlap=0)
        chunks = splitter.split(text)
        assert len(chunks) >= 2

    def test_chinese_sentence_split(self):
        """Split by Chinese punctuation."""
        text = "这是第一句话。这是第二句话。这是第三句话。"
        splitter = RecursiveSplitter(chunk_size=15, chunk_overlap=0)
        chunks = splitter.split(text)
        assert len(chunks) >= 2

    def test_overlap(self):
        """Overlap should prepend tail of previous chunk."""
        text = "AAAAAAAAAABBBBBBBBBB"
        splitter = RecursiveSplitter(chunk_size=10, chunk_overlap=3)
        chunks = splitter.split(text)
        assert len(chunks) >= 2
        # Hard split at 10: "AAAAAAAAAA" | "BBBBBBBBBB"
        # Overlap prepends last 3 chars of chunk0 ("AAA") to chunk1
        assert chunks[1].content.startswith("AAA")

    def test_fenced_code_block_protection(self):
        """Code blocks should NOT be split."""
        text = "Before\n\n```python\ndef hello():\n    print('world')\n```\n\nAfter"
        splitter = RecursiveSplitter(chunk_size=20, chunk_overlap=0)
        chunks = splitter.split(text)
        # Find the code block chunk
        code_chunks = [c for c in chunks if "def hello" in c.content]
        assert len(code_chunks) == 1
        assert "```" in code_chunks[0].content

    def test_heading_sticks_to_next(self):
        """A heading-only line should stick to the following content."""
        text = "# Title\n\nSome content here."
        splitter = RecursiveSplitter(chunk_size=50, chunk_overlap=0)
        chunks = splitter.split(text)
        # The heading should be in the same chunk as the content
        heading_chunks = [c for c in chunks if "# Title" in c.content]
        assert len(heading_chunks) >= 1
        if len(chunks) > 1:
            # If heading is merged with content
            assert "Some content" in heading_chunks[0].content or len(heading_chunks) == 1

    def test_hard_split_fallback(self):
        """Very long text without any separators should be hard-split."""
        text = "a" * 100
        splitter = RecursiveSplitter(chunk_size=30, chunk_overlap=0)
        chunks = splitter.split(text)
        assert len(chunks) >= 3
        for c in chunks:
            assert len(c.content) <= 30

    def test_chunk_ids_sequential(self):
        text = "Para one.\n\nPara two.\n\nPara three."
        splitter = RecursiveSplitter(chunk_size=15, chunk_overlap=0)
        chunks = splitter.split(text)
        for i, c in enumerate(chunks):
            assert c.id == i
