"""fs_list tool — list a workspace directory.

Port of src/server/tools/fs-list.ts. Path defaults to the workspace root.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ValidationError

from app.services.fs_service import get_workspace_for_conversation, list_dir_in_workspace
from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok


class _Args(BaseModel):
    path: str = ""


_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": 'Directory path. Omit or pass "" for the workspace root.',
        },
    },
}


async def _handler(args: Any, ctx: ToolContext) -> ToolResult:
    try:
        parsed = _Args.model_validate(args or {})
    except ValidationError as e:
        return err(f"Invalid args: {e}")

    workspace = await get_workspace_for_conversation(ctx.conversation_id)
    if workspace is None:
        return err("Workspace not found")

    try:
        result = list_dir_in_workspace(workspace, parsed.path)
    except (ValueError, OSError) as e:
        return err(str(e))

    entries = []
    for entry in result.entries:
        item: dict[str, Any] = {"name": entry.name, "isDirectory": entry.is_directory}
        if entry.size is not None:
            item["size"] = entry.size
        entries.append(item)

    return ok(
        {
            "relPath": result.rel_path,
            "absolutePath": result.absolute_path,
            "parent": result.parent,
            "entries": entries,
        }
    )


fs_list_tool = ToolDef(
    name="fs_list",
    description=(
        "List files and directories inside the workspace. Path defaults to the "
        "workspace root. Use this before fs_read when exploring a project; it avoids "
        "shell-specific listing mistakes."
    ),
    parameters=_PARAMETERS,
    handler=_handler,
)
