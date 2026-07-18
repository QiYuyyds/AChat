"""Bounded source-graph exploration for ready local Workspaces."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.code_intelligence import service as code_service
from app.code_intelligence.metadata import MetadataStore
from app.services.fs_service import get_workspace_for_conversation
from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok

MAX_OUTPUT_CHARS = 30_000


class _Args(BaseModel):
    query: str = Field(min_length=1, max_length=2000)

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("query must not be blank")
        return stripped


_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["query"],
    "properties": {
        "query": {
            "type": "string",
            "minLength": 1,
            "maxLength": 2000,
            "description": "关于代码结构的问题，如“项目入口在哪”“X 的调用链是什么”“修改 Y 会影响哪些文件”。",
        }
    },
}


async def _handler(args: Any, ctx: ToolContext) -> ToolResult:
    try:
        parsed = _Args.model_validate(args)
    except ValidationError as exc:
        return err(f"Invalid args: {exc}")

    workspace = await get_workspace_for_conversation(ctx.conversation_id)
    if workspace is None:
        return _fallback("Workspace not found")
    if workspace.mode != "local" or not workspace.bound_path:
        return _fallback("Source intelligence supports local Workspaces only")

    metadata = MetadataStore(workspace.root_path).read()
    if not metadata.enabled or metadata.status != "ready":
        return _fallback(
            f"Source intelligence is unavailable (state: {metadata.status})"
        )
    if ctx.cancel_event.is_set():
        return _fallback("Source exploration was cancelled")

    try:
        output = await code_service.get_code_intelligence_service().explore(
            workspace_root=Path(workspace.root_path),
            project_path=Path(workspace.bound_path).resolve(),
            query=parsed.query,
            cancel_event=ctx.cancel_event,
        )
    except Exception as exc:  # noqa: BLE001 - tool failures stay non-fatal
        return _fallback(f"Source exploration failed: {exc}")

    truncated = len(output) > MAX_OUTPUT_CHARS
    context = output[:MAX_OUTPUT_CHARS]
    if truncated:
        context += "\n\n[TRUNCATED: refine the query for a narrower result]"
    return ok({"query": parsed.query, "context": context, "truncated": truncated})


def _fallback(reason: str) -> ToolResult:
    return err(f"{reason}. Fall back to the available file search/read tools.")


code_explore_tool = ToolDef(
    name="code_explore",
    description=(
        "基于代码图谱回答结构性问题：项目入口、调用链、模块依赖、修改影响范围。"
        "适合“主要流程是什么”“X 从哪里被调用”这类高层次问题，"
        "返回结构化分析，比逐个读文件再自己总结高效得多。"
        "仅适用于本地模式 workspace 且代码图谱已就绪；不可用时改用 fs_glob / fs_grep。"
    ),
    parameters=_PARAMETERS,
    handler=_handler,
)
