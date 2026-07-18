"""fs_read tool — read a workspace text file.

Port of src/server/tools/fs-read.ts. Path may be relative (to the workspace
root) or absolute, but must resolve inside the workspace. Supports optional
offset/limit for line-based pagination of large files, plus ``mode`` parameter
for outline (structure-only) and head (first-N-lines) reads.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.services.fs_service import (
    detect_language,
    extract_outline,
    get_workspace_for_conversation,
    read_file_in_workspace,
)
from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok

_HEAD_DEFAULT_LINES = 50


class _Args(BaseModel):
    path: str = Field(min_length=1)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=0, ge=0)
    mode: str = Field(default="full", pattern="^(full|outline|head)$")


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
        "mode": {
            "type": "string",
            "enum": ["full", "outline", "head"],
            "description": (
                "读取模式（默认 full）：\n"
                "  full — 完整内容（≤50k chars），配合 offset/limit 分页读取大文件。\n"
                "  outline — 只返回文件结构骨架（import / class / function 签名等），"
                "用正则提取不调 LLM，token 消耗约为 full 的 1/10。"
                "适合快速了解文件结构、判断是否值得完整读取。\n"
                "  head — 只读前 N 行（limit 参数，默认 50），适合快速预览文件开头。"
            ),
            "default": "full",
        },
    },
}


_DESCRIPTION = (
    "读取 workspace 内的文本文件内容。返回 UTF-8 内容，超过 50,000 字符时截断。\n"
    "支持三种模式（mode 参数）：\n"
    "  full（默认）— 完整内容，大文件可用 offset 和 limit 分段读取。\n"
    "  outline — 只返回文件结构骨架（import / class / function 签名），"
    "正则提取不调 LLM，token 消耗约为 full 的 1/10，适合快速了解文件结构。\n"
    "  head — 只读前 N 行（limit 参数，默认 50），适合快速判断文件是否值得完整读取。\n"
    "分析项目代码时建议先用 outline 模式了解文件结构，再按需 full 读取关键文件。"
)


async def _handler(args: Any, ctx: ToolContext) -> ToolResult:
    try:
        parsed = _Args.model_validate(args)
    except ValidationError as e:
        return err(f"Invalid args: {e}")

    workspace = await get_workspace_for_conversation(ctx.conversation_id)
    if workspace is None:
        return err("Workspace not found")

    # ── outline mode ──────────────────────────────────────────────────────
    if parsed.mode == "outline":
        return _handle_outline(workspace, parsed.path)

    # ── head mode ─────────────────────────────────────────────────────────
    if parsed.mode == "head":
        head_limit = parsed.limit if parsed.limit > 0 else _HEAD_DEFAULT_LINES
        return _handle_head(workspace, parsed.path, head_limit)

    # ── full mode (default) ───────────────────────────────────────────────
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


def _handle_outline(workspace: Any, path: str) -> ToolResult:
    """Extract structural skeleton without reading full content into response."""
    try:
        result = read_file_in_workspace(workspace, path)
    except (ValueError, OSError) as e:
        return err(str(e))

    language = detect_language(path)
    outline = extract_outline(result.content, language)
    total_lines = result.content.count("\n") + (1 if result.content and not result.content.endswith("\n") else 0)
    if not result.content:
        total_lines = 0

    response: dict[str, Any] = {
        "path": result.path,
        "absolutePath": result.absolute_path,
        "mode": "outline",
        "language": language,
        "outline": outline,
        "totalLines": total_lines,
        "fullSize": result.size,
    }
    if not outline:
        response["note"] = (
            "No structural elements detected. Try mode=\"full\" to read the "
            "complete file content."
        )
    return ok(response)


def _handle_head(workspace: Any, path: str, limit: int) -> ToolResult:
    """Return the first *limit* lines of the file."""
    try:
        result = read_file_in_workspace(workspace, path, offset=0, limit=limit)
    except (ValueError, OSError) as e:
        return err(str(e))

    # read_file_in_workspace with offset=0,limit=N returns lines 1..N
    # but sets start_line=1 and end_line accordingly only when offset>0 or limit>0
    total_lines = result.total_lines
    if total_lines == 0:
        # Recompute total lines by reading the full file metadata
        full_result = read_file_in_workspace(workspace, path)
        total_lines = full_result.content.count("\n") + (
            1 if full_result.content and not full_result.content.endswith("\n") else 0
        )
        if not full_result.content:
            total_lines = 0

    end_line = min(limit, total_lines) if total_lines > 0 else limit
    truncated = total_lines > limit

    response: dict[str, Any] = {
        "path": result.path,
        "absolutePath": result.absolute_path,
        "cwd": result.cwd,
        "size": result.size,
        "content": result.content,
        "mode": "head",
        "startLine": 1,
        "endLine": end_line,
        "totalLines": total_lines,
        "truncated": truncated,
    }
    return ok(response)


fs_read_tool = ToolDef(
    name="fs_read",
    description=_DESCRIPTION,
    parameters=_PARAMETERS,
    handler=_handler,
)
