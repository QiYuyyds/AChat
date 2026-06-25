"""Tests for dispatch_plan: parsing, validation, code-task detection, compilation."""

from __future__ import annotations

import pytest

from app.schemas.dispatch import DispatchExpectedOutput, DispatchPlanItem, DispatchTaskInput
from app.services.dispatch_plan import (
    CODE_TASK_RUNNABLE_ACCEPTANCE_CRITERION,
    CODE_TASK_RUNNABLE_REQUIRED_EVIDENCE,
    PLAN_TASKS_TOOL_NAME,
    assert_acyclic_dispatch_plan,
    collect_dependency_closure,
    compile_and_validate_dispatch_plan,
    compile_dispatch_plan,
    extract_plan_tasks_tool_args,
    get_required_expected_outputs,
    is_code_implementation_task,
    normalize_task_contract,
    parse_dispatch_plan_tool_args,
    task_expects_artifact,
    validate_dispatch_plan,
)

AGENTS = [{"id": "a1"}, {"id": "a2"}, {"id": "designer"}]
ORCH = "orchestrator"


def _task(**kwargs) -> DispatchPlanItem:
    base = {"id": "t1", "agent_id": "a1", "task": "do work"}
    base.update(kwargs)
    return DispatchPlanItem(**base)


# ─── parsing ──────────────────────
def test_parse_minimal_task():
    plan = parse_dispatch_plan_tool_args(
        {"tasks": [{"id": "t1", "agentId": "a1", "task": "实现登录页面"}]}
    )
    assert len(plan) == 1
    assert plan[0].id == "t1"
    assert plan[0].agent_id == "a1"
    assert plan[0].task == "实现登录页面"


def test_parse_full_task_fields():
    plan = parse_dispatch_plan_tool_args(
        {
            "tasks": [
                {
                    "id": "t1",
                    "agentId": "a1",
                    "task": "build api",
                    "taskKind": "code",
                    "dependsOn": ["t0"],
                    "expectedOutputs": [
                        {"id": "project", "type": "project", "required": True}
                    ],
                    "inputs": [{"fromTaskId": "t0", "outputId": "prd"}],
                    "acceptanceCriteria": ["builds"],
                    "targetPaths": ["src/api.ts"],
                    "expectedWorkspaceChanges": ["add api"],
                    "requiredCommands": [{"command": "pnpm build", "timeoutMs": 1000}],
                    "requiredEvidence": ["exitCode 0"],
                }
            ]
        }
    )
    t = plan[0]
    assert t.task_kind == "code"
    assert t.depends_on == ["t0"]
    assert t.expected_outputs[0].type == "project"
    assert t.inputs[0].from_task_id == "t0"
    assert t.required_commands[0].timeout_ms == 1000
    assert t.required_evidence == ["exitCode 0"]


def test_parse_rejects_missing_tasks_array():
    with pytest.raises(ValueError, match="must include a tasks array"):
        parse_dispatch_plan_tool_args({"foo": 1})


def test_parse_rejects_missing_required_field():
    with pytest.raises(ValueError, match="agentId must be a non-empty string"):
        parse_dispatch_plan_tool_args({"tasks": [{"id": "t1", "task": "x"}]})


def test_parse_rejects_bad_task_kind():
    with pytest.raises(ValueError, match="taskKind must be one of"):
        parse_dispatch_plan_tool_args(
            {"tasks": [{"id": "t1", "agentId": "a1", "task": "x", "taskKind": "bogus"}]}
        )


def test_parse_rejects_non_array_depends_on():
    with pytest.raises(ValueError, match="dependsOn must be an array"):
        parse_dispatch_plan_tool_args(
            {"tasks": [{"id": "t1", "agentId": "a1", "task": "x", "dependsOn": "t0"}]}
        )


def test_parse_rejects_bad_expected_output_type():
    with pytest.raises(ValueError, match="type must be one of"):
        parse_dispatch_plan_tool_args(
            {
                "tasks": [
                    {
                        "id": "t1",
                        "agentId": "a1",
                        "task": "x",
                        "expectedOutputs": [{"id": "o", "type": "nonsense"}],
                    }
                ]
            }
        )


def test_parse_rejects_non_positive_timeout():
    with pytest.raises(ValueError, match="timeoutMs must be a positive integer"):
        parse_dispatch_plan_tool_args(
            {
                "tasks": [
                    {
                        "id": "t1",
                        "agentId": "a1",
                        "task": "x",
                        "requiredCommands": [{"command": "c", "timeoutMs": 0}],
                    }
                ]
            }
        )


# ─── tool-name extraction ──────────────────────
def test_extract_plan_tasks_tool_args_variants():
    args = {"tasks": []}
    assert extract_plan_tasks_tool_args(PLAN_TASKS_TOOL_NAME, args) is args
    assert extract_plan_tasks_tool_args("mcp__agenthub__plan_tasks", args) is args
    assert extract_plan_tasks_tool_args("foo__plan_tasks", args) is args
    assert extract_plan_tasks_tool_args("foo_plan_tasks", args) is args
    assert extract_plan_tasks_tool_args("other_tool", args) is None


