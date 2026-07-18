"""fs_list tool — list a workspace directory.

Port of src/server/tools/fs-list.ts. Path defaults to the workspace root.
Supports optional ``depth`` for recursive listing and ``showHidden`` for
dotfile visibility.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.services.fs_service import (
    get_workspace_for_conversation,
    list_dir_in_workspace,
    list_dir_recursive,
)
from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok


class _Args(BaseModel):
    path: str = ""
    depth: int = Field(default=1, ge=1, le=5)
    showHidden: bool = False


_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "目录路径，省略或传 \"\" 表示 workspace 根目录。",
        },
        "depth": {
            "type": "integer",
            "description": (
                "递归展开子目录的深度（1–5，默认 1）。"
                "depth=1 只列出当前目录（保持原有行为）；"
                "depth>1 递归展开子目录，返回扁平列表，每个 entry 携带 relativePath 和 depth 字段。"
                "分析项目结构时建议用 depth=3 获取整体概览。"
                "递归时自动跳过 node_modules / .git / dist 等依赖目录。"
            ),
            "default": 1,
            "minimum": 1,
            "maximum": 5,
        },
        "showHidden": {
            "type": "boolean",
            "description": (
                "是否显示隐藏文件（以 . 开头的文件/目录），默认 false。"
                "需要查看 .env.example / .eslintrc 等配置文件时设为 true。"
            ),
            "default": False,
        },
    },
}


_DESCRIPTION = (
    "列出 workspace 内目录的文件和子目录。"
    "path 省略时默认为 workspace 根目录。\n"
    "depth 参数控制递归深度（1–5，默认 1）：depth=1 只列出当前目录内容；"
    "depth>1 递归展开子目录返回扁平列表（每个 entry 含 relativePath 和 depth），"
    "自动跳过 node_modules / .git / dist 等依赖目录，entry 上限 500。\n"
    "分析项目结构时建议用 depth=3 一次性获取整体概览，避免逐目录遍历。\n"
    "showHidden=true 可显示 .env.example / .eslintrc 等隐藏配置文件。"
)


async def _handler(args: Any, ctx: ToolContext) -> ToolResult:
    try:
        parsed = _Args.model_validate(args or {})
    except ValidationError as e:
        return err(f"Invalid args: {e}")

    workspace = await get_workspace_for_conversation(ctx.conversation_id)
    if workspace is None:
        return err("Workspace not found")

    if parsed.depth > 1:
        try:
            result = list_dir_recursive(
                workspace,
                parsed.path,
                depth=parsed.depth,
                show_hidden=parsed.showHidden,
            )
        except (ValueError, OSError) as e:
            return err(str(e))

        entries = []
        for entry in result.entries:
            item: dict[str, Any] = {
                "name": entry.name,
                "isDirectory": entry.is_directory,
                "relativePath": entry.relative_path,
                "depth": entry.depth,
            }
            if entry.size is not None:
                item["size"] = entry.size
            entries.append(item)

        return ok(
            {
                "relPath": result.rel_path,
                "absolutePath": result.absolute_path,
                "entries": entries,
                "truncated": result.truncated,
            }
        )

    try:
        result = list_dir_in_workspace(
            workspace, parsed.path, show_hidden=parsed.showHidden
        )
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
    description=_DESCRIPTION,
    parameters=_PARAMETERS,
    handler=_handler,
)
