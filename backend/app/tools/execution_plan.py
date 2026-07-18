"""Execution plan tools: create_plan, plan_step, add_plan_steps.

These tools allow a solo-mode Agent to create and track a structured execution
plan. The plan is rendered as a checklist card in the chat UI, with step
status transitions visible to the user in real time.

Event flow (symmetric to artifact_ref path):
  - create_plan  → PlanCreatedEvent   → consume_stream injects execution_plan part
  - plan_step    → PlanStepUpdateEvent → consume_stream updates steps in parts_buffer
  - add_plan_steps → PlanStepUpdateEvent → consume_stream updates steps in parts_buffer
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.schemas.plan import PlanComplexity, PlanStep, PlanState
from app.services.plan_registry import plan_registry
from app.tools.base import ToolContext, ToolDef, ToolResult, err, ok
from app.utils.ids import _gen_id


# ─── create_plan ─────────────────────────────────────

class _CreatePlanStepInput(BaseModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)


class _CreatePlanArgs(BaseModel):
    steps: list[_CreatePlanStepInput] = Field(min_length=2, max_length=10)
    complexity: PlanComplexity

    model_config = ConfigDict(populate_by_name=True)


_CREATE_PLAN_DESCRIPTION = (
    "Create a structured execution plan with named steps. Use this for tasks "
    "that require 3+ steps, including exploratory tasks like analyzing a project, "
    "understanding a codebase, or generating multi-module documentation. "
    "Do NOT use for simple tasks (1-2 steps) or single-step queries. "
    "Each step needs a unique id (e.g. 's1', 's2') and a title. "
    "After creating the plan, call plan_step before starting each step's work."
)

_CREATE_PLAN_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["steps", "complexity"],
    "properties": {
        "steps": {
            "type": "array",
            "minItems": 2,
            "maxItems": 10,
            "description": "List of plan steps, each with a unique id and title.",
            "items": {
                "type": "object",
                "required": ["id", "title"],
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Unique step identifier (e.g. 's1', 's2').",
                    },
                    "title": {
                        "type": "string",
                        "description": "Short description of what this step does.",
                    },
                },
            },
        },
        "complexity": {
            "type": "string",
            "enum": ["simple", "moderate", "complex"],
            "description": "Assessed complexity of the task.",
        },
    },
}


async def _handle_create_plan(args: Any, ctx: ToolContext) -> ToolResult:
    try:
        parsed = _CreatePlanArgs.model_validate(args)
    except ValidationError as e:
        return err(f"Invalid create_plan arguments: {e}")

    # Check duplicate step IDs
    seen_ids: set[str] = set()
    for s in parsed.steps:
        if s.id in seen_ids:
            return err(f"Duplicate step ID: '{s.id}'. Step IDs must be unique.")
        seen_ids.add(s.id)

    plan_id = _gen_id("plan_", 10)
    plan_steps = [PlanStep(id=s.id, title=s.title, status="pending") for s in parsed.steps]
    plan = PlanState(planId=plan_id, steps=plan_steps, complexity=parsed.complexity)
    plan_registry.register(plan)

    return ok({
        "planId": plan_id,
        "stepCount": len(plan_steps),
        "steps": [s.model_dump(by_alias=True) for s in plan_steps],
        "complexity": parsed.complexity,
    })


create_plan_tool = ToolDef(
    name="create_plan",
    description=_CREATE_PLAN_DESCRIPTION,
    parameters=_CREATE_PLAN_PARAMETERS,
    handler=_handle_create_plan,
)


# ─── plan_step ────────────────────────────────────────

class _PlanStepArgs(BaseModel):
    plan_id: str = Field(alias="planId", min_length=1)
    step_id: str = Field(alias="stepId", min_length=1)

    model_config = ConfigDict(populate_by_name=True)


_PLAN_STEP_DESCRIPTION = (
    "Mark a plan step as in_progress. Automatically marks the previous "
    "in_progress step as done. Call this BEFORE starting actual work on each step. "
    "You can issue plan_step and actual tool calls as parallel tool calls in the same turn."
)

_PLAN_STEP_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["planId", "stepId"],
    "properties": {
        "planId": {
            "type": "string",
            "description": "The planId returned by create_plan.",
        },
        "stepId": {
            "type": "string",
            "description": "The step id to mark as in_progress.",
        },
    },
}


async def _handle_plan_step(args: Any, ctx: ToolContext) -> ToolResult:
    try:
        parsed = _PlanStepArgs.model_validate(args)
    except ValidationError as e:
        return err(f"Invalid plan_step arguments: {e}")

    plan = plan_registry.get(parsed.plan_id)
    if plan is None:
        return err(f"Plan not found: {parsed.plan_id}")

    # Find the target step
    target_step = None
    for s in plan.steps:
        if s.id == parsed.step_id:
            target_step = s
            break
    if target_step is None:
        return err(f"Step not found in plan: {parsed.step_id}")

    # Auto-mark previous in_progress step as done
    previous_step = None
    for s in plan.steps:
        if s.status == "in_progress":
            previous_step = s
            s.status = "done"

    # Mark target as in_progress
    target_step.status = "in_progress"
    plan_registry.update(plan)

    result: dict[str, Any] = {
        "planId": parsed.plan_id,
        "currentStep": target_step.model_dump(by_alias=True),
        "previousStep": previous_step.model_dump(by_alias=True) if previous_step else None,
        "updatedSteps": [s.model_dump(by_alias=True) for s in plan.steps],
    }

    # Just-in-time verification nudge: when starting the last step of a 3+ step
    # plan and no step title mentions verification, remind the agent to verify.
    # This mirrors Claude Code's TodoWriteTool verification nudge — a structural
    # prompt injected at the critical moment, not a static system-prompt rule.
    remaining_pending = [
        s for s in plan.steps
        if s.id != target_step.id and s.status != "done"
    ]
    if not remaining_pending and len(plan.steps) >= 3:
        verification_keywords = (
            "验证", "测试", "检查", "verify", "test", "check",
            "validate", "lint", "typecheck", "run ",
        )
        has_verification = any(
            any(kw in s.title.lower() for kw in verification_keywords)
            for s in plan.steps
        )
        if not has_verification:
            result["nudge"] = (
                "你的计划即将完成，但没有包含验证步骤。"
                "建议在完成最后一步后，跑一遍 typecheck / lint / tests（如果项目有的话），"
                "或用 add_plan_steps 追加一个验证步骤来确认工作成果。"
            )

    return ok(result)


plan_step_tool = ToolDef(
    name="plan_step",
    description=_PLAN_STEP_DESCRIPTION,
    parameters=_PLAN_STEP_PARAMETERS,
    handler=_handle_plan_step,
)


# ─── add_plan_steps ──────────────────────────────────

class _AddPlanStepsStepInput(BaseModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)


class _AddPlanStepsArgs(BaseModel):
    plan_id: str = Field(alias="planId", min_length=1)
    steps: list[_AddPlanStepsStepInput] = Field(min_length=1, max_length=5)

    model_config = ConfigDict(populate_by_name=True)


_ADD_PLAN_STEPS_DESCRIPTION = (
    "Append new steps to an existing execution plan. Use when you discover "
    "additional work is needed that wasn't in the original plan. "
    "New steps are appended with 'pending' status."
)

_ADD_PLAN_STEPS_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "required": ["planId", "steps"],
    "properties": {
        "planId": {
            "type": "string",
            "description": "The planId returned by create_plan.",
        },
        "steps": {
            "type": "array",
            "minItems": 1,
            "maxItems": 5,
            "description": "New steps to append to the plan.",
            "items": {
                "type": "object",
                "required": ["id", "title"],
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Unique step identifier.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Short description of what this step does.",
                    },
                },
            },
        },
    },
}


async def _handle_add_plan_steps(args: Any, ctx: ToolContext) -> ToolResult:
    try:
        parsed = _AddPlanStepsArgs.model_validate(args)
    except ValidationError as e:
        return err(f"Invalid add_plan_steps arguments: {e}")

    plan = plan_registry.get(parsed.plan_id)
    if plan is None:
        return err(f"Plan not found: {parsed.plan_id}")

    # Check total step count
    if len(plan.steps) + len(parsed.steps) > 15:
        return err(
            f"Adding {len(parsed.steps)} steps would exceed maximum of 15 "
            f"(current: {len(plan.steps)})"
        )

    # Check duplicate IDs
    existing_ids = {s.id for s in plan.steps}
    for s in parsed.steps:
        if s.id in existing_ids:
            return err(f"Duplicate step ID: '{s.id}'. Step IDs must be unique within the plan.")
        existing_ids.add(s.id)

    # Append new steps
    new_steps = [PlanStep(id=s.id, title=s.title, status="pending") for s in parsed.steps]
    plan.steps.extend(new_steps)
    plan.added_steps_count += len(new_steps)
    plan_registry.update(plan)

    return ok({
        "planId": parsed.plan_id,
        "addedCount": len(new_steps),
        "totalSteps": len(plan.steps),
        "updatedSteps": [s.model_dump(by_alias=True) for s in plan.steps],
    })


add_plan_steps_tool = ToolDef(
    name="add_plan_steps",
    description=_ADD_PLAN_STEPS_DESCRIPTION,
    parameters=_ADD_PLAN_STEPS_PARAMETERS,
    handler=_handle_add_plan_steps,
)
