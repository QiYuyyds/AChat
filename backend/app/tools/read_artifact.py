"""read_artifact tool.

Port of src/server/tools/read-artifact.ts. Returns the full content of an
artifact in the current conversation.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from app.db.engine import get_db
from app.db.models import Artifact
from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok


class _Args(BaseModel):
    artifact_id: str = Field(alias="artifactId", min_length=1)

    model_config = {"populate_by_name": True}


_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["artifactId"],
    "properties": {
        "artifactId": {"type": "string", "description": "Id of the artifact, format art_xxx"},
    },
}


async def _handler(args: Any, ctx: ToolContext) -> ToolResult:
    try:
        parsed = _Args.model_validate(args)
    except ValidationError as e:
        return err(f"Invalid args: {e}")

    async with get_db() as db:
        result = await db.execute(
            select(Artifact).where(
                Artifact.id == parsed.artifact_id,
                Artifact.conversation_id == ctx.conversation_id,
            )
        )
        artifact = result.scalar_one_or_none()
    if artifact is None:
        return err(f"Artifact not found: {parsed.artifact_id}")

    return ok(
        {
            "id": artifact.id,
            "type": artifact.type,
            "title": artifact.title,
            "content": artifact.content_dict,
            "version": artifact.version,
        }
    )


read_artifact_tool = ToolDef(
    name="read_artifact",
    description=(
        "Read full content of an existing artifact in the current conversation. Use "
        "when you need the actual body of an artifact referenced by id."
    ),
    parameters=_PARAMETERS,
    handler=_handler,
)
