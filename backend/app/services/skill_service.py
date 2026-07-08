"""Skill service — local-filesystem storage for agent skills.

A skill is a directory containing a ``SKILL.md`` (YAML frontmatter with ``name``
and ``description`` + Markdown body) plus optional bundled files (scripts,
templates, references). Skills live under ``<data_dir>/skills/<slug>/`` and are
NEVER written to the database — only the agent↔skill binding (``Agent.skill_names``)
is persisted. See openspec/changes/add-agent-skills.

Three creation paths (upload single file / upload folder / agent ``write_skill``)
all funnel through :func:`save_skill`, sharing one contract check + naming/collision
rule, so the registry and ``load_skill`` are source-agnostic.
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)

SKILL_MD = "SKILL.md"
# Windows-illegal filename chars, plus whitespace, stripped during slugify.
_ILLEGAL = re.compile(r'[\\/:*?"<>|]')
# Max trigger_keywords entries; extra entries are truncated.
MAX_TRIGGER_KEYWORDS = 10


class SkillError(Exception):
    """Raised for skill contract / naming / collision violations."""


@dataclass
class SkillMeta:
    slug: str
    name: str
    description: str
    trigger_keywords: list[str] = field(default_factory=list)


def skills_root() -> Path:
    """Resolved ``<data_dir>/skills`` directory (created on demand)."""
    root = get_settings().data_path / "skills"
    root.mkdir(parents=True, exist_ok=True)
    return root


# ─── Frontmatter parsing ─────────────────────────────────────────────────────

def _parse_trigger_keywords(lines: list[str], start_idx: int, end_idx: int) -> list[str]:
    """Parse the ``trigger_keywords`` YAML list from frontmatter lines.

    Supports both block style (``- item`` lines) and inline style
    (``[item1, item2]``). Returns ``[]`` on missing/invalid format.
    """
    raw: object = None
    for i in range(start_idx, end_idx):
        ln = lines[i]
        if ":" not in ln:
            continue
        key, _, value = ln.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key != "trigger_keywords":
            continue
        if not value:
            # Block-style list: collect following ``- item`` lines
            items: list[str] = []
            for j in range(i + 1, end_idx):
                next_ln = lines[j].strip()
                if next_ln.startswith("- "):
                    items.append(next_ln[2:].strip().strip("\"'"))
                elif next_ln.startswith("-"):
                    items.append(next_ln[1:].strip().strip("\"'"))
                elif next_ln == "":
                    continue
                else:
                    break
            raw = items
            break
        else:
            # Inline list: ``[item1, item2]`` or bare scalar
            v = value.strip("\"'")
            if v.startswith("[") and v.endswith("]"):
                inner = v[1:-1]
                raw = [
                    item.strip().strip("\"'")
                    for item in inner.split(",")
                    if item.strip()
                ]
            else:
                raw = v
            break

    if raw is None:
        return []
    if not isinstance(raw, list):
        logger.warning(
            "SKILL.md trigger_keywords is not a list (got %s); treating as empty",
            type(raw).__name__,
        )
        return []
    result: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
        else:
            logger.warning(
                "SKILL.md trigger_keywords contains non-string entry %r; skipping",
                item,
            )
    if len(result) > MAX_TRIGGER_KEYWORDS:
        logger.warning(
            "SKILL.md trigger_keywords has %d entries; truncating to %d",
            len(result), MAX_TRIGGER_KEYWORDS,
        )
        result = result[:MAX_TRIGGER_KEYWORDS]
    return result


def parse_skill_md(text: str) -> tuple[str, str, list[str]]:
    """Extract ``(name, description, trigger_keywords)`` from a SKILL.md's frontmatter.

    Minimal line-based parser (avoids a YAML dependency): reads the leading
    ``---`` … ``---`` block for ``name:`` / ``description:`` keys and the
    optional ``trigger_keywords`` list. Raises :class:`SkillError` if the
    frontmatter or either required field is missing/empty.
    """
    stripped = text.lstrip("﻿")  # tolerate BOM
    if not stripped.lstrip().startswith("---"):
        raise SkillError("SKILL.md must start with a YAML frontmatter block (---)")

    lines = stripped.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip() == "---")
    end = None
    for i in range(start + 1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        raise SkillError("SKILL.md frontmatter block is not closed with ---")

    fields: dict[str, str] = {}
    for ln in lines[start + 1 : end]:
        if ":" not in ln:
            continue
        key, _, value = ln.partition(":")
        key = key.strip().lower()
        value = value.strip().strip("\"'")
        # trigger_keywords is parsed separately (list, not scalar)
        if key == "trigger_keywords":
            continue
        fields[key] = value

    name = fields.get("name", "").strip()
    description = fields.get("description", "").strip()
    if not name:
        raise SkillError("SKILL.md frontmatter is missing a non-empty 'name'")
    if not description:
        raise SkillError("SKILL.md frontmatter is missing a non-empty 'description'")
    trigger_keywords = _parse_trigger_keywords(lines, start + 1, end)
    return name, description, trigger_keywords


def strip_frontmatter(text: str) -> str:
    """Return the Markdown body after the leading frontmatter block."""
    stripped = text.lstrip("﻿")
    lines = stripped.splitlines()
    if not lines or lines[0].strip() != "---":
        return stripped
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[i + 1 :]).lstrip("\n")
    return stripped


# ─── Naming ──────────────────────────────────────────────────────────────────

def slugify(name: str) -> str:
    """Filesystem-safe kebab-case slug from a skill name.

    Removes Windows-illegal chars, lowercases, collapses whitespace/separators
    to single dashes. Raises :class:`SkillError` if nothing usable remains
    (e.g. a name made only of illegal/non-ASCII chars).
    """
    cleaned = _ILLEGAL.sub(" ", name)
    cleaned = cleaned.lower().strip()
    cleaned = re.sub(r"[\s_]+", "-", cleaned)
    cleaned = re.sub(r"[^a-z0-9-]", "", cleaned)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-")
    if not cleaned:
        raise SkillError(
            f"Cannot derive a folder name from skill name '{name}'; "
            "use latin letters/digits in the name"
        )
    return cleaned


# ─── Storage ─────────────────────────────────────────────────────────────────

def save_skill(files: dict[str, str]) -> SkillMeta:
    """Validate and write a skill to ``<data_dir>/skills/<slug>/``.

    ``files`` maps relative POSIX paths to text content; it MUST contain a
    root-level ``SKILL.md``. Slug is derived from the frontmatter ``name``;
    an existing slug is rejected (no overwrite, no suffix).
    """
    skill_md = files.get(SKILL_MD)
    if skill_md is None:
        raise SkillError("Upload must contain a root-level SKILL.md")

    name, description, trigger_keywords = parse_skill_md(skill_md)
    slug = slugify(name)

    target = skills_root() / slug
    if target.exists():
        raise SkillError(f"Skill '{slug}' already exists; delete it first to replace")

    target.mkdir(parents=True)
    try:
        for rel, content in files.items():
            dest = _safe_join(target, rel)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content, encoding="utf-8")
    except Exception:
        shutil.rmtree(target, ignore_errors=True)  # don't leave a half-written skill
        raise

    return SkillMeta(slug=slug, name=name, description=description, trigger_keywords=trigger_keywords)


def _safe_join(base: Path, rel: str) -> Path:
    """Join ``rel`` onto ``base``, rejecting path escapes."""
    dest = (base / rel).resolve()
    if base.resolve() not in dest.parents and dest != base.resolve():
        raise SkillError(f"Unsafe path in skill upload: {rel}")
    return dest


def list_skills() -> list[SkillMeta]:
    """Scan the skills root; return metadata for every valid skill dir.

    Directories without a parseable SKILL.md are skipped (not errors).
    """
    out: list[SkillMeta] = []
    for child in sorted(skills_root().iterdir()):
        if not child.is_dir():
            continue
        md = child / SKILL_MD
        if not md.is_file():
            continue
        try:
            name, description, trigger_keywords = parse_skill_md(md.read_text(encoding="utf-8"))
        except (SkillError, OSError):
            continue
        out.append(SkillMeta(slug=child.name, name=name, description=description, trigger_keywords=trigger_keywords))
    return out


def read_skill_body(slug: str) -> str:
    """Return the Markdown body (frontmatter stripped) of a skill's SKILL.md."""
    md = skills_root() / slug / SKILL_MD
    if not md.is_file():
        raise SkillError(f"Skill not found: {slug}")
    return strip_frontmatter(md.read_text(encoding="utf-8"))


def delete_skill(slug: str) -> None:
    """Delete a skill directory. No-op if it does not exist."""
    target = skills_root() / slug
    if target.is_dir():
        shutil.rmtree(target, ignore_errors=True)
