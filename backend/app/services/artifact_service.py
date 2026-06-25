"""Artifact content normalisation.

Port of src/server/artifact-content.ts. Loose ``content`` from the LLM (or the
user panel) is coerced into a strongly-shaped artifact content dict; invalid
input returns None. The ``write_artifact`` tool and the user-panel version
creation share this so validation has a single source of truth.

Output dicts use **camelCase** keys to stay byte-compatible with the existing
frontend and the TS-written rows (e.g. ``workspacePath``, ``targetArtifactId``).

The artifact CRUD / version-chain helpers (createArtifactVersion etc.) used by
the artifact API live with 阶段 6's routes; this module is the shared validator.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select

from app.db.engine import get_db
from app.db.models import Artifact, Conversation
from app.utils.clock import now_ms
from app.utils.ids import new_artifact_id
from app.utils.mermaid_normalize import normalise_mermaid_source
from app.utils.ppt_normalize import normalize_blocks

MAX_DIAGRAM_SOURCE_CHARS = 50_000


def build_artifact_content(artifact_type: str, raw_input: Any) -> dict[str, Any] | None:
    """Coerce loose content into a typed artifact content dict, or None."""
    raw = _unwrap_stringified_content(raw_input)

    if artifact_type == "web_app":
        return _build_web_app(raw)
    if artifact_type == "document":
        return _build_document(raw)
    if artifact_type == "diagram":
        return _build_diagram(raw)
    if artifact_type == "image":
        return _build_image(raw)
    if artifact_type == "diff":
        return _build_diff(raw)
    if artifact_type == "code_file":
        return _build_code_file(raw)
    if artifact_type == "ppt":
        return _build_ppt(raw)
    return None


def describe_artifact_content_error(artifact_type: str, raw_input: Any) -> str | None:
    if artifact_type != "diagram":
        return None
    raw = _unwrap_stringified_content(raw_input)
    if isinstance(raw, dict):
        source = (
            _read_string(raw.get("source"))
            or _read_string(raw.get("mermaid"))
            or _read_string(raw.get("code"))
            or _read_string(raw.get("content"))
        )
    elif isinstance(raw, str):
        source = raw
    else:
        source = None
    if not source:
        return "Invalid diagram content: missing Mermaid source."
    if len(source.strip()) > MAX_DIAGRAM_SOURCE_CHARS:
        return (
            f"Invalid diagram content: Mermaid source exceeds "
            f"{MAX_DIAGRAM_SOURCE_CHARS} characters."
        )
    result = normalise_mermaid_source(source)
    if not result.ok:
        return f"Invalid Mermaid diagram: {result.error}"
    return None


# ─── per-type builders ──────────────────────────────────────────────────────
def _build_web_app(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        obj = raw
        files = obj.get("files")
        if isinstance(files, dict):
            normalised = {k: v for k, v in files.items() if isinstance(v, str)}
            if not normalised:
                return None
            entry = obj.get("entry")
            return {
                "type": "web_app",
                "files": normalised,
                "entry": entry if isinstance(entry, str) else "index.html",
            }

        if (
            isinstance(obj.get("html"), str)
            or isinstance(obj.get("css"), str)
            or isinstance(obj.get("js"), str)
        ):
            out_files: dict[str, str] = {}
            if isinstance(obj.get("html"), str):
                out_files["index.html"] = obj["html"]
            if isinstance(obj.get("css"), str):
                out_files["style.css"] = obj["css"]
            if isinstance(obj.get("js"), str):
                out_files["script.js"] = obj["js"]
            return {"type": "web_app", "files": out_files, "entry": "index.html"}

        if isinstance(obj.get("content"), str):
            return {
                "type": "web_app",
                "files": {"index.html": obj["content"]},
                "entry": "index.html",
            }
        if isinstance(obj.get("code"), str):
            return {
                "type": "web_app",
                "files": {"index.html": obj["code"]},
                "entry": "index.html",
            }

    if isinstance(raw, str):
        return {"type": "web_app", "files": {"index.html": raw}, "entry": "index.html"}
    return None


def _build_document(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        for key in ("content", "markdown", "text"):
            if isinstance(raw.get(key), str):
                return {"type": "document", "format": "markdown", "content": raw[key]}
    if isinstance(raw, str):
        return {"type": "document", "format": "markdown", "content": raw}
    return None


def _build_diagram(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        syntax = _read_string(raw.get("syntax")) or _read_string(raw.get("format")) or "mermaid"
        if syntax.lower() != "mermaid":
            return None
        source = (
            _read_string(raw.get("source"))
            or _read_string(raw.get("mermaid"))
            or _read_string(raw.get("code"))
            or _read_string(raw.get("content"))
        )
        if not source:
            return None
        normalised = _normalise_diagram_source(source)
        if not normalised:
            return None
        theme = _normalise_mermaid_theme(raw.get("theme"))
        out: dict[str, Any] = {"type": "diagram", "syntax": "mermaid", "source": normalised}
        if theme:
            out["theme"] = theme
        return out
    if isinstance(raw, str):
        normalised = _normalise_diagram_source(raw)
        if not normalised:
            return None
        return {"type": "diagram", "syntax": "mermaid", "source": normalised}
    return None


def _build_image(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict) and isinstance(raw.get("url"), str):
        alt = raw.get("alt")
        return {"type": "image", "url": raw["url"], "alt": alt if isinstance(alt, str) else ""}
    if isinstance(raw, str):
        return {"type": "image", "url": raw, "alt": ""}
    return None


def _build_diff(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    target = _read_string(raw.get("targetArtifactId")) or _read_string(raw.get("targetId"))
    if not target:
        return None
    if isinstance(raw.get("hunks"), list):
        hunks = _normalise_hunks(raw["hunks"])
    else:
        hunks = _parse_unified_diff(_read_string(raw.get("diff")) or _read_string(raw.get("patch")))
    if not hunks:
        return None
    return {
        "type": "diff",
        "targetArtifactId": target,
        "hunks": hunks,
        "applied": raw["applied"] if isinstance(raw.get("applied"), bool) else False,
    }


def _build_code_file(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    workspace_path = _read_string(raw.get("workspacePath")) or _read_string(raw.get("path"))
    if not workspace_path:
        return None
    return {
        "type": "code_file",
        "workspacePath": workspace_path,
        "language": _read_string(raw.get("language")) or _guess_language(workspace_path),
        "sizeBytes": _read_non_negative_number(raw.get("sizeBytes")) or 0,
        "checksum": _read_string(raw.get("checksum")) or "",
    }


def _build_ppt(raw: Any) -> dict[str, Any] | None:
    obj = raw if isinstance(raw, dict) else None
    if _contains_unbounded_binary_payload(raw):
        return None
    if isinstance(raw, list):
        raw_slides: list[Any] | None = raw
    elif obj is not None and isinstance(obj.get("slides"), list):
        raw_slides = obj["slides"]
    else:
        raw_slides = None
    if raw_slides is None:
        return None

    slides: list[dict[str, Any]] = []
    for item in raw_slides:
        if not isinstance(item, dict):
            continue
        title = _read_string(item.get("title"))
        subtitle = _read_string(item.get("subtitle"))
        bullets: list[str] | None = None
        if isinstance(item.get("bullets"), list):
            bullets = [b for b in item["bullets"] if isinstance(b, str)]
        elif isinstance(item.get("bullets"), str):
            bullets = [x.strip() for x in item["bullets"].split("\n") if x.strip()]
        elif isinstance(item.get("points"), list):
            bullets = [b for b in item["points"] if isinstance(b, str)]
        blocks = normalize_blocks(item.get("blocks"))
        notes = _read_string(item.get("notes"))
        layout = _normalise_ppt_layout(item.get("layout"))
        if (
            not title
            and not subtitle
            and (not bullets)
            and not blocks
            and layout != "blank"
        ):
            continue
        slide: dict[str, Any] = {}
        if title:
            slide["title"] = title
        if subtitle:
            slide["subtitle"] = subtitle
        if bullets:
            slide["bullets"] = bullets
        if blocks:
            slide["blocks"] = blocks
        if notes:
            slide["notes"] = notes
        if layout:
            slide["layout"] = layout
        slides.append(slide)
    if not slides:
        return None

    deck_title = _read_string(obj.get("title")) if obj else None
    theme = _normalise_ppt_theme(obj.get("theme")) if obj else None
    out: dict[str, Any] = {"type": "ppt"}
    if deck_title:
        out["title"] = deck_title
    if theme:
        out["theme"] = theme
    out["slides"] = slides
    return out


# ─── stringified-content unwrapping ─────────────────────────────────────────
_CONTENT_WRAPPER_KEYS = [
    "format", "content", "markdown", "text", "files", "entry", "html", "css", "js",
    "code", "url", "source", "mermaid", "targetArtifactId", "targetId", "hunks",
    "diff", "patch", "workspacePath", "path", "language", "sizeBytes", "checksum",
    "slides", "blocks", "subtitle",
]

_WRAPPER_SIGNATURE_RE = re.compile(
    r'"(?:format|content|markdown|text|files|entry|html|source|mermaid|'
    r"targetArtifactId|targetId|hunks|diff|patch|workspacePath|path|slides|"
    r'blocks|subtitle)"\s*:'
)


def _unwrap_stringified_content(raw: Any) -> Any:
    if not isinstance(raw, str):
        return raw
    trimmed = raw.strip()
    if not trimmed.startswith("{"):
        return raw

    try:
        parsed = json.loads(trimmed)
        if _is_wrapper_object(parsed):
            return parsed
    except (ValueError, TypeError):
        pass

    if _WRAPPER_SIGNATURE_RE.search(trimmed):
        candidate = _first_balanced_object(_fix_invalid_json_escapes(trimmed)) or _first_balanced_object(trimmed)
        if candidate:
            try:
                parsed = json.loads(candidate)
                if _is_wrapper_object(parsed):
                    return parsed
            except (ValueError, TypeError):
                pass
    return raw


def _is_wrapper_object(v: Any) -> bool:
    return isinstance(v, dict) and any(k in v for k in _CONTENT_WRAPPER_KEYS)


def _fix_invalid_json_escapes(s: str) -> str:
    return re.sub(
        r"\\(.)",
        lambda m: m.group(0) if re.match(r'["\\/bfnrtu]', m.group(1)) else "\\\\" + m.group(1),
        s,
    )


def _first_balanced_object(s: str) -> str | None:
    start = s.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start : i + 1]
    return None


# ─── diff helpers ───────────────────────────────────────────────────────────
_HUNK_HEADER_RE = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@")
_HUNK_META_RE = re.compile(r"^@@\s+-\d+(?:,\d+)?\s+\+\d+(?:,\d+)?\s+@@")


def _normalise_hunks(raw_hunks: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in raw_hunks:
        if not isinstance(raw, dict):
            continue
        lines = (
            [
                line
                for line in raw["lines"]
                if isinstance(line, str) and not _is_unified_diff_metadata_line(line)
            ]
            if isinstance(raw.get("lines"), list)
            else []
        )
        if not lines:
            continue
        out.append(
            {
                "oldStart": _read_positive_number(raw.get("oldStart")) or 1,
                "oldLines": _read_non_negative_number(raw.get("oldLines"))
                if _read_non_negative_number(raw.get("oldLines")) is not None
                else _count_hunk_lines(lines, "+"),
                "newStart": _read_positive_number(raw.get("newStart")) or 1,
                "newLines": _read_non_negative_number(raw.get("newLines"))
                if _read_non_negative_number(raw.get("newLines")) is not None
                else _count_hunk_lines(lines, "-"),
                "lines": lines,
            }
        )
    return out


def _parse_unified_diff(raw_diff: str | None) -> list[dict[str, Any]]:
    if not raw_diff:
        return []
    hunks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in raw_diff.replace("\r\n", "\n").split("\n"):
        header = _HUNK_HEADER_RE.match(line)
        if header:
            current = {
                "oldStart": int(header.group(1)),
                "oldLines": int(header.group(2)) if header.group(2) else 1,
                "newStart": int(header.group(3)),
                "newLines": int(header.group(4)) if header.group(4) else 1,
                "lines": [],
            }
            hunks.append(current)
            continue
        if current is None:
            continue
        if line.startswith("\\ No newline") or _is_unified_diff_metadata_line(line):
            continue
        if line.startswith("+") or line.startswith("-") or line.startswith(" "):
            current["lines"].append(line)
    return [h for h in hunks if h["lines"]]


def _count_hunk_lines(lines: list[str], excluded_prefix: str) -> int:
    return len([line for line in lines if not line.startswith(excluded_prefix)])


def _is_unified_diff_metadata_line(line: str) -> bool:
    return bool(_HUNK_META_RE.match(line))


# ─── scalar readers ─────────────────────────────────────────────────────────
def _read_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _read_non_negative_number(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        n: float = value
    elif isinstance(value, str):
        try:
            n = float(value)
        except ValueError:
            return None
    else:
        return None
    import math

    return int(math.floor(n)) if math.isfinite(n) and n >= 0 else None


def _read_positive_number(value: Any) -> int | None:
    n = _read_non_negative_number(value)
    return n if n and n > 0 else None


def _guess_language(workspace_path: str) -> str:
    ext = workspace_path.split(".")[-1].lower() if "." in workspace_path else ""
    aliases = {
        "js": "javascript",
        "jsx": "javascript",
        "ts": "typescript",
        "tsx": "typescript",
        "md": "markdown",
        "markdown": "markdown",
        "html": "html",
        "css": "css",
        "json": "json",
        "yml": "yaml",
        "yaml": "yaml",
    }
    return aliases.get(ext) or (ext or "text")


def _normalise_diagram_source(source: str) -> str | None:
    if len(source.strip()) > MAX_DIAGRAM_SOURCE_CHARS:
        return None
    result = normalise_mermaid_source(source)
    return result.source if result.ok else None


def _normalise_mermaid_theme(value: Any) -> str | None:
    theme = _read_string(value)
    return theme if theme in ("default", "base", "dark", "forest", "neutral") else None


def _normalise_ppt_layout(value: Any) -> str | None:
    v = _read_string(value)
    return v if v in (
        "title", "title-bullets", "section", "blank", "content",
        "two-column", "metrics", "timeline", "quote",
    ) else None


def _normalise_ppt_theme(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    obj = value

    def hex_color(*keys: str) -> str | None:
        for key in keys:
            s = _read_string(obj.get(key))
            if s:
                return s.lstrip("#")
        return None

    def font(*keys: str) -> str | None:
        for key in keys:
            s = _read_string(obj.get(key))
            if s:
                return s
        return None

    theme: dict[str, str] = {}
    primary = hex_color("primary", "primaryColor", "color")
    if primary:
        theme["primary"] = primary
    background = hex_color("background", "bg")
    if background:
        theme["background"] = background
    surface = hex_color("surface", "card")
    if surface:
        theme["surface"] = surface
    text_body = hex_color("textBody", "text", "bodyColor")
    if text_body:
        theme["textBody"] = text_body
    text_muted = hex_color("textMuted", "muted")
    if text_muted:
        theme["textMuted"] = text_muted
    accent_positive = hex_color("accentPositive", "positive", "success")
    if accent_positive:
        theme["accentPositive"] = accent_positive
    accent_negative = hex_color("accentNegative", "negative", "danger", "warning")
    if accent_negative:
        theme["accentNegative"] = accent_negative
    divider = hex_color("divider", "border")
    if divider:
        theme["divider"] = divider
    font_heading = font("fontHeading", "headingFont", "fontFace", "font")
    if font_heading:
        theme["fontHeading"] = font_heading
    font_body = font("fontBody", "bodyFont", "font")
    if font_body:
        theme["fontBody"] = font_body
    return theme or None


_DATA_URI_RE = re.compile(r"^data:[^,]+;base64,", re.IGNORECASE)


def _contains_unbounded_binary_payload(value: Any) -> bool:
    if isinstance(value, str):
        return bool(_DATA_URI_RE.match(value.strip()))
    if isinstance(value, list):
        return any(_contains_unbounded_binary_payload(v) for v in value)
    if isinstance(value, dict):
        return any(_contains_unbounded_binary_payload(v) for v in value.values())
    return False


# ─── artifact CRUD / version-chain / export (阶段 6 service layer) ────────────
# Port of src/server/artifact-service.ts plus the deterministic helpers the
# artifact API routes need (get one, version chain, export serialisation). The
# routes are thin: they call these and translate the result to HTTP. Returned
# dicts use **camelCase** keys to stay byte-compatible with the frontend.


@dataclass(frozen=True)
class ArtifactWithMeta:
    """A flattened artifact row joined with its conversation title (no N+1)."""

    id: str
    conversation_id: str
    conversation_title: str | None
    type: str
    title: str
    version: int
    parent_artifact_id: str | None
    created_by_agent_id: str
    created_at: int

    def to_camel(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "conversationId": self.conversation_id,
            "conversationTitle": self.conversation_title,
            "type": self.type,
            "title": self.title,
            "version": self.version,
            "parentArtifactId": self.parent_artifact_id,
            "createdByAgentId": self.created_by_agent_id,
            "createdAt": self.created_at,
        }


def artifact_to_dict(row: Artifact) -> dict[str, Any]:
    """Serialise a full artifact row to a camelCase dict (matches the TS row)."""
    return {
        "id": row.id,
        "conversationId": row.conversation_id,
        "type": row.type,
        "title": row.title,
        "content": row.content_dict,
        "version": row.version,
        "parentArtifactId": row.parent_artifact_id,
        "createdByAgentId": row.created_by_agent_id,
        "createdAt": row.created_at,
    }


async def list_artifacts() -> list[ArtifactWithMeta]:
    """All artifacts, newest first, each joined with its conversation title."""
    async with get_db() as db:
        rows = (
            (await db.execute(select(Artifact).order_by(Artifact.created_at.desc())))
            .scalars()
            .all()
        )
        if not rows:
            return []

        conv_ids = list({r.conversation_id for r in rows})
        convs = (
            (await db.execute(select(Conversation).where(Conversation.id.in_(conv_ids))))
            .scalars()
            .all()
        )
        title_by_id = {c.id: c.title for c in convs}

        return [
            ArtifactWithMeta(
                id=r.id,
                conversation_id=r.conversation_id,
                conversation_title=title_by_id.get(r.conversation_id),
                type=r.type,
                title=r.title,
                version=r.version,
                parent_artifact_id=r.parent_artifact_id,
                created_by_agent_id=r.created_by_agent_id,
                created_at=r.created_at,
            )
            for r in rows
        ]


async def get_artifact(artifact_id: str) -> dict[str, Any] | None:
    """Single artifact as a camelCase dict, or None if missing."""
    async with get_db() as db:
        row = await db.get(Artifact, artifact_id)
        return artifact_to_dict(row) if row else None


async def delete_artifact(artifact_id: str) -> None:
    """Delete an artifact. Raises ValueError if it does not exist."""
    async with get_db() as db:
        row = await db.get(Artifact, artifact_id)
        if row is None:
            raise ValueError(f"Artifact not found: {artifact_id}")
        await db.delete(row)


@dataclass(frozen=True)
class CreateArtifactVersionResult:
    """ok=True carries the new artifact dict; ok=False carries error + status."""

    ok: bool
    artifact: dict[str, Any] | None = None
    error: str | None = None
    status: Literal[400, 404] | None = None


async def create_artifact_version(
    parent_artifact_id: str,
    raw_content: Any,
    title: str | None = None,
) -> CreateArtifactVersionResult:
    """Create a new version off an existing artifact (user-panel "save as new version").

    Inherits the parent's conversationId / type / createdByAgentId (no migration,
    no FK churn); version = parent.version + 1, parentArtifactId links the chain.
    Content goes through ``build_artifact_content`` — same validation as
    ``write_artifact`` — so the two write paths stay consistent.
    """
    async with get_db() as db:
        parent = await db.get(Artifact, parent_artifact_id)
        if parent is None:
            return CreateArtifactVersionResult(
                ok=False, error=f"Artifact not found: {parent_artifact_id}", status=404
            )

        content = build_artifact_content(parent.type, raw_content)
        if content is None:
            return CreateArtifactVersionResult(
                ok=False,
                error=describe_artifact_content_error(parent.type, raw_content)
                or f"Invalid content for type {parent.type}",
                status=400,
            )

        resolved_title = (title.strip() if title else "") or parent.title
        artifact = Artifact(
            id=new_artifact_id(),
            conversation_id=parent.conversation_id,
            type=parent.type,
            title=resolved_title,
            version=parent.version + 1,
            parent_artifact_id=parent.id,
            created_by_agent_id=parent.created_by_agent_id,
            created_at=now_ms(),
        )
        artifact.content_dict = content
        db.add(artifact)
        await db.flush()
        result = artifact_to_dict(artifact)

    return CreateArtifactVersionResult(ok=True, artifact=result)


async def list_artifact_versions(artifact_id: str) -> list[dict[str, Any]] | None:
    """The full version chain containing ``artifact_id``, ascending by version.

    Climb parentArtifactId to the root, then BFS down all descendants (separate
    visited set from the climb to avoid the historical "climbed vs visited" bug).
    Returns None if the artifact does not exist.
    """
    async with get_db() as db:
        root = await db.get(Artifact, artifact_id)
        if root is None:
            return None

        # 1) climb to the root
        climbed: set[str] = {root.id}
        while root.parent_artifact_id and root.parent_artifact_id not in climbed:
            climbed.add(root.parent_artifact_id)
            parent = await db.get(Artifact, root.parent_artifact_id)
            if parent is None:
                break
            root = parent

        # 2) BFS down all descendants
        collected: list[Artifact] = [root]
        visited: set[str] = {root.id}
        queue: list[str] = [root.id]
        while queue:
            parent_id = queue.pop(0)
            children = (
                (
                    await db.execute(
                        select(Artifact).where(Artifact.parent_artifact_id == parent_id)
                    )
                )
                .scalars()
                .all()
            )
            for child in children:
                if child.id in visited:
                    continue
                visited.add(child.id)
                collected.append(child)
                queue.append(child.id)

        collected.sort(key=lambda a: a.version)
        return [artifact_to_dict(a) for a in collected]


@dataclass(frozen=True)
class ArtifactExport:
    """Serialised export of one artifact.

    ``kind`` tells the router how to respond:
      - "file"     → write ``body`` (bytes) with ``content_type`` + ``filename``
      - "redirect" → 302 to ``redirect_url`` (image artifacts)
      - "error"    → respond ``error`` with HTTP ``status`` (400/404/501)
      - "deferred" → router must finish (ppt → .pptx, project → workspace zip);
                     ``deferred_kind`` is "ppt" or "project".
    """

    kind: Literal["file", "redirect", "error", "deferred"]
    filename: str | None = None
    content_type: str | None = None
    body: bytes | None = None
    redirect_url: str | None = None
    error: str | None = None
    status: int | None = None
    deferred_kind: Literal["ppt", "project"] | None = None
    base_name: str | None = None


async def serialize_artifact_export(
    artifact_id: str, export_mode: str = "editable"
) -> ArtifactExport:
    """Serialise an artifact for one-click export.

    Handles the self-contained types (web_app ZIP, document .md, image redirect,
    diagram .mmd, JSON fallback) directly. ppt / project need extra machinery
    (pptx generation / workspace access) so they are returned as ``deferred`` for
    the router to finish; the content dict is reachable via ``get_artifact``.
    """
    if export_mode not in ("editable", "visual"):
        return ArtifactExport(
            kind="error", error=f"Unsupported export mode: {export_mode}", status=400
        )

    async with get_db() as db:
        row = await db.get(Artifact, artifact_id)
        if row is None:
            return ArtifactExport(kind="error", error="Artifact not found", status=404)
        content = row.content_dict
        title = row.title
        version = row.version

    safe_title = _sanitize_file_name(title) or "artifact"
    base_name = f"{safe_title}-v{version}"
    ctype = content.get("type") if isinstance(content, dict) else None

    if ctype == "web_app":
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            files = content.get("files") or {}
            for name, body in files.items():
                if isinstance(body, str):
                    zf.writestr(name, body)
            entry = content.get("entry", "index.html")
            zf.writestr(
                "README.txt",
                f"Artifact: {title}\nVersion: v{version}\nEntry: {entry}\n\n"
                f"打开 {entry} 即可在浏览器中查看。\n"
                f"导出时间: {_iso_now()}\n",
            )
        return ArtifactExport(
            kind="file",
            filename=f"{base_name}.zip",
            content_type="application/zip",
            body=buf.getvalue(),
        )

    if ctype == "document":
        return ArtifactExport(
            kind="file",
            filename=f"{base_name}.md",
            content_type="text/markdown; charset=utf-8",
            body=str(content.get("content", "")).encode("utf-8"),
        )

    if ctype == "image":
        return ArtifactExport(kind="redirect", redirect_url=str(content.get("url", "")))

    if ctype == "diagram":
        return ArtifactExport(
            kind="file",
            filename=f"{base_name}.mmd",
            content_type="text/plain; charset=utf-8",
            body=str(content.get("source", "")).encode("utf-8"),
        )

    if ctype == "ppt":
        if export_mode == "visual":
            return ArtifactExport(
                kind="error",
                error="Visual-priority PPTX export is not enabled yet. "
                "Use the default editable PPTX export instead.",
                status=501,
            )
        return ArtifactExport(kind="deferred", deferred_kind="ppt", base_name=base_name)

    if ctype == "project":
        return ArtifactExport(
            kind="deferred", deferred_kind="project", base_name=base_name
        )

    # fallback: raw JSON
    return ArtifactExport(
        kind="file",
        filename=f"{base_name}.json",
        content_type="application/json",
        body=json.dumps(content, indent=2, ensure_ascii=False).encode("utf-8"),
    )


def _sanitize_file_name(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"\s+", "_", name)
    return name[:60].strip()


def _iso_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
