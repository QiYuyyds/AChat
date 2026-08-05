"""Wikilink parsing and rendering.

Extracts `[[target]]` links from Markdown body text and renders them
as relative paths. Supports:
  - `[[target]]` — plain wikilink (predicate = NULL)
  - `[[target|alias]]` — aliased wikilink
  - `predicate:: [[target]]` — predicate wikilink (e.g. `derived_from:: [[path]]`)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
_PREDICATE_WIKILINK_RE = re.compile(r"^(\w+)::\s*\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")


@dataclass
class WikilinkLink:
    """A parsed wikilink with optional predicate."""
    target: str
    predicate: str | None = None
    alias: str | None = None


def extract_wikilinks(body: str) -> list[str]:
    """Extract all wikilink targets from a Markdown body.

    Returns a list of target paths (without brackets). Duplicates are
    preserved in order of first appearance. Handles both plain `[[target]]`
    and predicate `pred:: [[target]]` syntax.
    """
    targets: list[str] = []
    seen: set[str] = set()
    for link in extract_wikilinks_detailed(body):
        if link.target not in seen:
            seen.add(link.target)
            targets.append(link.target)
    return targets


def extract_wikilinks_detailed(body: str) -> list[WikilinkLink]:
    """Extract all wikilinks with predicate info from a Markdown body.

    Scans line by line. For each line, first checks for predicate wikilink
    syntax (`predicate:: [[target]]`), then falls back to plain wikilink
    (`[[target]]`). This ensures predicate syntax is matched before plain.
    """
    links: list[WikilinkLink] = []
    seen: set[tuple[str, str | None]] = set()

    for line in body.split("\n"):
        # Try predicate wikilinks first
        pred_match = _PREDICATE_WIKILINK_RE.match(line.strip())
        if pred_match:
            predicate = pred_match.group(1)
            target = pred_match.group(2).strip()
            alias = pred_match.group(3)
            key = (target, predicate)
            if target and key not in seen:
                seen.add(key)
                links.append(WikilinkLink(
                    target=target,
                    predicate=predicate,
                    alias=alias.strip() if alias else None,
                ))
            continue

        # Fall back to plain wikilinks in the line
        for match in _WIKILINK_RE.finditer(line):
            target = match.group(1).strip()
            alias = match.group(2)
            key = (target, None)
            if target and key not in seen:
                seen.add(key)
                links.append(WikilinkLink(
                    target=target,
                    predicate=None,
                    alias=alias.strip() if alias else None,
                ))

    return links


def render_wikilinks(body: str) -> str:
    """Render wikilinks as Markdown links for display.

    `[[target]]` → `[target](target)` (relative link)
    `[[target|alias]]` → `[alias](target)`
    `predicate:: [[target]]` → `[predicate: target](target)`
    """
    def _replace_plain(match: re.Match) -> str:
        target = match.group(1).strip()
        alias = match.group(2)
        label = alias.strip() if alias else target
        return f"[{label}]({target})"

    def _replace_predicate(match: re.Match) -> str:
        predicate = match.group(1)
        target = match.group(2).strip()
        alias = match.group(3)
        label = alias.strip() if alias else f"{predicate}: {target}"
        return f"[{label}]({target})"

    # Replace predicate wikilinks first, then plain
    body = _PREDICATE_WIKILINK_RE.sub(_replace_predicate, body)
    body = _WIKILINK_RE.sub(_replace_plain, body)
    return body


def add_wikilink(body: str, target: str, predicate: str | None = None) -> str:
    """Append a wikilink to the end of the body if not already present."""
    if predicate:
        link = f"{predicate}:: [[{target}]]"
        if link in body:
            return body
    else:
        link = f"[[{target}]]"
        if link in body:
            return body

    if body.strip():
        return body.rstrip() + "\n" + link
    return link


def retarget_wikilinks(body: str, old_target: str, new_target: str) -> str:
    """Rewrite all wikilinks pointing to old_target to point to new_target.

    Handles both plain (`[[old]]`) and predicate (`pred:: [[old]]`) syntax.
    """
    # Replace predicate wikilinks: `pred:: [[old]]` → `pred:: [[new]]`
    def _replace_pred(match: re.Match) -> str:
        predicate = match.group(1)
        target = match.group(2).strip()
        alias = match.group(3)
        if target == old_target:
            alias_part = f"|{alias}" if alias else ""
            return f"{predicate}:: [[{new_target}{alias_part}]]"
        return match.group(0)

    body = _PREDICATE_WIKILINK_RE.sub(_replace_pred, body)

    # Replace plain wikilinks: `[[old]]` → `[[new]]`
    def _replace_plain(match: re.Match) -> str:
        target = match.group(1).strip()
        alias = match.group(2)
        if target == old_target:
            alias_part = f"|{alias}" if alias else ""
            return f"[[{new_target}{alias_part}]]"
        return match.group(0)

    body = _WIKILINK_RE.sub(_replace_plain, body)
    return body
