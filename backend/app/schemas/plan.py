"""Pydantic schemas for execution plan.

Corresponds to PlanStep and PlanState types used by the execution_plan tool
and plan_registry.
"""

from typing import Literal

from pydantic import BaseModel, Field

PlanStepStatus = Literal["pending", "in_progress", "done", "failed", "skipped"]
PlanComplexity = Literal["simple", "moderate", "complex"]


class PlanStep(BaseModel):
    """A single step in an execution plan."""

    id: str
    title: str
    status: PlanStepStatus = "pending"

    model_config = {"populate_by_name": True}


class PlanState(BaseModel):
    """Full state of an execution plan, stored in plan_registry."""

    plan_id: str = Field(alias="planId")
    steps: list[PlanStep]
    complexity: PlanComplexity
    added_steps_count: int = 0

    model_config = {"populate_by_name": True}
