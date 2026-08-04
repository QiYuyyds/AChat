"""Wikilink parsing and rendering.

Extracts `[[target]]` links from Markdown body text and renders them
as relative paths. Supports `[[target]]` and `[[target|alias]]` syntax.
"""

from __future__ import annotations

import re

_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")


def extract_wikilinks(body: str) -> list[str]:
    """Extract all wikilink targets from a Markdown body.

    Returns a list of target paths (without brackets). Duplicates are
    preserved in order of first appearance.
    """
    targets: list[str] = []
    seen: set[str] = set()
    for match in _WIKILINK_RE.finditer(body):
        target = match.group(1).strip()
        if target and target not in seen:
            seen.add(target)
            targets.append(target)
    return targets


def render_wikilinks(body: str) -> str:
    """Render wikilinks as Markdown links for display.

    `[[target]]` → `[target](target)` (relative link)
    `[[target|alias]]` → `[alias](target)`
    """
    def _replace(match: re.Match) -> str:
        target = match.group(1).strip()
        alias = match.group(2)
        label = alias.strip() if alias else target
        return f"[{label}]({target})"

    return _WIKILINK_RE.sub(_replace, body)


def add_wikilink(body: str, target: str) -> str:
    """Append a wikilink to the end of the body if not already present."""
    if f"[[{target}]]" in body:
        return body
    link = f"[[{target}]]"
    if body.strip():
        return body.rstrip() + "\n" + link
    return link
