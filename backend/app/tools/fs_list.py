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
            "description": "目录路径，省略或传 \"\" 表示 workspace 根目录。",
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
        "列出 workspace 内单个目录的文件和子目录，path 省略时默认为 workspace 根目录。"
        "适合查看某个具体目录的内容。"
        "需要一次性获取整个项目的文件清单时，用 fs_glob 更高效。"
    ),
    parameters=_PARAMETERS,
    handler=_handler,
)