def test_extract_codex_mcp_json_string_args():
    wrapped = {"tool": "plan_tasks", "arguments": '{"tasks": [1]}'}
    out = extract_plan_tasks_tool_args("codex_mcp_agenthub_plan_tasks", wrapped)
    assert out == {"tasks": [1]}


# ─── validation ──────────────────────
def test_validate_rejects_empty_plan():
    with pytest.raises(ValueError, match="must not be empty"):
        validate_dispatch_plan([], AGENTS, ORCH)


def test_validate_rejects_duplicate_ids():
    plan = [_task(id="t1"), _task(id="t1", agent_id="a2")]
    with pytest.raises(ValueError, match="duplicate task id"):
        validate_dispatch_plan(plan, AGENTS, ORCH)


def test_validate_rejects_dispatch_to_orchestrator():
    plan = [_task(agent_id=ORCH)]
    with pytest.raises(ValueError, match="recurse"):
        validate_dispatch_plan(plan, AGENTS, ORCH)


def test_validate_rejects_unknown_agent():
    plan = [_task(agent_id="ghost")]
    with pytest.raises(ValueError, match="unavailable agentId"):
        validate_dispatch_plan(plan, AGENTS, ORCH)


def test_validate_rejects_self_dependency():
    plan = [_task(id="t1", depends_on=["t1"])]
    with pytest.raises(ValueError, match="cannot depend on itself"):
        validate_dispatch_plan(plan, AGENTS, ORCH)


def test_validate_rejects_unknown_dependency():
    plan = [_task(id="t1", depends_on=["nope"])]
    with pytest.raises(ValueError, match="depends on unknown task"):
        validate_dispatch_plan(plan, AGENTS, ORCH)


def test_validate_accepts_external_dependency():
    external = [_task(id="prev")]
    plan = [_task(id="t1", depends_on=["prev"])]
    validate_dispatch_plan(plan, AGENTS, ORCH, external)  # no raise


def test_validate_rejects_input_self_reference():
    plan = [
        _task(
            id="t1",
            inputs=[DispatchTaskInput(from_task_id="t1", output_id="o")],
        )
    ]
    with pytest.raises(ValueError, match="input cannot reference itself"):
        validate_dispatch_plan(plan, AGENTS, ORCH)


def test_validate_rejects_input_unknown_output():
    plan = [
        _task(id="t0", expected_outputs=[DispatchExpectedOutput(id="real", type="document")]),
        _task(
            id="t1",
            agent_id="a2",
            inputs=[DispatchTaskInput(from_task_id="t0", output_id="missing")],
        ),
    ]
    with pytest.raises(ValueError, match="unknown output"):
        validate_dispatch_plan(plan, AGENTS, ORCH)


def test_validate_accepts_valid_input_reference():
    plan = [
        _task(id="t0", expected_outputs=[DispatchExpectedOutput(id="prd", type="document")]),
        _task(
            id="t1",
            agent_id="a2",
            inputs=[DispatchTaskInput(from_task_id="t0", output_id="prd")],
        ),
    ]
    validate_dispatch_plan(plan, AGENTS, ORCH)  # no raise


def test_validate_rejects_duplicate_expected_output():
    plan = [
        _task(
            expected_outputs=[
                DispatchExpectedOutput(id="o", type="document"),
                DispatchExpectedOutput(id="o", type="web_app"),
            ]
        )
    ]
    with pytest.raises(ValueError, match="duplicate expected output"):
        validate_dispatch_plan(plan, AGENTS, ORCH)


# ─── cycle detection ──────────────────────
def test_assert_acyclic_detects_cycle():
    plan = [
        _task(id="t1", depends_on=["t2"]),
        _task(id="t2", agent_id="a2", depends_on=["t1"]),
    ]
    with pytest.raises(ValueError, match="circular dependency"):
        assert_acyclic_dispatch_plan(plan)


def test_assert_acyclic_passes_dag():
    plan = [
        _task(id="t1"),
        _task(id="t2", agent_id="a2", depends_on=["t1"]),
    ]
    assert_acyclic_dispatch_plan(plan)  # no raise


# ─── code-task detection ──────────────────────
def test_is_code_task_by_task_kind():
    assert is_code_implementation_task(_task(task_kind="code")) is True
    assert is_code_implementation_task(_task(task_kind="review")) is False


def test_is_code_task_by_project_output():
    t = _task(expected_outputs=[DispatchExpectedOutput(id="p", type="project")])
    assert is_code_implementation_task(t) is True


def test_is_code_task_by_target_paths():
    assert is_code_implementation_task(_task(target_paths=["src/x.ts"])) is True


def test_is_code_task_target_paths_but_review_text():
    t = _task(task="审查代码质量", target_paths=["src/x.ts"])
    assert is_code_implementation_task(t) is False


def test_is_code_task_by_text_keyword():
    assert is_code_implementation_task(_task(task="实现登录功能")) is True
    assert is_code_implementation_task(_task(task="implement the API")) is True


