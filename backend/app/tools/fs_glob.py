"""fs_glob tool — recursive pattern matching inside the workspace.

Uses ``pathlib.Path.glob()`` to support ``**/*.ext`` recursive globs cross-
platform (no binary dependency like ``find`` / ``rg``). Guards against symlink
cycles via realpath deduplication (same strategy as ``_scan_workspace_usage``)
and caps results at 200 to protect the agent context.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.services.fs_service import get_workspace_for_conversation
from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok
from app.utils.workspace_utils import assert_path_within_workspace, get_effective_cwd

MAX_GLOB_RESULTS = 200


class _Args(BaseModel):
    pattern: str = Field(min_length=1)
    path: str = ""


_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["pattern"],
    "properties": {
        "pattern": {
            "type": "string",
            "description": "Glob 模式，如 '**/*.tsx' 匹配所有 TypeScript 文件，'src/**/*.py' 匹配 src 下的 Python 文件。",
        },
        "path": {
            "type": "string",
            "description": "可选的搜索子目录（相对于 workspace 根目录），省略则搜索整个 workspace。",
        },
    },
}


_DESCRIPTION = (
    "按 glob 模式批量查找文件（如 **/*.py、src/**/*.tsx）。"
    "一次调用最多返回 200 个匹配结果，包含路径、是否目录、文件大小；"
    "超过 200 个时 truncated 标志为 true。"
    "需要一次性获取整个项目的文件清单、或按扩展名/路径模式筛选文件时使用，"
    "比反复 fs_list 递归高效得多。"
)


def _glob_with_symlink_guard(root: Path, pattern: str) -> list[Path]:
    """Walk pathlib glob results, deduping by realpath to avoid symlink cycles.

    ``Path.glob('**/...')`` follows symlinks by default; a cycle would loop
    forever. We track visited realpaths and skip entries we've already seen.
    """
    visited: set[str] = set()
    results: list[Path] = []
    for entry in root.glob(pattern):
        try:
            real = os.path.realpath(str(entry))
        except OSError:
            continue
        if real in visited:
            continue
        visited.add(real)
        results.append(entry)
    return results


async def _handler(args: Any, ctx: ToolContext) -> ToolResult:
    try:
        parsed = _Args.model_validate(args or {})
    except ValidationError as e:
        return err(f"Invalid args: {e}")

    workspace = await get_workspace_for_conversation(ctx.conversation_id)
    if workspace is None:
        return err("Workspace not found")

    # Resolve the search root: workspace cwd by default, or a subpath.
    try:
        search_root = (
            get_effective_cwd(workspace)
            if parsed.path == ""
            else assert_path_within_workspace(workspace, parsed.path)
        )
    except ValueError as e:
        return err(str(e))

    if not os.path.isdir(search_root):
        return err(f"Not a directory: {parsed.path or '(root)'}")

    try:
        entries = _glob_with_symlink_guard(Path(search_root), parsed.pattern)
    except (OSError, ValueError) as e:
        return err(str(e))

    cwd = get_effective_cwd(workspace)
    truncated = len(entries) > MAX_GLOB_RESULTS
    files: list[dict[str, Any]] = []
    for entry in entries[:MAX_GLOB_RESULTS]:
        try:
            abs_path = str(entry)
            # Return paths relative to the workspace cwd for consistency with
            # other fs tools; fall back to absolute if somehow outside.
            try:
                rel = os.path.relpath(abs_path, cwd)
            except ValueError:
                rel = abs_path
            is_dir = entry.is_dir()
            size = entry.stat().st_size if entry.is_file() else None
            files.append({"path": rel, "is_directory": is_dir, "size": size})
        except OSError:
            continue

    return ok({"files": files, "truncated": truncated})


fs_glob_tool = ToolDef(
    name="fs_glob",
    description=_DESCRIPTION,
    parameters=_PARAMETERS,
    handler=_handler,
)
