"""fs_read tool — read a workspace text file.

Port of src/server/tools/fs-read.ts. Path may be relative (to the workspace
root) or absolute, but must resolve inside the workspace. Supports optional
offset/limit for line-based pagination of large files.
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
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=0, ge=0)


_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["path"],
    "properties": {
        "path": {
            "type": "string",
            "description": "文件路径，相对于 workspace 根目录或绝对路径（必须在 workspace 内）。",
        },
        "offset": {
            "type": "integer",
            "description": (
                "从第几行开始读取（1-based），默认 0 表示从头读取。"
                "大文件截断后用此参数继续读取后续内容。"
            ),
            "default": 0,
        },
        "limit": {
            "type": "integer",
            "description": "最多读取的行数，默认 0 表示不限制（受 50k 字符上限约束）。",
            "default": 0,
        },
    },
}


_DESCRIPTION = (
    "读取 workspace 内的文本文件内容。返回 UTF-8 内容，超过 50,000 字符时截断。"
    "适合在已通过 fs_glob / fs_grep 定位到目标文件后读取其完整内容。"
    "大文件可用 offset 和 limit 参数分段读取：offset 指定起始行（1-based），"
    "limit 指定最多读取行数。响应包含 startLine、endLine、totalLines 便于翻页。"
)


async def _handler(args: Any, ctx: ToolContext) -> ToolResult:
    try:
        parsed = _Args.model_validate(args)
    except ValidationError as e:
        return err(f"Invalid args: {e}")

    workspace = await get_workspace_for_conversation(ctx.conversation_id)
    if workspace is None:
        return err("Workspace not found")

    try:
        result = read_file_in_workspace(
            workspace, parsed.path, offset=parsed.offset, limit=parsed.limit
        )
    except (ValueError, OSError) as e:
        return err(str(e))

    response: dict[str, Any] = {
        "path": result.path,
        "absolutePath": result.absolute_path,
        "cwd": result.cwd,
        "size": result.size,
        "content": result.content,
        "truncated": result.truncated,
    }
    if result.start_line > 0 or result.total_lines > 0:
        response["startLine"] = result.start_line
        response["endLine"] = result.end_line
        response["totalLines"] = result.total_lines
    return ok(response)


fs_read_tool = ToolDef(
    name="fs_read",
    description=_DESCRIPTION,
    parameters=_PARAMETERS,
    handler=_handler,
)
