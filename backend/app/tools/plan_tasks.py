"""plan_tasks tool — Orchestrator's plan output.

Port of src/server/tools/plan-tasks.ts. Side-effect-free: it only validates and
acks the decomposed sub-task list. AgentRunner (阶段 5) sees the plan_tasks call,
enters dispatch mode, and fans the plan out into child AgentRuns.

See specs/06-orchestrator-flow.md.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.schemas.dispatch import DispatchPlanItem
from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok


class _Args(BaseModel):
    reasoning: str = Field(min_length=1)
    tasks: list[DispatchPlanItem] = Field(min_length=1)


_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["reasoning", "tasks"],
    "properties": {
        "reasoning": {
            "type": "string",
            "description": (
                "Brief explanation of why this decomposition makes sense, 3 sentences "
                "max"
            ),
        },
        "tasks": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["id", "agentId", "task"],
                "properties": {
                    "id": {"type": "string", "description": "Sub-task id, use t1/t2/t3 format"},
                    "agentId": {
                        "type": "string",
                        "description": (
                            "Agent id that should execute this task. Must come from the "
                            "available list."
                        ),
                    },
                    "task": {
                        "type": "string",
                        "description": (
                            "Concrete, self-contained instruction for that agent. The "
                            "agent will not see the full group history."
                        ),
                    },
                    "taskKind": {
                        "type": "string",
                        "enum": ["code", "test", "review", "design", "doc", "analysis"],
                        "description": (
                            "Kind of work. Use code/test/review/design/doc/analysis to "
                            "help AgentRunner apply evidence expectations."
                        ),
                    },
                    "dependsOn": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Ids of prerequisite tasks. Omit when the task can start "
                            "immediately."
                        ),
                    },
                    "expectedOutputs": {
                        "type": "array",
                        "description": (
                            "Artifacts this task must create for downstream handoff or "
                            "user inspection. Code implementation tasks should declare a "
                            "required project output. Omit for text-only work such as "
                            "review, validation, diagnosis, status check, explanation, "
                            "or summary."
                        ),
                        "items": {
                            "type": "object",
                            "required": ["id", "type"],
                            "properties": {
                                "id": {
                                    "type": "string",
                                    "description": (
                                        "Symbolic output key within this task, not an "
                                        "artifact id."
                                    ),
                                },
                                "type": {
                                    "type": "string",
                                    "enum": ["web_app", "document", "image", "ppt", "project"],
                                    "description": (
                                        "Expected artifact type. Use project for "
                                        "workspace code trees; project is created by "
                                        "AChat from fs_write evidence, not by "
                                        "write_artifact."
                                    ),
                                },
                                "required": {
                                    "type": "boolean",
                                    "description": (
                                        "Whether this handoff output is expected by the "
                                        "plan. Defaults to true. Required project "
                                        "outputs on code tasks are hard completion gates."
                                    ),
                                },
                                "description": {
                                    "type": "string",
                                    "description": (
                                        "Short description of what this output should "
                                        "contain."
                                    ),
                                },
                            },
                        },
                    },
                    "inputs": {
                        "type": "array",
                        "description": (
                            "Upstream artifacts this task must consume. AgentRunner "
                            "validates these against upstream expectedOutputs and "
                            "compiles them into dependencies."
                        ),
                        "items": {
                            "type": "object",
                            "required": ["fromTaskId", "outputId"],
                            "properties": {
                                "fromTaskId": {
                                    "type": "string",
                                    "description": (
                                        "Upstream task id that produces the artifact."
                                    ),
                                },
                                "outputId": {
                                    "type": "string",
                                    "description": "The upstream expectedOutputs.id to consume.",
                                },
                                "required": {
                                    "type": "boolean",
                                    "description": "Whether this input is required. Defaults to true.",
                                },
                                "description": {
                                    "type": "string",
                                    "description": "Why this input is needed.",
                                },
                            },
                        },
                    },
                    "acceptanceCriteria": {
                        "type": "array",
                        "description": (
                            "Concrete completion checks for this task. Use this for "
                            "text-only/review/validation tasks instead of "
                            "expectedOutputs. The child agent must report each item "
                            "through report_task_result.acceptanceResults."
                        ),
                        "items": {"type": "string"},
                    },
                    "targetPaths": {
                        "type": "array",
                        "description": (
                            "Workspace file or directory paths this task is expected to "
                            "inspect, create, or change. Use relative paths when possible."
                        ),
                        "items": {"type": "string"},
                    },
                    "expectedWorkspaceChanges": {
                        "type": "array",
                        "description": (
                            "Plain-language list of expected workspace changes. Required "
                            "for non-trivial code tasks."
                        ),
                        "items": {"type": "string"},
                    },
                    "requiredCommands": {
                        "type": "array",
                        "description": (
                            "Commands that AChat must run successfully before "
                            "accepting this task as complete, such as pnpm test or mvn "
                            "compile. Use cwd instead of cd for subdirectories."
                        ),
                        "items": {
                            "type": "object",
                            "required": ["command"],
                            "properties": {
                                "command": {
                                    "type": "string",
                                    "description": (
                                        "Exact command expected to run. Keep it focused "
                                        "on the verification step; use cwd for "
                                        "subdirectories."
                                    ),
                                },
                                "description": {
                                    "type": "string",
                                    "description": "Short reason this command verifies the task.",
                                },
                                "cwd": {
                                    "type": "string",
                                    "description": (
                                        "Optional workspace-relative directory to run the "
                                        'command in, such as "frontend" or "backend".'
                                    ),
                                },
                                "timeoutMs": {
                                    "type": "number",
                                    "description": (
                                        "Optional timeout in milliseconds. Use a larger "
                                        "value for dependency install or compilation."
                                    ),
                                },
                            },
                        },
                    },
                    "requiredEvidence": {
                        "type": "array",
                        "description": (
                            "Evidence statements the child must provide in "
                            "report_task_result before the task can complete."
                        ),
                        "items": {"type": "string"},
                    },
                },
            },
        },
    },
}


async def _handler(args: Any, ctx: ToolContext) -> ToolResult:
    try:
        parsed = _Args.model_validate(args)
    except ValidationError as e:
        return err(f"Invalid plan: {e}")
    # Actual execution happens in AgentRunner; here we only validate and ack.
    return ok({"acknowledged": True, "taskCount": len(parsed.tasks)})


plan_tasks_tool = ToolDef(
    name="plan_tasks",
    description=(
        "Decompose the user request into sub-tasks and dispatch them to other agents "
        "in this group. Output a complete plan in a single call; do NOT call this "
        "tool multiple times."
    ),
    parameters=_PARAMETERS,
    handler=_handler,
)
