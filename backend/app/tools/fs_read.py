"""fs_read tool — read a workspace text file.

Port of src/server/tools/fs-read.ts. Path may be relative (to the workspace
root) or absolute, but must resolve inside the workspace.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.services.fs_service import (
    get_workspace_for_conversation,
    read_file_in_workspace,
)
from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok


class _Args(BaseModel):
    path: str = Field(min_length=1)


_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["path"],
    "properties": {
        "path": {
            "type": "string",
            "description": "File path. Relative paths resolve from the workspace root.",
        },
    },
}


async def _handler(args: Any, ctx: ToolContext) -> ToolResult:
    try:
        parsed = _Args.model_validate(args)
    except ValidationError as e:
        return err(f"Invalid args: {e}")

    workspace = await get_workspace_for_conversation(ctx.conversation_id)
    if workspace is None:
        return err("Workspace not found")

    try:
        result = read_file_in_workspace(workspace, parsed.path)
    except (ValueError, OSError) as e:
        return err(str(e))
    return ok(
        {
            "path": result.path,
            "absolutePath": result.absolute_path,
            "cwd": result.cwd,
            "size": result.size,
            "content": result.content,
            "truncated": result.truncated,
        }
    )


fs_read_tool = ToolDef(
    name="fs_read",
    description=(
        "Read a text file from the workspace. Path can be relative (to the workspace "
        "root) or absolute (must still resolve inside the workspace). Returns UTF-8 "
        "contents truncated to 50,000 characters. Use this to inspect source code, "
        "configs, etc."
    ),
    parameters=_PARAMETERS,
    handler=_handler,
)
