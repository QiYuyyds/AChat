"""Mermaid source normalisation + static validation.

Port of src/shared/mermaid-normalize.ts. Used by the artifact-content builder
(``app/services/artifact_service.py``) to preflight diagram artifacts before
they are stored, so the frontend's Mermaid renderer is given clean source.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_MERMAID_DECLARATION_RE = re.compile(
    r"^(?:flowchart|graph|sequenceDiagram|classDiagram|stateDiagram(?:-v2)?|"
    r"erDiagram|gantt|pie|journey|gitGraph|mindmap|timeline|quadrantChart|"
    r"requirementDiagram|C4Context|C4Container|C4Component|C4Dynamic|"
    r"C4Deployment|architecture-beta|block-beta|packet-beta|sankey-beta|"
    r"xychart-beta)\b",
    re.IGNORECASE,
)

_FENCE_RE = re.compile(r"^```(?:mermaid|mmd)?\s*\n([\s\S]*?)\n```$", re.IGNORECASE)
_FLOWCHART_RE = re.compile(r"^(?:flowchart|graph)\b", re.IGNORECASE)
_SUBGRAPH_RE = re.compile(r"^(\s*subgraph\s+)([A-Za-z][\w-]*)(\[)([^\]\"']+)(\]\s*)$")
_NODE_LABEL_RE = re.compile(r"(^|[\s;&])([A-Za-z][\w-]*)(\[)([^\]\"'\n]+)(\])")
_STYLE_RE = re.compile(r"^style\s+", re.IGNORECASE)
_VALID_STYLE_RE = re.compile(
    r"^style\s+[A-Za-z][\w-]*\s+[A-Za-z-]+:[^\s,]+(?:,[A-Za-z-]+:[^\s,]+)*$",
    re.IGNORECASE,
)


@dataclass
class MermaidNormaliseOutcome:
    ok: bool
    source: str | None = None
    error: str | None = None


def normalise_mermaid_source(raw_source: str) -> MermaidNormaliseOutcome:
    source = re.sub(r"\r\n?", "\n", _strip_mermaid_fence(raw_source)).strip()
    if not source:
        return MermaidNormaliseOutcome(ok=False, error="Mermaid source is empty.")

    first_line = _first_significant_line(source)
    if not first_line or not _MERMAID_DECLARATION_RE.search(first_line):
        return MermaidNormaliseOutcome(
            ok=False,
            error=(
                'Mermaid source must start with a supported diagram declaration '
                'such as "flowchart TD", "sequenceDiagram", or "classDiagram".'
            ),
        )

    normalised = (
        _normalise_flowchart_labels(source) if _is_flowchart(first_line) else source
    )
    validation_error = _validate_mermaid_source_static(normalised)
    if validation_error:
        return MermaidNormaliseOutcome(ok=False, error=validation_error)

    return MermaidNormaliseOutcome(ok=True, source=normalised)


def _strip_mermaid_fence(source: str) -> str:
    trimmed = source.strip()
    match = _FENCE_RE.match(trimmed)
    return match.group(1) if match else source


def _first_significant_line(source: str) -> str | None:
    for line in source.split("\n"):
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("%%"):
            continue
        return trimmed
    return None


def _is_flowchart(first_line: str) -> bool:
    return bool(_FLOWCHART_RE.search(first_line))


def _escape_mermaid_label(label: str) -> str:
    return label.strip().replace("\\", "\\\\").replace('"', '\\"')


def _normalise_flowchart_labels(source: str) -> str:
    out_lines: list[str] = []
    for line in source.split("\n"):
        subgraph = _SUBGRAPH_RE.match(line)
        if subgraph:
            out_lines.append(
                f"{subgraph.group(1)}{subgraph.group(2)}"
                f'["{_escape_mermaid_label(subgraph.group(4))}"]'
                f"{subgraph.group(5)[1:]}"
            )
            continue

        def _repl(m: re.Match[str]) -> str:
            return (
                f"{m.group(1)}{m.group(2)}{m.group(3)}"
                f'"{_escape_mermaid_label(m.group(4))}"{m.group(5)}'
            )

        out_lines.append(_NODE_LABEL_RE.sub(_repl, line))
    return "\n".join(out_lines)


def _validate_mermaid_source_static(source: str) -> str | None:
    for index, line in enumerate(source.split("\n")):
        line_number = index + 1
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("%%"):
            continue

        if trimmed.startswith("```"):
            return (
                f"Line {line_number}: remove Markdown code fences before saving a "
                "Mermaid diagram."
            )

        if _STYLE_RE.search(trimmed) and not _is_valid_style_line(trimmed):
            return (
                f"Line {line_number}: invalid style syntax. Use "
                '"style ID fill:#hex,color:#hex" without trailing prose.\n'
                f"{trimmed}"
            )

        balance_error = _validate_bracket_balance(trimmed)
        if balance_error:
            return f"Line {line_number}: {balance_error}\n{trimmed}"

    return None


def _is_valid_style_line(line: str) -> bool:
    return bool(_VALID_STYLE_RE.match(line))


_BRACKET_PAIRS = {"[": "]", "(": ")", "{": "}"}


def _validate_bracket_balance(line: str) -> str | None:
    stack: list[str] = []
    quote: str | None = None
    escaped = False

    for ch in line:
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue

        if ch in ('"', "'"):
            quote = ch
            continue
        if ch in ("[", "(", "{"):
            stack.append(ch)
            continue
        if ch in ("]", ")", "}"):
            open_ch = stack.pop() if stack else None
            if not open_ch or _BRACKET_PAIRS.get(open_ch) != ch:
                return f'unmatched "{ch}"'

    if quote:
        return f"unclosed {quote} quote"
    if stack:
        return f'unclosed "{stack[-1]}"'
    return None
