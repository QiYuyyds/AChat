"""Markdown I/O — read/write memory files with frontmatter.

Uses python-frontmatter for parsing/serialization. Files are written
atomically: write to temp file then rename (POSIX) or delete+rename (Windows).
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import frontmatter as fm_lib

from app.memory.file_store.frontmatter import MemoryFrontmatter
from app.memory.file_store.wikilinks import retarget_wikilinks

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
            with contextlib.suppress(OSError):
                filepath.unlink()
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


def move_file(
    src: Path,
    dst: Path,
    workspace_root: Path,
    retarget: bool = True,
) -> list[Path]:
    """Move a memory file and optionally retarget all inbound wikilinks.

    Args:
        src: Source file path (absolute).
        dst: Destination file path (absolute).
        workspace_root: Memory workspace root for scanning retarget targets.
        retarget: If True, scan all .md files in daily/ and digest/ and
            rewrite `[[old]]` → `[[new]]` (including predicate wikilinks).

    Returns:
        List of files whose wikilinks were rewritten (excluding src/dst).
    """
    if not src.exists():
        raise FileNotFoundError(f"Source file not found: {src}")

    # Compute relative paths for retargeting
    src_rel = str(src.relative_to(workspace_root)).replace("\\", "/")
    dst_rel = str(dst.relative_to(workspace_root)).replace("\\", "/")

    # Also handle absolute path forms that might appear in wikilinks
    src_abs = str(src)
    dst_abs = str(dst)

    retargeted_files: list[Path] = []

    if retarget:
        # Scan all .md files in daily/ and digest/ for wikilink retargeting
        scan_dirs = [
            workspace_root / "daily",
            workspace_root / "digest",
        ]
        for scan_dir in scan_dirs:
            if not scan_dir.exists():
                continue
            for md_file in scan_dir.rglob("*.md"):
                if md_file in (src, dst):
                    continue
                try:
                    text = md_file.read_text(encoding="utf-8")
                except Exception:
                    continue

                # Retarget using both relative and absolute path forms
                new_text = text
                if src_rel in new_text:
                    new_text = retarget_wikilinks(new_text, src_rel, dst_rel)
                if src_abs in new_text and src_abs != src_rel:
                    new_text = retarget_wikilinks(new_text, src_abs, dst_abs)

                if new_text != text:
                    md_file.write_text(new_text, encoding="utf-8")
                    retargeted_files.append(md_file)
                    logger.debug("move_file: retargeted wikilinks in %s", md_file)

    # Move the file
    dst.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        if dst.exists():
            with contextlib.suppress(OSError):
                dst.unlink()
        os.rename(str(src), str(dst))
    else:
        os.replace(str(src), str(dst))

    logger.info(
        "move_file: %s → %s (retargeted %d files)",
        src_rel, dst_rel, len(retargeted_files),
    )
    return retargeted_files
