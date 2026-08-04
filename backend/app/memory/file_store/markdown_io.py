"""Markdown I/O — read/write memory files with frontmatter.

Uses python-frontmatter for parsing/serialization. Files are written
atomically: write to temp file then rename (POSIX) or delete+rename (Windows).
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import frontmatter as fm_lib

from app.memory.file_store.frontmatter import MemoryFrontmatter

logger = logging.getLogger(__name__)


@dataclass
class MemoryFile:
    """A parsed memory Markdown file."""

    path: str  # relative to workspace root
    frontmatter: MemoryFrontmatter
    body: str  # raw Markdown body (without frontmatter)

    @property
    def name(self) -> str:
        return self.frontmatter.name

    @property
    def content(self) -> str:
        """Full text for indexing: name + description + body."""
        parts = [self.frontmatter.name, self.frontmatter.description, self.body]
        return "\n".join(p for p in parts if p.strip())


def read_markdown(filepath: Path) -> MemoryFile | None:
    """Read a memory Markdown file from disk.

    Returns None if the file doesn't exist or isn't valid frontmatter Markdown.
    """
    if not filepath.exists():
        return None
    try:
        text = filepath.read_text(encoding="utf-8")
        post = fm_lib.loads(text)
        fm = MemoryFrontmatter.from_dict(dict(post.metadata))
        body = post.content
        rel = str(filepath)
        return MemoryFile(path=rel, frontmatter=fm, body=body)
    except Exception as e:
        logger.warning("read_markdown failed for %s: %s", filepath, e)
        return None


def write_markdown(filepath: Path, fm: MemoryFrontmatter, body: str) -> None:
    """Write a memory Markdown file to disk atomically.

    Creates parent directories if needed. Uses write-temp-then-rename for
    atomicity (with Windows fallback: delete then rename).
    """
    filepath.parent.mkdir(parents=True, exist_ok=True)
    post = fm_lib.Post(body, **fm.to_dict())
    content = fm_lib.dumps(post)

    tmp = filepath.with_suffix(filepath.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")

    if sys.platform == "win32":
        if filepath.exists():
            try:
                filepath.unlink()
            except OSError:
                pass
        os.rename(str(tmp), str(filepath))
    else:
        os.replace(str(tmp), str(filepath))


def delete_markdown(filepath: Path) -> bool:
    """Delete a memory file. Returns True if deleted, False if not found."""
    try:
        filepath.unlink()
        return True
    except FileNotFoundError:
        return False
    except Exception as e:
        logger.warning("delete_markdown failed for %s: %s", filepath, e)
        return False
