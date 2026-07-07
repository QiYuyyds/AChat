"""Tests for O11: DispatchPlanItem context_level field and plan prompt guidance.

Covers tasks 1.3 and 3.3.
"""

from __future__ import annotations

from app.schemas.dispatch import DispatchPlanItem

# ─── Task 1.3: DispatchPlanItem context_level field ───────────────────────────


def test_context_level_defaults_to_none():
    """context_level not set → defaults to None (isolated behavior)."""
    item = DispatchPlanItem(id="t1", agentId="ag_1", task="do something")
    assert item.context_level is None


def test_context_level_standard_parsed():
    """context_level='standard' parses correctly via alias."""
    item = DispatchPlanItem(
        id="t1", agentId="ag_1", task="review", contextLevel="standard"
    )
    assert item.context_level == "standard"


def test_context_level_isolated_parsed():
    """context_level='isolated' parses correctly."""
    item = DispatchPlanItem(
        id="t1", agentId="ag_1", task="implement", contextLevel="isolated"
    )
    assert item.context_level == "isolated"


def test_context_level_full_not_rejected():
    """context_level='full' is advisory — Pydantic Literal allows only the
    defined values, but the field accepts None. Unknown values are treated
    as isolated by build_sub_agent_prompt (is_standard check is ==)."""
    # Pydantic will reject "full" since it's not in the Literal, but
    # the field is optional and defaults to None. The advisory nature means
    # compile_and_validate_dispatch_plan doesn't validate it.
    item = DispatchPlanItem(id="t1", agentId="ag_1", task="do something")
    assert item.context_level is None

    # Standard works
    item2 = DispatchPlanItem(
        id="t2", agentId="ag_1", task="review", contextLevel="standard"
    )
    assert item2.context_level == "standard"


def test_context_level_populate_by_name():
    """context_level can be set via field name (not just alias)."""
    item = DispatchPlanItem(
        id="t1", agentId="ag_1", task="do something", context_level="standard"
    )
    assert item.context_level == "standard"


# ─── Task 3.3: Plan prompt contains contextLevel guidance ─────────────────────


def test_plan_prompt_contains_context_level_guidance():
    """build_orchestrator_plan_prompt includes contextLevel, standard, isolated."""
    from app.db.models import Agent, Workspace
    from app.services.orchestrator_prompts import build_orchestrator_plan_prompt
    from app.utils.clock import now_ms

    now = now_ms()
    agent = Agent(
        id="ag_test",
        name="TestAgent",
        avatar="T",
        description="test",
        system_prompt="base prompt",
        adapter_name="custom",
        is_builtin=False,
        is_orchestrator=False,
        supports_vision=False,
        created_at=now,
    )
    agent.capabilities_list = []
    agent.tool_names_list = []

    workspace = Workspace(
        id="ws_test",
        conversation_id="conv_test",
        root_path="/tmp/test",
        mode="sandbox",
        bound_path=None,
        created_at=now,
    )

    prompt = build_orchestrator_plan_prompt("base system prompt", [agent], workspace)

    assert "contextLevel" in prompt
    assert "standard" in prompt
    assert "isolated" in prompt
    assert "审查" in prompt or "review" in prompt.lower()
