"""update_artifact tool — incremental file updates for web_app artifacts.

Allows agents to add, update, or remove files in an existing web_app artifact
without creating a new version. This enables large web applications to be
written in chunks, avoiding the max_tokens limit on a single tool call.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.db.engine import get_local_db
from app.db.models import Artifact
from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok

_MAX_FILE_OPS = 20
_MAX_FILE_SIZE_BYTES = 100 * 1024  # 100KB per file


class _UpdateArgs(BaseModel):
    artifact_id: str = Field(alias="artifactId")
    add_files: dict[str, str] | None = Field(default=None, alias="addFiles")
    update_files: dict[str, str] | None = Field(default=None, alias="updateFiles")
    remove_files: list[str] | None = Field(default=None, alias="removeFiles")
    model_config = ConfigDict(populate_by_name=True)


_DESCRIPTION = (
    "Add, update, or remove files in an existing web_app artifact. Use this to "
    "write large web applications incrementally when a single write_artifact call "
    "would exceed the token limit. Only supports web_app type artifacts. Max 20 "
    "file operations per call, 100KB per file."
)

_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["artifactId"],
    "properties": {
        "artifactId": {
            "type": "string",
            "description": "ID of the target web_app artifact to update.",
        },
        "addFiles": {
            "type": "object",
            "description": (
                'Files to add, e.g. { "style.css": "body { margin: 0; }" }. '
                "Paths must be relative (no .. or absolute paths)."
            ),
            "additionalProperties": {"type": "string"},
        },
        "updateFiles": {
            "type": "object",
            "description": "Files to overwrite (same shape as addFiles).",
            "additionalProperties": {"type": "string"},
        },
        "removeFiles": {
            "type": "array",
            "items": {"type": "string"},
            "description": "File paths to remove from the artifact.",
        },
    },
}


def _is_unsafe_path(path: str) -> bool:
    return ".." in path.split("/") or path.startswith("/") or "\\" in path


async def _handler(args: Any, ctx: ToolContext) -> ToolResult:
    parsed = _UpdateArgs.model_validate(args)

    add_files = parsed.add_files or {}
    update_files = parsed.update_files or {}
    remove_files = parsed.remove_files or []

    total_ops = len(add_files) + len(update_files) + len(remove_files)
    if total_ops == 0:
        return err("No file operations specified (addFiles, updateFiles, or removeFiles).")
    if total_ops > _MAX_FILE_OPS:
        return err(f"Too many file operations (max {_MAX_FILE_OPS} per call)")

    for name, body in {**add_files, **update_files}.items():
        if _is_unsafe_path(name):
            return err(f"Unsafe file path (contains .. or absolute path): {name}")
        if len(body.encode("utf-8")) > _MAX_FILE_SIZE_BYTES:
            return err(
                f"File '{name}' exceeds {_MAX_FILE_SIZE_BYTES} bytes "
                f"(got {len(body.encode('utf-8'))} bytes)"
            )
    for name in remove_files:
        if _is_unsafe_path(name):
            return err(f"Unsafe file path (contains .. or absolute path): {name}")

    async with get_local_db() as db:
        result = await db.execute(
            select(Artifact).where(Artifact.id == parsed.artifact_id)
        )
        artifact = result.scalar_one_or_none()
        if artifact is None:
            return err(f"Artifact not found: {parsed.artifact_id}")
        if artifact.type != "web_app":
            return err("update_artifact only supports web_app type")

        content = dict(artifact.content_dict) if isinstance(artifact.content_dict, dict) else {}
        if not isinstance(content, dict):
            return err("Artifact content is not a valid web_app structure")
        files = dict(content.get("files")) if isinstance(content.get("files"), dict) else {}
        content["files"] = files

        updated: list[str] = []
        for name, body in {**add_files, **update_files}.items():
            files[name] = body
            updated.append(name)
        for name in remove_files:
            if name in files:
                del files[name]
                updated.append(name)

        from sqlalchemy.orm.attributes import flag_modified

        artifact.content_dict = content
        flag_modified(artifact, "content")

    return ok({
        "artifactId": parsed.artifact_id,
        "updatedFiles": updated,
    })


update_artifact_tool = ToolDef(
    name="update_artifact",
    description=_DESCRIPTION,
    parameters=_PARAMETERS,
    handler=_handler,
)