def test_is_code_task_review_text_not_code():
    assert is_code_implementation_task(_task(task="review the design")) is False


# ─── contract normalization ──────────────────────
def test_normalize_adds_project_output_and_criteria():
    t = normalize_task_contract(_task(task="实现 API"))
    assert any(o.type == "project" and o.required for o in t.expected_outputs)
    assert CODE_TASK_RUNNABLE_ACCEPTANCE_CRITERION in t.acceptance_criteria
    assert CODE_TASK_RUNNABLE_REQUIRED_EVIDENCE in t.required_evidence


def test_normalize_noop_for_non_code():
    t = _task(task="review the plan")
    assert normalize_task_contract(t) is t


def test_normalize_does_not_duplicate_criteria():
    t = _task(
        task="实现 API",
        acceptance_criteria=[CODE_TASK_RUNNABLE_ACCEPTANCE_CRITERION],
    )
    out = normalize_task_contract(t)
    assert out.acceptance_criteria.count(CODE_TASK_RUNNABLE_ACCEPTANCE_CRITERION) == 1


def test_normalize_preserves_existing_project_output_id():
    t = _task(
        task="实现 API",
        expected_outputs=[DispatchExpectedOutput(id="myproj", type="project")],
    )
    out = normalize_task_contract(t)
    proj = [o for o in out.expected_outputs if o.type == "project"]
    assert len(proj) == 1
    assert proj[0].id == "myproj"
    assert proj[0].required is True


# ─── dependency compilation ──────────────────────
def test_compile_infers_dependency_from_text_reference():
    plan = [
        _task(id="prd_task", task="撰写 PRD 产品需求文档"),
        _task(id="impl", agent_id="a2", task="基于 prd_task 的产物实现前端"),
    ]
    result = compile_dispatch_plan(plan)
    impl = next(t for t in result.plan if t.id == "impl")
    assert "prd_task" in (impl.depends_on or [])
    assert any(d.task_id == "impl" for d in result.inferred_dependencies)


def test_compile_infers_dependency_from_artifact_topic():
    plan = [
        _task(id="prd", task="产出 PRD 产品需求文档"),
        _task(id="ui", agent_id="designer", task="根据 PRD 需求文档设计 UI 设计稿"),
    ]
    result = compile_dispatch_plan(plan)
    ui = next(t for t in result.plan if t.id == "ui")
    assert "prd" in (ui.depends_on or [])


def test_compile_adds_input_deps():
    plan = [
        _task(id="t0", task="产出 PRD", expected_outputs=[
            DispatchExpectedOutput(id="prd", type="document")
        ]),
        _task(
            id="t1",
            agent_id="a2",
            task="some unrelated task",
            inputs=[DispatchTaskInput(from_task_id="t0", output_id="prd")],
        ),
    ]
    result = compile_dispatch_plan(plan)
    t1 = next(t for t in result.plan if t.id == "t1")
    assert "t0" in (t1.depends_on or [])


def test_compile_and_validate_combined():
    plan = parse_dispatch_plan_tool_args(
        {
            "tasks": [
                {"id": "t0", "agentId": "a1", "task": "产出 PRD 需求文档"},
                {"id": "t1", "agentId": "a2", "task": "基于 t0 实现前端代码"},
            ]
        }
    )
    result = compile_and_validate_dispatch_plan(plan, AGENTS, ORCH)
    t1 = next(t for t in result.plan if t.id == "t1")
    assert "t0" in (t1.depends_on or [])
    # t1 is a code task → contract normalized:
    assert any(o.type == "project" for o in (t1.expected_outputs or []))


def test_compile_and_validate_rejects_invalid():
    plan = [_task(agent_id="ghost")]
    with pytest.raises(ValueError, match="unavailable agentId"):
        compile_and_validate_dispatch_plan(plan, AGENTS, ORCH)


# ─── graph queries ──────────────────────
def test_collect_dependency_closure():
    plan = [
        _task(id="a"),
        _task(id="b", agent_id="a2", depends_on=["a"]),
        _task(id="c", agent_id="a2", depends_on=["b"]),
    ]
    assert collect_dependency_closure(plan, "c") == ["a", "b"]
    assert collect_dependency_closure(plan, "missing") == []


def test_get_required_expected_outputs():
    t = _task(
        expected_outputs=[
            DispatchExpectedOutput(id="o1", type="document", required=True),
            DispatchExpectedOutput(id="o2", type="web_app", required=False),
            DispatchExpectedOutput(id="o3", type="diagram"),
        ]
    )
    ids = [o.id for o in get_required_expected_outputs(t)]
    assert ids == ["o1", "o3"]


def test_task_expects_artifact():
    assert task_expects_artifact(
        _task(expected_outputs=[DispatchExpectedOutput(id="o", type="document")])
    )
    assert task_expects_artifact(_task(task="生成一份 PRD 文档"))
    assert not task_expects_artifact(_task(task="just think about it"))
