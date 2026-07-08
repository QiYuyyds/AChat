"""Unit tests for P0 orchestration optimizations (O4, O10, O12).

O4: Plan stage Explore — complexity/explored fields, prompt content
O10: DAG dynamic priority — _task_priority sorting
O12: Aggregate deep analysis — prompt content
"""

from types import SimpleNamespace

from app.schemas.dispatch import DispatchPlanItem
from app.services.orchestrator import _task_priority
from app.services.orchestrator_prompts import (
    build_aggregate_prompt,
    build_orchestrator_aggregate_prompt,
    build_orchestrator_plan_prompt,
)


def _make_workspace():
    return SimpleNamespace(mode="sandbox", root_path="/tmp/ws", bound_path=None)

# ─── O4: Plan stage Explore ──────────────────────────────────────────────────


def test_dispatch_plan_item_accepts_complexity_and_explored():
    """DispatchPlanItem accepts advisory complexity and explored fields."""
    item = DispatchPlanItem(
        id="t1",
        agent_id="a1",
        task="do something",
        complexity="simple",
        explored=["src/main.py", "README.md"],
    )
    assert item.complexity == "simple"
    assert item.explored == ["src/main.py", "README.md"]


def test_dispatch_plan_item_complexity_and_explored_default_none():
    """DispatchPlanItem works without advisory fields (backward compatible)."""
    item = DispatchPlanItem(id="t1", agent_id="a1", task="do something")
    assert item.complexity is None
    assert item.explored is None


def test_plan_prompt_contains_exploration_instruction():
    """Plan system prompt instructs the LLM to explore workspace before planning."""
    prompt = build_orchestrator_plan_prompt("base", [], _make_workspace())
    assert "fs_list" in prompt
    assert "fs_read" in prompt
    assert "explored" in prompt


def test_plan_prompt_contains_complexity_guidance():
    """Plan system prompt includes complexity assessment guidance."""
    prompt = build_orchestrator_plan_prompt("base", [], _make_workspace())
    assert "simple" in prompt
    assert "moderate" in prompt
    assert "complex" in prompt
    assert "complexity" in prompt


# ─── O10: DAG dynamic priority ───────────────────────────────────────────────


def test_code_task_higher_priority_than_review():
    """Code tasks have higher priority than review tasks."""
    code_task = DispatchPlanItem(id="t1", agent_id="a", task="implement", task_kind="code")
    review_task = DispatchPlanItem(id="t2", agent_id="a", task="review", task_kind="review")
    assert _task_priority(code_task) > _task_priority(review_task)


def test_doc_task_higher_than_review():
    """Doc tasks have higher priority than review tasks."""
    doc_task = DispatchPlanItem(id="t1", agent_id="a", task="write docs", task_kind="doc")
    review_task = DispatchPlanItem(id="t2", agent_id="a", task="review", task_kind="review")
    assert _task_priority(doc_task) > _task_priority(review_task)


def test_target_paths_boosts_priority():
    """Tasks with target_paths get +1 priority."""
    task_with_paths = DispatchPlanItem(
        id="t1", agent_id="a", task="implement", task_kind="code",
        target_paths=["src/main.py"],
    )
    task_without_paths = DispatchPlanItem(
        id="t2", agent_id="a", task="implement", task_kind="code",
    )
    assert _task_priority(task_with_paths) == _task_priority(task_without_paths) + 1


def test_same_weight_sort_is_stable():
    """Sorting preserves relative order for same-priority tasks."""
    t1 = DispatchPlanItem(id="t1", agent_id="a", task="review1", task_kind="review")
    t2 = DispatchPlanItem(id="t2", agent_id="a", task="review2", task_kind="review")
    t3 = DispatchPlanItem(id="t3", agent_id="a", task="review3", task_kind="review")
    ready = [t1, t2, t3]
    ready.sort(key=lambda t: -_task_priority(t))
    # All have same priority, stable sort preserves original order
    assert [t.id for t in ready] == ["t1", "t2", "t3"]


def test_code_sorted_before_review_in_mixed_list():
    """In a mixed list, code tasks come before review tasks after sorting."""
    code1 = DispatchPlanItem(id="c1", agent_id="a", task="code1", task_kind="code")
    review1 = DispatchPlanItem(id="r1", agent_id="a", task="rev1", task_kind="review")
    code2 = DispatchPlanItem(id="c2", agent_id="a", task="code2", task_kind="code")
    review2 = DispatchPlanItem(id="r2", agent_id="a", task="rev2", task_kind="review")

    ready = [review1, code1, review2, code2]
    ready.sort(key=lambda t: -_task_priority(t))

    ids = [t.id for t in ready]
    # Code tasks (priority 3) before review tasks (priority 1)
    assert ids.index("c1") < ids.index("r1")
    assert ids.index("c2") < ids.index("r2")


# ─── O12: Aggregate deep analysis ────────────────────────────────────────────


def test_aggregate_system_prompt_contains_consistency_check():
    """Aggregate system prompt includes cross-task consistency check instructions."""
    prompt = build_orchestrator_aggregate_prompt("base")
    assert "一致性" in prompt or "consistency" in prompt.lower()


def test_aggregate_system_prompt_contains_completion_scoring():
    """Aggregate system prompt includes completion scoring instructions."""
    prompt = build_orchestrator_aggregate_prompt("base")
    assert "完成度" in prompt
    assert "fully complete" in prompt
    assert "partially complete" in prompt


def test_aggregate_system_prompt_contains_next_step():
    """Aggregate system prompt includes next-step recommendation instructions."""
    prompt = build_orchestrator_aggregate_prompt("base")
    assert "下一步" in prompt
    assert "failed" in prompt or "修复" in prompt


def test_aggregate_user_prompt_contains_analysis_requirements():
    """Aggregate user prompt includes analysis requirement keywords."""
    import asyncio

    async def _run():
        prompt = await build_aggregate_prompt(
            "build a web app",
            [],
            {},
            [],
            _make_workspace(),
        )
        return prompt

    prompt = asyncio.run(_run())
    assert "一致性" in prompt or "consistency" in prompt.lower()
    assert "完成度" in prompt
    assert "下一步" in prompt
