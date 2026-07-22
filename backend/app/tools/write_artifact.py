"""write_artifact tool.

Port of src/server/tools/write-artifact.ts. Creates an artifact, or a new
version of an existing one (version auto-increments, parentArtifactId links the
chain). Writes the DB row only and returns the artifactId; the adapter emits
``artifact.create`` after the tool result and AgentRunner injects the
``artifact_ref`` part — keeping the event stream's single source (the adapter).
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from app.db.engine import get_local_db
from app.db.models import Artifact
from app.services.artifact_service import (
    build_artifact_content,
    describe_artifact_content_error,
)
from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok
from app.utils.clock import now_ms
from app.utils.ids import new_artifact_id

_WRITABLE_TYPES = {"web_app", "document", "image", "ppt", "diagram"}

_TYPE_FORMAT_EXAMPLES: dict[str, str] = {
    "web_app": '{ "files": { "index.html": "<html>...</html>" }, "entry": "index.html" }',
    "document": '{ "format": "markdown", "content": "# Title\n\nText..." }',
    "image": '{ "url": "https://...", "alt": "description" }',
    "ppt": '{ "slides": [{ "title": "S1", "bullets": ["point"] }] }',
    "diagram": '{ "syntax": "mermaid", "source": "flowchart TD\nA[Start] --> B[End]" }',
}


def _format_error(artifact_type: str, detail: str, raw_content: Any) -> str:
    if artifact_type in _TYPE_FORMAT_EXAMPLES:
        example = _TYPE_FORMAT_EXAMPLES[artifact_type]
    else:
        all_examples = "\n".join(
            f"  {t}: {ex}" for t, ex in _TYPE_FORMAT_EXAMPLES.items()
        )
        example = f"(choose one of the following types)\n{all_examples}"
    preview = json.dumps(raw_content, ensure_ascii=False)[:200] if raw_content is not None else "(none)"
    return (
        f"Invalid content for type '{artifact_type}'.\n"
        f"Detail: {detail}\n"
        f"Expected format: {example}\n"
        f"Received (first 200 chars): {preview}\n"
        "Tip: Pass content as a JSON object, not a stringified JSON string."
    )


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
    "Pass as a JSON OBJECT, do NOT JSON-stringify it. Formats per type:\n"
    'web_app: { files: { "index.html": "..." }, entry: "index.html" }\n'
    'document: { content: "# markdown..." }\n'
    'image: { url: "...", alt: "..." }\n'
    'diagram: { source: "flowchart TD\\nA[\\"label\\"] --> B" }\n'
    'ppt: { slides: [{ title: "...", bullets: ["..."] }] }\n'
    "Common mistake: sending content as a stringified JSON string — send the raw object."
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
        artifact_type = args.get("type", "") if isinstance(args, dict) else ""
        missing = [field for field in ("type", "title", "content") if field not in args]
        if missing:
            detail = f"Required fields missing: {', '.join(missing)}."
            if "type" in missing:
                detail += (" type must be one of: "
                           ", ".join(sorted(_WRITABLE_TYPES)) + ".")
        else:
            detail = str(e)
        return err(_format_error(
            artifact_type or "(missing)",
            detail,
            args,
        ))
    if parsed.type not in _WRITABLE_TYPES:
        return err(f"Invalid args: unsupported type {parsed.type!r}")

    full_content = build_artifact_content(parsed.type, parsed.content)
    if not full_content:
        return err(
            _format_error(
                parsed.type,
                describe_artifact_content_error(parsed.type, parsed.content)
                or f"Invalid content for type {parsed.type}",
                parsed.content,
            )
        )

    version = 1
    resolved_parent: str | None = None

    async with get_local_db() as db:
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
