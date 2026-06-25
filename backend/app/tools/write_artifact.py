"""write_artifact tool.

Port of src/server/tools/write-artifact.ts. Creates an artifact, or a new
version of an existing one (version auto-increments, parentArtifactId links the
chain). Writes the DB row only and returns the artifactId; the adapter emits
``artifact.create`` after the tool result and AgentRunner injects the
``artifact_ref`` part — keeping the event stream's single source (the adapter).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from app.db.engine import get_db
from app.db.models import Artifact
from app.services.artifact_service import (
    build_artifact_content,
    describe_artifact_content_error,
)
from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok
from app.utils.clock import now_ms
from app.utils.ids import new_artifact_id

_WRITABLE_TYPES = {"web_app", "document", "image", "ppt", "diagram"}


class _Args(BaseModel):
    type: str
    title: str = Field(min_length=1)
    content: Any
    output_key: str | None = Field(default=None, alias="outputKey", min_length=1)
    parent_artifact_id: str | None = Field(default=None, alias="parentArtifactId")
    model_config = ConfigDict(populate_by_name=True)


_DESCRIPTION = (
    "Create a new artifact, or a new version of an existing one. Never call with "
    "empty args: type, title, and content are required in the same tool call. "
    "Pass parentArtifactId to create a version that links to the prior; version "
    "auto-increments. Use this to produce code/web/docs/images/PPT decks/diagrams "
    "that the user can preview."
)

_CONTENT_DESCRIPTION = (
    "Artifact body — pass as a JSON OBJECT, do NOT JSON-stringify it into a quoted "
    'string. For web_app: { files: { "index.html": "...", "style.css"?, '
    '"script.js"? }, entry: "index.html" }. For document: { format: "markdown", '
    'content: "...markdown text..." }. For image: { url: "...", alt: "..." }. For '
    'diagram: { syntax: "mermaid", source: "flowchart TD\\nA[\\"中文 / formula '
    'O(N^2)\\"] --> B[\\"结果\\"]", theme?: "default"|"base"|"dark"|"forest"|'
    '"neutral" }. Diagram source is preflighted: quote labels with Chinese/math/'
    'symbols as A["..."], use one edge per line, omit ```mermaid fences, and if '
    "the tool returns Invalid Mermaid diagram, fix source and call again. For ppt: "
    "{ title?, theme?: { primary?, background?, surface?, textBody?, textMuted?, "
    "accentPositive?, accentNegative?, divider?, fontHeading?, fontBody? }, slides: "
    "[{ title?, subtitle?, layout?, blocks?: [...] , notes? }] }. Legacy slides "
    "with bullets are still accepted, but prefer blocks for polished decks. Hex "
    'colors have no "#"; ppt JSON must not embed raw base64/data URI assets. Common '
    'mistake to avoid: sending content as a string like "{\\"format\\":\\"markdown'
    '\\",...}" — send the raw object, not its JSON text.'
)

_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["type", "title", "content"],
    "properties": {
        "type": {
            "type": "string",
            "enum": ["web_app", "document", "image", "ppt", "diagram"],
            "description": (
                "web_app for HTML/CSS/JS bundles, document for markdown text, image "
                "for URL or data URI, ppt for slide decks (structured JSON, "
                "exportable to a real .pptx), diagram for Mermaid diagrams"
            ),
        },
        "title": {"type": "string", "description": "Short human-readable title"},
        "content": {"type": "object", "description": _CONTENT_DESCRIPTION},
        "parentArtifactId": {
            "type": "string",
            "description": (
                "Optional: id of an existing artifact to base a new version on. When "
                "provided, the new row links to it and version increments from the "
                "parent."
            ),
        },
        "outputKey": {
            "type": "string",
            "description": (
                "Optional Orchestrator handoff key. When your task declares "
                "expectedOutputs, pass the matching expectedOutputs.id so downstream "
                "tasks can consume this artifact reliably."
            ),
        },
    },
}


async def _handler(args: Any, ctx: ToolContext) -> ToolResult:
    try:
        parsed = _Args.model_validate(args)
    except ValidationError as e:
        return err(f"Invalid args: {e}")
    if parsed.type not in _WRITABLE_TYPES:
        return err(f"Invalid args: unsupported type {parsed.type!r}")

    full_content = build_artifact_content(parsed.type, parsed.content)
    if not full_content:
        return err(
            describe_artifact_content_error(parsed.type, parsed.content)
            or f"Invalid content for type {parsed.type}"
        )

    version = 1
    resolved_parent: str | None = None

    async with get_db() as db:
        if parsed.parent_artifact_id:
            result = await db.execute(
                select(Artifact).where(Artifact.id == parsed.parent_artifact_id)
            )
            parent = result.scalar_one_or_none()
            if parent is None:
                return err(f"parentArtifactId not found: {parsed.parent_artifact_id}")
            if parent.conversation_id != ctx.conversation_id:
                return err("parentArtifactId belongs to a different conversation")
            version = parent.version + 1
            resolved_parent = parent.id

        artifact_id = new_artifact_id()
        created_at = now_ms()
        artifact = Artifact(
            id=artifact_id,
            conversation_id=ctx.conversation_id,
            type=parsed.type,
            title=parsed.title,
            version=version,
            parent_artifact_id=resolved_parent,
            created_by_agent_id=ctx.agent_id,
            created_at=created_at,
        )
        artifact.content_dict = full_content
        db.add(artifact)

    value: dict[str, Any] = {
        "artifactId": artifact_id,
        "title": parsed.title,
        "type": parsed.type,
        "version": version,
        "parentArtifactId": resolved_parent,
    }
    if parsed.output_key:
        value["outputKey"] = parsed.output_key
    return ok(value)


write_artifact_tool = ToolDef(
    name="write_artifact",
    description=_DESCRIPTION,
    parameters=_PARAMETERS,
    handler=_handler,
)
