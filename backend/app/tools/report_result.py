"""report_result terminal tool — subagent structured result submission.

When a subagent calls this tool, the ReAct loop terminates immediately
(see TERMINAL_TOOLS in agent_runner.py). The structured payload is cached
in-process and later extracted by spawn_subagent_loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.tools.base import ToolContext, ToolDef, ToolResult, ok


@dataclass
class ReportResultPayload:
    """Structured result submitted by a subagent via report_result tool."""

    summary: str
    key_decisions: list[str] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)


_report_result_cache: dict[str, ReportResultPayload] = {}


_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["summary"],
    "properties": {
        "summary": {
            "type": "string",
            "description": (
                "任务完成的摘要。面向下游 Agent 或主 Agent，"
                "需自包含关键结论和产出说明。控制在 500 token 以内。"
            ),
        },
        "keyDecisions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "关键决策或发现列表（可选）。",
        },
        "filesChanged": {
            "type": "array",
            "items": {"type": "string"},
            "description": "新增或修改的文件路径列表（可选）。",
        },
        "artifacts": {
            "type": "array",
            "items": {"type": "string"},
            "description": "产出的 artifact ID 列表（可选）。",
        },
    },
}


async def _handler(args: Any, ctx: ToolContext) -> ToolResult:
    summary = args.get("summary", "") if isinstance(args, dict) else ""
    if not summary:
        return ok({"status": "reported", "warning": "empty summary"})

    payload = ReportResultPayload(
        summary=summary,
        key_decisions=args.get("keyDecisions", []) if isinstance(args, dict) else [],
        files_changed=args.get("filesChanged", []) if isinstance(args, dict) else [],
        artifacts=args.get("artifacts", []) if isinstance(args, dict) else [],
    )
    _report_result_cache[ctx.run_id] = payload
    return ok({"status": "reported"})


report_result_tool = ToolDef(
    name="report_result",
    description=(
        "Submit a structured task completion report. This is a terminal tool — "
        "after calling it, the system will end your execution immediately. "
        "Call this as the LAST tool in your final turn, after all other work "
        "(fs_write, bash, etc.) is done. Do NOT call other tools in the same "
        "turn as report_result."
    ),
    parameters=_PARAMETERS,
    handler=_handler,
)
