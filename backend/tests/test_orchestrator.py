"""Tests for the orchestrator port (phase 5 Core-B).

Covers the pure/synchronous building blocks (plan compile/validate, replan
context, DAG ordering + skip-on-blocked + conflict wiring, completion gating,
prompt/XML rendering) plus one end-to-end dispatch driven by a scripted fake
adapter that plans → dispatches to a mock child → aggregates.
"""

import asyncio

import pytest_asyncio

from app.schemas.dispatch import (
    DispatchExpectedOutput,
    DispatchPlanItem,
    DispatchRequiredCommand,
)
from app.services import dispatch_plan as dp
from app.services import orchestrator as orch
from app.services import orchestrator_prompts as prompts


# ─── plan compile / validate ──────────────────────────────────────────────────
def test_compile_and_validate_orders_dependencies():
    plan = [
        DispatchPlanItem(id="t2", agent_id="a", task="按设计稿实现页面", depends_on=["t1"]),
        DispatchPlanItem(id="t1", agent_id="a", task="产出 UI 设计稿"),
    ]
    compiled = dp.compile_and_validate_dispatch_plan(plan, [{"id": "a"}], "orch").plan
    assert {t.id for t in compiled} == {"t1", "t2"}
    t2 = next(t for t in compiled if t.id == "t2")
    assert t2.depends_on == ["t1"]


def test_validate_rejects_dispatch_to_orchestrator_itself():
    plan = [DispatchPlanItem(id="t1", agent_id="orch", task="do")]
    import pytest

    with pytest.raises(ValueError, match="recurse"):
        dp.validate_dispatch_plan(plan, [{"id": "orch"}], "orch")


def test_validate_rejects_unknown_agent():
    plan = [DispatchPlanItem(id="t1", agent_id="ghost", task="do")]
    import pytest

    with pytest.raises(ValueError, match="unavailable"):
        dp.validate_dispatch_plan(plan, [{"id": "a"}], "orch")


def test_assert_acyclic_detects_cycle():
    plan = [
        DispatchPlanItem(id="t1", agent_id="a", task="x", depends_on=["t2"]),
        DispatchPlanItem(id="t2", agent_id="a", task="y", depends_on=["t1"]),
    ]
    import pytest

    with pytest.raises(ValueError, match="circular dependency"):
        dp.assert_acyclic_dispatch_plan(plan)


# ─── replan helpers ───────────────────────────────────────────────────────────
def test_should_replan_true_on_failure():
    views = [dp.ReplanTaskView(task_id="t1", agent_id="a", status="failed", error="boom")]
    assert dp.should_replan(views, []) is True


def test_should_replan_false_when_all_complete_no_conflicts():
    views = [dp.ReplanTaskView(task_id="t1", agent_id="a", status="complete")]
    assert dp.should_replan(views, []) is False


def test_should_replan_true_on_conflict():
    views = [dp.ReplanTaskView(task_id="t1", agent_id="a", status="complete")]
    conflicts = [dp.ReplanConflictView(path="/x", task_ids=["t1", "t2"])]
    assert dp.should_replan(views, conflicts) is True


def test_build_replan_context_lists_done_and_failed():
    views = [
        dp.ReplanTaskView(task_id="t1", agent_id="a", status="complete"),
        dp.ReplanTaskView(task_id="t2", agent_id="b", status="failed", error="boom"),
    ]
    ctx = dp.build_replan_context(views, [])
    assert '<task id="t1" agent="a" status="complete" />' in ctx
    # failed tasks now have opening+closing tags (to hold task_report content)
    assert '<task id="t2" agent="b" status="failed" error="boom">' in ctx
    assert "<original_request>" not in ctx  # the prefix wraps it; replan ctx itself doesn't


def test_build_revise_context_includes_feedback_and_plan():
    plan = [DispatchPlanItem(id="t1", agent_id="a", task="do it", depends_on=None)]
    ctx = dp.build_revise_context(plan, "make it parallel")
    assert '<task id="t1" agent="a">do it</task>' in ctx
    assert "make it parallel" in ctx


# ─── prompt / XML rendering ───────────────────────────────────────────────────
def test_escape_xml_and_xml_attr():
    assert prompts.escape_xml('a<b>&c') == "a&lt;b&gt;&amp;c"
    assert prompts.xml_attr('he said "hi" <x>') == '"he said &quot;hi&quot; &lt;x&gt;"'


def test_render_expected_outputs_xml():
    outputs = [
        DispatchExpectedOutput(id="o1", type="document", required=True),
        DispatchExpectedOutput(id="o2", type="web_app", required=False, description="the app"),
    ]
    xml = prompts.render_expected_outputs_xml(outputs)
    assert '<output id="o1" type="document" required="true" />' in xml
    assert '<output id="o2" type="web_app" required="false">the app</output>' in xml


def test_render_acceptance_criteria_xml():
    xml = prompts.render_acceptance_criteria_xml(["builds pass", "tests <green>"])
    assert "<item>builds pass</item>" in xml
    assert "<item>tests &lt;green&gt;</item>" in xml


def test_render_task_evidence_contract_xml():
    task = DispatchPlanItem(
        id="t1",
        agent_id="a",
        task="impl",
        task_kind="code",
        target_paths=["src/x.ts"],
        expected_workspace_changes=["add x"],
        required_commands=[
            DispatchRequiredCommand(command="pnpm build", cwd="frontend", timeout_ms=300000)
        ],
        required_evidence=["build exitCode=0"],
    )
    xml = prompts.render_task_evidence_contract_xml(task)
    assert "<task_kind>code</task_kind>" in xml
    assert "<target_path>src/x.ts</target_path>" in xml
    assert "<expected_workspace_change>add x</expected_workspace_change>" in xml
    assert '<required_command command="pnpm build" cwd="frontend" timeoutMs="300000" />' in xml
    assert "<required_evidence>build exitCode=0</required_evidence>" in xml


def test_build_aggregate_prompt_keyword():
    assert "## 当前阶段" in prompts.build_orchestrator_aggregate_prompt("base")
    assert "不要再调用 plan_tasks" in prompts.build_orchestrator_aggregate_prompt("base")


def test_render_task_result_report_xml():
    report = {
        "status": "complete",
        "summary": "done",
        "acceptanceResults": [{"criterion": "c1", "passed": True, "evidence": "ok"}],
        "filesChanged": [{"path": "a.ts", "action": "created"}],
        "commandsRun": [{"command": "pnpm build", "exitCode": 0}],
        "tests": [{"command": "pytest", "passed": True}],
    }
    xml = prompts.render_task_result_report_xml(report)
    assert '<task_report status="complete">' in xml
    assert "<summary>done</summary>" in xml
    assert '<acceptance criterion="c1" passed="true">ok</acceptance>' in xml
    assert '<file path="a.ts" action="created" />' in xml
    assert '<command command="pnpm build" exitCode="0" />' in xml
    assert '<test command="pytest" passed="true" />' in xml


# ─── DAG building blocks (skip on blocked, terminal results) ──────────────────
def test_skipped_task_result_when_blocked():
    task = DispatchPlanItem(id="t2", agent_id="a", task="x", depends_on=["t1"])
    blocked = orch.DispatchTaskResult(run_id=None, status="failed", error="boom")
    result = orch._skipped_task_result(task, [{"taskId": "t1", "result": blocked}])
    assert result.status == "skipped"
    assert "t1:failed" in result.error


def test_resolve_task_inputs_marks_missing():
    plan = [
        DispatchPlanItem(
            id="t1",
            agent_id="a",
            task="produce",
            expected_outputs=[DispatchExpectedOutput(id="o1", type="document")],
        ),
        DispatchPlanItem(
            id="t2",
            agent_id="a",
            task="consume",
            inputs=[{"fromTaskId": "t1", "outputId": "o1"}],
        ),
    ]
    t2 = plan[1]
    # upstream produced nothing → input is missing
    resolved = orch._resolve_task_inputs(t2, {}, plan)
    assert len(resolved) == 1
    assert resolved[0].missing is True
    assert resolved[0].type == "document"

    # upstream produced the artifact → resolved
    up = {"t1": orch.DispatchTaskResult(run_id="r1", status="complete", output_artifacts={"o1": "art_1"})}
    resolved2 = orch._resolve_task_inputs(t2, up, plan)
    assert resolved2[0].missing is False
    assert resolved2[0].artifact_id == "art_1"


def test_merge_attempt_aggregate_dedups_artifacts():
    aggregate = orch._empty_run_execution_result()
    aggregate.artifact_ids = ["art_1"]
    aggregate.output_message_ids = ["m1"]
    result = orch.DispatchTaskResult(
        run_id="r1", status="complete", artifact_ids=["art_1", "art_2"]
    )
    merged = orch._merge_attempt_aggregate(result, aggregate)
    assert merged.artifact_ids == ["art_1", "art_2"]
    assert merged.output_message_ids == ["m1"]


# ─── conflict detection wiring ────────────────────────────────────────────────
def test_detect_wave_conflicts_via_orchestrator_helpers():
    from app.utils.dispatch_file_writes import RunFileWrites, detect_wave_conflicts

    runs = [
        RunFileWrites(task_id="t1", agent_id="a", run_id="r1", writes={"/ws/x.ts": "h1"}),
        RunFileWrites(task_id="t2", agent_id="b", run_id="r2", writes={"/ws/x.ts": "h2"}),
    ]
    conflicts = detect_wave_conflicts(runs)
    assert len(conflicts) == 1
    views = orch._to_replan_conflicts(conflicts)
    assert views[0].path == "/ws/x.ts"
    assert set(views[0].task_ids) == {"t1", "t2"}


# ─── completion gating ────────────────────────────────────────────────────────
def test_evaluate_child_task_result_fails_without_report():
    from app.utils.dispatch_run_evidence import RunToolEvidence

    task = DispatchPlanItem(id="t1", agent_id="a", task="do")
    result = orch.DispatchTaskResult(run_id="r1", status="complete", task_report=None)
    evaluated, summary = orch._evaluate_child_task_result(task, result, RunToolEvidence())
    assert evaluated.status == "failed"
    assert "report_task_result" in evaluated.error
    assert summary.has_report is False


def test_evaluate_child_task_result_passes_with_report():
    from app.utils.dispatch_run_evidence import RunToolEvidence

    task = DispatchPlanItem(id="t1", agent_id="a", task="write a doc")
    result = orch.DispatchTaskResult(
        run_id="r1",
        status="complete",
        task_report={"status": "complete", "summary": "done"},
    )
    evaluated, summary = orch._evaluate_child_task_result(task, result, RunToolEvidence())
    assert evaluated.status == "complete"
    assert summary.has_report is True
    assert summary.report_status == "complete"
    assert summary.advisory_issues == []


def test_bind_implicit_single_output():
    task = DispatchPlanItem(
        id="t1",
        agent_id="a",
        task="produce a doc",
        expected_outputs=[DispatchExpectedOutput(id="o1", type="document", required=True)],
    )
    result = orch.DispatchTaskResult(run_id="r1", status="complete", artifact_ids=["art_1"])
    bound = orch._bind_implicit_single_output(task, result)
    assert bound["o1"] == "art_1"


# ─── advisory issues + LLM semantic evaluation (tasks 2.5, 6.1-6.3) ────────────
def test_evaluate_child_task_result_collects_advisory_issues():
    """When report is complete but evidence has issues, advisory_issues are collected
    instead of blocking (task 6.1)."""
    from app.utils.dispatch_run_evidence import RunCommandEvidence, RunToolEvidence

    task = DispatchPlanItem(
        id="t1", agent_id="a", task="impl",
        task_kind="code",
        target_paths=["src/foo.py"],
        acceptance_criteria=["builds"],
    )
    result = orch.DispatchTaskResult(
        run_id="r1", status="complete",
        task_report={"status": "complete", "summary": "done"},
    )
    evidence = RunToolEvidence(
        commands=[RunCommandEvidence(
            command="pytest", cwd="/ws", exit_code=1, timed_out=False, is_error=False
        )]
    )
    evaluated, summary = orch._evaluate_child_task_result(
        task, result, evidence, has_build_toolchain=True
    )
    # Task still passes sync evaluation — advisory issues don't block
    assert evaluated.status == "complete"
    assert summary.report_status == "complete"
    assert len(summary.advisory_issues) > 0
    assert any("Failed command" in i for i in summary.advisory_issues)
    assert any("Target path" in i for i in summary.advisory_issues)
    assert any("Acceptance criteria" in i for i in summary.advisory_issues)


def test_evaluate_child_task_result_no_advisory_when_clean():
    """Clean report + matching evidence → no advisory issues (task 6.1)."""
    from app.utils.dispatch_run_evidence import RunCommandEvidence, RunToolEvidence

    task = DispatchPlanItem(
        id="t1", agent_id="a", task="impl",
        task_kind="code",
        acceptance_criteria=["builds"],
        target_paths=["src/foo.py"],
        required_commands=[DispatchRequiredCommand(command="pytest")],
    )
    result = orch.DispatchTaskResult(
        run_id="r1", status="complete",
        task_report={
            "status": "complete", "summary": "done",
            "acceptanceResults": [{"criterion": "builds", "passed": True, "evidence": "ok"}],
            "filesChanged": [{"path": "src/foo.py", "action": "modified"}],
            "commandsRun": [{"command": "pytest", "exitCode": 0}],
        },
    )
    evidence = RunToolEvidence(commands=[RunCommandEvidence(
        command="pytest", cwd="/ws", exit_code=0, timed_out=False, is_error=False
    )])
    evaluated, summary = orch._evaluate_child_task_result(
        task, result, evidence, has_build_toolchain=True
    )
    assert evaluated.status == "complete"
    assert summary.advisory_issues == []


def test_evaluate_with_llm_returns_pass_when_no_agent():
    """_evaluate_with_llm returns (True, '') when ctx has no orchestrator agent (task 6.2)."""
    import asyncio
    from app.services.orchestrator import DagContext, _evaluate_with_llm
    from app.services.task_result_report import TaskEvidenceSummary
    from app.utils.dispatch_run_evidence import RunToolEvidence

    task = DispatchPlanItem(id="t1", agent_id="a", task="do")
    summary = TaskEvidenceSummary(
        advisory_issues=["some issue"],
        has_report=True,
        report_status="complete",
        evidence=RunToolEvidence(),
    )
    ctx = DagContext(
        parent_run_id="r1",
        conversation_id="c1",
        trigger_message_id="m1",
        workspace=None,  # type: ignore[arg-type]
        cancel_event=asyncio.Event(),
        orchestrator_agent=None,
    )
    import asyncio
    passed, feedback = asyncio.run(
        _evaluate_with_llm(task, {"status": "complete", "summary": "ok"}, summary, ctx)
    )
    assert passed is True
    assert feedback == ""


async def test_collect_text_from_stream_collects_text_parts():
    """_collect_text_from_stream correctly assembles text from part.start + part.delta."""
    from app.schemas.events import PartStartEvent
    from app.utils.clock import now_ms
    from app.utils.ids import new_message_id

    async def fake_stream():
        message_id = new_message_id()
        yield PartStartEvent(
            conversation_id="c1", timestamp=now_ms(),
            message_id=message_id, part_index=0,
            part={"type": "text", "content": "Hello "},
        )
        # Simulate a text.append delta
        from dataclasses import dataclass

        @dataclass
        class FakeDelta:
            type: str = "part.delta"
            delta: dict = None  # type: ignore[assignment]

        yield FakeDelta(delta={"type": "text.append", "text": "World"})

    text = await orch._collect_text_from_stream(fake_stream())
    assert text == "Hello World"


def test_build_replan_context_includes_review_feedback():
    """Replan context includes review task's report summary/blockers (tasks 4.2, 6.3)."""
    views = [
        dp.ReplanTaskView(
            task_id="t1", agent_id="a", status="complete",
        ),
        dp.ReplanTaskView(
            task_id="t2", agent_id="reviewer", status="failed",
            error="review failed",
            task_report={
                "status": "failed",
                "summary": "Code quality issues found",
                "blockers": ["missing error handling", "no tests"],
                "acceptanceResults": [
                    {"criterion": "error handling", "passed": False, "evidence": "missing"},
                    {"criterion": "tests", "passed": True, "evidence": "ok"},
                ],
            },
        ),
    ]
    ctx = dp.build_replan_context(views, [])
    # Review feedback is included
    assert "<report_summary>Code quality issues found</report_summary>" in ctx
    assert "<blocker>missing error handling</blocker>" in ctx
    assert "<blocker>no tests</blocker>" in ctx
    assert "<failed_criterion>error handling</failed_criterion>" in ctx
    # Passed criteria are NOT included as <failed_criterion>
    # Only the failed criterion "error handling" should appear as an XML element
    assert "<failed_criterion>error handling</failed_criterion>" in ctx
    # The passed criterion "tests" should not appear as a failed_criterion element
    assert "<failed_criterion>tests</failed_criterion>" not in ctx


def test_should_replan_triggers_on_review_task_failed():
    """Review task failed → should_replan returns True (tasks 4.1, 4.3, 6.3)."""
    views = [
        dp.ReplanTaskView(task_id="t1", agent_id="a", status="complete"),
        dp.ReplanTaskView(
            task_id="t2", agent_id="reviewer", status="failed",
            error="review found issues",
            task_report={"status": "failed", "summary": "issues"},
        ),
    ]
    assert dp.should_replan(views, []) is True


def test_should_replan_true_on_complete_with_blockers():
    """A complete task with blockers in its report triggers replan.

    This covers the scenario where a review agent reports status='complete'
    (the review action itself was done) but includes blockers (critical
    issues found) that require the implementation task to be fixed.
    """
    views = [
        dp.ReplanTaskView(task_id="t1", agent_id="designer", status="complete"),
        dp.ReplanTaskView(
            task_id="t2", agent_id="reviewer", status="complete",
            task_report={
                "status": "complete",
                "summary": "Review completed, found critical issues",
                "blockers": ["Canvas 水波纵向压缩", "Canvas水边界与CSS不同步"],
            },
        ),
    ]
    assert dp.should_replan(views, []) is True


def test_should_replan_false_when_complete_no_blockers():
    """A complete task with no blockers does NOT trigger replan."""
    views = [
        dp.ReplanTaskView(task_id="t1", agent_id="a", status="complete"),
        dp.ReplanTaskView(
            task_id="t2", agent_id="reviewer", status="complete",
            task_report={"status": "complete", "summary": "All good", "blockers": []},
        ),
    ]
    assert dp.should_replan(views, []) is False


def test_build_replan_context_includes_complete_task_with_blockers():
    """Replan context expands complete tasks that have blockers."""
    views = [
        dp.ReplanTaskView(task_id="t1", agent_id="designer", status="complete"),
        dp.ReplanTaskView(
            task_id="t2", agent_id="reviewer", status="complete",
            task_report={
                "status": "complete",
                "summary": "Found critical issues",
                "blockers": ["Canvas 水波纵向压缩", "CSS不同步"],
            },
        ),
    ]
    ctx = dp.build_replan_context(views, [])
    # The complete task with blockers should be expanded (not self-closing)
    assert 'has_review_blockers="true"' in ctx
    assert "<report_summary>Found critical issues</report_summary>" in ctx
    assert "<blocker>Canvas 水波纵向压缩</blocker>" in ctx
    assert "<blocker>CSS不同步</blocker>" in ctx
    # The clean complete task should still be self-closing
    assert '<task id="t1" agent="designer" status="complete" />' in ctx


def test_to_replan_views_includes_task_report():
    """_to_replan_views passes task_report from result to view (task 4.2)."""
    plan_by_id = {
        "t1": DispatchPlanItem(id="t1", agent_id="a", task="impl"),
    }
    results = {
        "t1": orch.DispatchTaskResult(
            run_id="r1", status="failed",
            error="boom",
            task_report={"status": "failed", "summary": "broke", "blockers": ["db"]},
        ),
    }
    views = orch._to_replan_views(plan_by_id, results)
    assert len(views) == 1
    assert views[0].task_id == "t1"
    assert views[0].status == "failed"
    assert views[0].error == "boom"
    assert views[0].task_report == {"status": "failed", "summary": "broke", "blockers": ["db"]}


def test_to_replan_views_handles_missing_result():
    """_to_replan_views handles tasks with no result (skipped)."""
    plan_by_id = {
        "t1": DispatchPlanItem(id="t1", agent_id="a", task="impl"),
    }
    views = orch._to_replan_views(plan_by_id, {})
    assert views[0].status == "skipped"
    assert views[0].task_report is None
    assert views[0].error is None


# ─── e2e dispatch with a scripted fake adapter ────────────────────────────────
class _ScriptedAdapter:
    """A fake adapter: plan stage emits plan_tasks, child emits report_task_result,
    aggregate emits a text message. Distinguished by the tool set it is given."""

    def __init__(self, name: str, plan_tasks: list[dict]) -> None:
        self._name = name
        self._plan_tasks = plan_tasks

    @property
    def name(self) -> str:
        return self._name

    async def stream(self, input, cancel_event):  # noqa: A002 - matches adapter API
        from app.schemas.events import (
            MessageEndEvent,
            MessageStartEvent,
            PartStartEvent,
            ToolCallEvent,
        )
        from app.utils.clock import now_ms
        from app.utils.ids import new_message_id, new_tool_call_id

        tools = set(input.tool_names)
        message_id = new_message_id()
        yield MessageStartEvent(
            conversation_id=input.conversation_id,
            timestamp=now_ms(),
            message_id=message_id,
            agent_id=input.agent_id,
            run_id=input.run_id,
        )

        if "plan_tasks" in tools:
            # plan stage: emit one plan_tasks tool call; consume_stream intercepts it.
            yield ToolCallEvent(
                conversation_id=input.conversation_id,
                timestamp=now_ms(),
                message_id=message_id,
                call_id=new_tool_call_id(),
                tool_name="plan_tasks",
                args={"tasks": self._plan_tasks},
            )
            return  # consume_stream stops + closes the message on the plan_tasks call

        from app.schemas.events import ToolResultEvent
        from app.services.task_result_report import REPORT_TASK_RESULT_TOOL_NAME

        if REPORT_TASK_RESULT_TOOL_NAME in tools:
            # child stage: report completion.
            call_id = new_tool_call_id()
            yield ToolCallEvent(
                conversation_id=input.conversation_id,
                timestamp=now_ms(),
                message_id=message_id,
                call_id=call_id,
                tool_name=REPORT_TASK_RESULT_TOOL_NAME,
                args={"status": "complete", "summary": "child done"},
            )
            yield ToolResultEvent(
                conversation_id=input.conversation_id,
                timestamp=now_ms(),
                message_id=message_id,
                call_id=call_id,
                result={"status": "complete", "summary": "child done"},
                is_error=False,
            )
            yield MessageEndEvent(
                conversation_id=input.conversation_id,
                timestamp=now_ms(),
                message_id=message_id,
            )
            return

        # aggregate stage: plain text summary.
        yield PartStartEvent(
            conversation_id=input.conversation_id,
            timestamp=now_ms(),
            message_id=message_id,
            part_index=0,
            part={"type": "text", "content": "最终总结：t1 完成"},
        )
        yield MessageEndEvent(
            conversation_id=input.conversation_id,
            timestamp=now_ms(),
            message_id=message_id,
        )


@pytest_asyncio.fixture
async def orch_setup(db, tmp_path):
    """Seed an orchestrator + a worker agent (both on a scripted adapter), a
    group conversation, a sandbox workspace, and a trigger user message."""
    from app.adapters.registry import agent_registry
    from app.db.engine import get_db
    from app.db.models import Agent, Conversation, Message, Workspace
    from app.utils.clock import now_ms
    from app.utils.ids import (
        new_conversation_id,
        new_message_id,
        new_workspace_id,
    )

    plan_tasks = [{"id": "t1", "agentId": "ag_worker", "task": "写一段说明文档"}]
    agent_registry.register(_ScriptedAdapter("scripted", plan_tasks))

    now = now_ms()
    ws_root = tmp_path / "ws"
    ws_root.mkdir(parents=True, exist_ok=True)
    conv_id = new_conversation_id()
    msg_id = new_message_id()

    async with get_db() as session:
        orch_agent = Agent(
            id="ag_orch2",
            name="Orchestrator",
            avatar="O",
            description="orch",
            system_prompt="orch prompt",
            adapter_name="scripted",
            is_builtin=True,
            is_orchestrator=True,
            supports_vision=False,
            created_at=now,
        )
        orch_agent.capabilities_list = []
        orch_agent.tool_names_list = ["plan_tasks", "ask_user"]

        worker = Agent(
            id="ag_worker",
            name="Worker",
            avatar="W",
            description="worker",
            system_prompt="worker prompt",
            adapter_name="scripted",
            is_builtin=False,
            is_orchestrator=False,
            supports_vision=False,
            created_at=now,
        )
        worker.capabilities_list = []
        worker.tool_names_list = []

        session.add(orch_agent)
        session.add(worker)

        conv = Conversation(
            id=conv_id,
            title="T",
            mode="group",
            archived=False,
            fs_write_approval_mode="auto",
            created_at=now,
            updated_at=now,
        )
        conv.agent_ids_list = ["ag_orch2", "ag_worker"]
        conv.pinned_message_ids_list = []
        conv.bookmarked_message_ids_list = []
        session.add(conv)
        session.add(
            Workspace(
                id=new_workspace_id(),
                conversation_id=conv_id,
                root_path=str(ws_root),
                mode="sandbox",
                bound_path=None,
                created_at=now,
            )
        )
        trigger = Message(
            id=msg_id,
            conversation_id=conv_id,
            role="user",
            agent_id=None,
            status="complete",
            run_id=None,
            created_at=now,
        )
        trigger.parts_list = [{"type": "text", "content": "帮我写文档"}]
        trigger.mentioned_agent_ids_list = []
        session.add(trigger)

    return {"conversation_id": conv_id, "agent_id": "ag_orch2", "trigger_message_id": msg_id}


async def test_orchestrator_plan_dispatch_aggregate_e2e(orch_setup):
    """Drive a full run: plan → auto-approve the parked plan → child completes →
    aggregate. Assert the dispatch lifecycle events fired and the run completed."""
    from sqlalchemy import select

    from app.db.engine import get_db
    from app.db.models import AgentRun
    from app.services.agent_runner import AgentRunnerImpl
    from app.services.event_bus import event_bus
    from app.services.pending_dispatch_plans import pending_dispatch_plans

    collected = []

    async def _drain(queue):
        try:
            while True:
                ev = await asyncio.wait_for(queue.get(), timeout=3.0)
                collected.append(ev)
                # auto-approve the plan the moment it is parked
                if getattr(ev, "type", None) == "dispatch.plan.pending":
                    pending_dispatch_plans.approve(ev.pending_plan.id)
        except TimeoutError:
            return

    async with event_bus.subscribe() as queue:
        drainer = asyncio.create_task(_drain(queue))
        runner = AgentRunnerImpl()
        handle = runner.run(
            agent_id=orch_setup["agent_id"],
            conversation_id=orch_setup["conversation_id"],
            trigger_message_id=orch_setup["trigger_message_id"],
        )
        entry = None
        from app.services import agent_runner as ar

        # wait for the run task to finish
        for _ in range(200):
            entry = ar._active_runs.get(handle.run_id)
            if entry is None:
                break
            await asyncio.sleep(0.02)
        if entry is not None:
            await entry[0]
        await asyncio.sleep(0.1)
        await drainer

    types = [getattr(e, "type", None) for e in collected]
    assert "dispatch.plan.pending" in types
    assert "dispatch.plan" in types
    assert "dispatch.start" in types
    assert "dispatch.end" in types

    dispatch_end = next(e for e in collected if getattr(e, "type", None) == "dispatch.end")
    assert dispatch_end.status == "complete"
    assert dispatch_end.task_id == "t1"

    async with get_db() as session:
        run = (
            await session.execute(select(AgentRun).where(AgentRun.id == handle.run_id))
        ).scalar_one()
    assert run.status == "complete"


# ─── dispatch.retry event test ───────────────────────────────────────────────
class _RetryOnceAdapter:
    """Fake adapter: plan stage emits plan_tasks; child stage fails on the first
    call then succeeds on the second; aggregate emits a text message."""

    def __init__(self, name: str, plan_tasks: list[dict]) -> None:
        self._name = name
        self._plan_tasks = plan_tasks
        self._child_call_count = 0

    @property
    def name(self) -> str:
        return self._name

    async def stream(self, input, cancel_event):  # noqa: A002
        from app.schemas.events import (
            MessageEndEvent,
            MessageStartEvent,
            PartStartEvent,
            ToolCallEvent,
        )
        from app.services.task_result_report import REPORT_TASK_RESULT_TOOL_NAME
        from app.utils.clock import now_ms
        from app.utils.ids import new_message_id, new_tool_call_id

        tools = set(input.tool_names)
        message_id = new_message_id()
        yield MessageStartEvent(
            conversation_id=input.conversation_id,
            timestamp=now_ms(),
            message_id=message_id,
            agent_id=input.agent_id,
            run_id=input.run_id,
        )

        if "plan_tasks" in tools:
            yield ToolCallEvent(
                conversation_id=input.conversation_id,
                timestamp=now_ms(),
                message_id=message_id,
                call_id=new_tool_call_id(),
                tool_name="plan_tasks",
                args={"tasks": self._plan_tasks},
            )
            return

        if REPORT_TASK_RESULT_TOOL_NAME in tools:
            self._child_call_count += 1
            call_id = new_tool_call_id()
            if self._child_call_count == 1:
                # First attempt: report failure → harness will retry.
                yield ToolCallEvent(
                    conversation_id=input.conversation_id,
                    timestamp=now_ms(),
                    message_id=message_id,
                    call_id=call_id,
                    tool_name=REPORT_TASK_RESULT_TOOL_NAME,
                    args={"status": "failed", "summary": "first attempt broke"},
                )
            else:
                # Second attempt: report success.
                yield ToolCallEvent(
                    conversation_id=input.conversation_id,
                    timestamp=now_ms(),
                    message_id=message_id,
                    call_id=call_id,
                    tool_name=REPORT_TASK_RESULT_TOOL_NAME,
                    args={"status": "complete", "summary": "child done"},
                )
            return

        # aggregate stage
        yield PartStartEvent(
            conversation_id=input.conversation_id,
            timestamp=now_ms(),
            message_id=message_id,
            part_index=0,
            part={"type": "text", "content": "最终总结"},
        )
        yield MessageEndEvent(
            conversation_id=input.conversation_id,
            timestamp=now_ms(),
            message_id=message_id,
        )


@pytest_asyncio.fixture
async def retry_setup(db, tmp_path):
    """Same as orch_setup but uses _RetryOnceAdapter so the child fails first."""
    from app.adapters.registry import agent_registry
    from app.db.engine import get_db
    from app.db.models import Agent, Conversation, Message, Workspace
    from app.utils.clock import now_ms
    from app.utils.ids import (
        new_conversation_id,
        new_message_id,
        new_workspace_id,
    )

    plan_tasks = [{"id": "t1", "agentId": "ag_worker_r", "task": "写一段说明文档"}]
    agent_registry.register(_RetryOnceAdapter("retry_scripted", plan_tasks))

    now = now_ms()
    ws_root = tmp_path / "ws_r"
    ws_root.mkdir(parents=True, exist_ok=True)
    conv_id = new_conversation_id()
    msg_id = new_message_id()

    async with get_db() as session:
        orch_agent = Agent(
            id="ag_orch_r",
            name="OrchestratorR",
            avatar="O",
            description="orch",
            system_prompt="orch prompt",
            adapter_name="retry_scripted",
            is_builtin=True,
            is_orchestrator=True,
            supports_vision=False,
            created_at=now,
        )
        orch_agent.capabilities_list = []
        orch_agent.tool_names_list = ["plan_tasks", "ask_user"]

        worker = Agent(
            id="ag_worker_r",
            name="WorkerR",
            avatar="W",
            description="worker",
            system_prompt="worker prompt",
            adapter_name="retry_scripted",
            is_builtin=False,
            is_orchestrator=False,
            supports_vision=False,
            created_at=now,
        )
        worker.capabilities_list = []
        worker.tool_names_list = []

        session.add(orch_agent)
        session.add(worker)

        conv = Conversation(
            id=conv_id,
            title="T",
            mode="group",
            archived=False,
            fs_write_approval_mode="auto",
            created_at=now,
            updated_at=now,
        )
        conv.agent_ids_list = ["ag_orch_r", "ag_worker_r"]
        conv.pinned_message_ids_list = []
        conv.bookmarked_message_ids_list = []
        session.add(conv)
        session.add(
            Workspace(
                id=new_workspace_id(),
                conversation_id=conv_id,
                root_path=str(ws_root),
                mode="sandbox",
                bound_path=None,
                created_at=now,
            )
        )
        trigger = Message(
            id=msg_id,
            conversation_id=conv_id,
            role="user",
            agent_id=None,
            status="complete",
            run_id=None,
            created_at=now,
        )
        trigger.parts_list = [{"type": "text", "content": "帮我写文档"}]
        trigger.mentioned_agent_ids_list = []
        session.add(trigger)

    return {"conversation_id": conv_id, "agent_id": "ag_orch_r", "trigger_message_id": msg_id}


async def test_dispatch_retry_event_emitted(retry_setup):
    """Child task fails first attempt → dispatch.retry emitted before second
    dispatch.start with correct attempt/error fields."""
    from app.services.agent_runner import AgentRunnerImpl
    from app.services.event_bus import event_bus
    from app.services.pending_dispatch_plans import pending_dispatch_plans

    collected = []

    async def _drain(queue):
        try:
            while True:
                ev = await asyncio.wait_for(queue.get(), timeout=3.0)
                collected.append(ev)
                if getattr(ev, "type", None) == "dispatch.plan.pending":
                    pending_dispatch_plans.approve(ev.pending_plan.id)
        except TimeoutError:
            return

    async with event_bus.subscribe() as queue:
        drainer = asyncio.create_task(_drain(queue))
        runner = AgentRunnerImpl()
        handle = runner.run(
            agent_id=retry_setup["agent_id"],
            conversation_id=retry_setup["conversation_id"],
            trigger_message_id=retry_setup["trigger_message_id"],
        )
        entry = None
        from app.services import agent_runner as ar

        for _ in range(200):
            entry = ar._active_runs.get(handle.run_id)
            if entry is None:
                break
            await asyncio.sleep(0.02)
        if entry is not None:
            await entry[0]
        await asyncio.sleep(0.1)
        await drainer

    types = [getattr(e, "type", None) for e in collected]
    assert "dispatch.retry" in types

    retry_event = next(e for e in collected if getattr(e, "type", None) == "dispatch.retry")
    assert retry_event.attempt == 2
    assert retry_event.max_attempts == 4
    assert retry_event.task_id == "t1"
    assert retry_event.error is not None
    assert "first attempt broke" in retry_event.error

    # Verify retry appears before the second dispatch.start
    retry_idx = next(i for i, e in enumerate(collected) if getattr(e, "type", None) == "dispatch.retry")
    start_indices = [
        i for i, e in enumerate(collected) if getattr(e, "type", None) == "dispatch.start"
    ]
    assert len(start_indices) >= 2
    assert retry_idx < start_indices[1]

    # Final dispatch.end should be complete
    dispatch_end = next(e for e in collected if getattr(e, "type", None) == "dispatch.end")
    assert dispatch_end.status == "complete"


# ─── _collect_existing_files (target_paths fallback) ──────────────────────────
import os
import tempfile


def test_collect_existing_files_finds_existing_file():
    """_collect_existing_files returns evidence for files that exist in the workspace."""
    with tempfile.TemporaryDirectory() as workspace_root:
        # Create a file in the workspace
        file_path = os.path.join(workspace_root, "index.html")
        with open(file_path, "w") as f:
            f.write("<html></html>")

        result = orch._collect_existing_files(["index.html"], workspace_root)
        assert len(result) == 1
        assert result[0].path == "index.html"
        assert os.path.isabs(result[0].absolute_path)
        assert result[0].bytes is not None and result[0].bytes > 0
        assert result[0].applied == "existing"


def test_collect_existing_files_returns_empty_for_missing_files():
    """_collect_existing_files returns empty list when files don't exist."""
    with tempfile.TemporaryDirectory() as workspace_root:
        result = orch._collect_existing_files(["nonexistent.html"], workspace_root)
        assert len(result) == 0


def test_collect_existing_files_skips_paths_outside_workspace():
    """_collect_existing_files rejects paths that escape the workspace root."""
    with tempfile.TemporaryDirectory() as workspace_root:
        # Create a file outside the workspace
        outside_dir = tempfile.mkdtemp()
        try:
            outside_file = os.path.join(outside_dir, "evil.txt")
            with open(outside_file, "w") as f:
                f.write("malicious")

            # Try absolute path escape
            result = orch._collect_existing_files([outside_file], workspace_root)
            assert len(result) == 0

            # Try relative path escape
            result2 = orch._collect_existing_files(["../../../etc/passwd"], workspace_root)
            assert len(result2) == 0
        finally:
            import shutil

            shutil.rmtree(outside_dir, ignore_errors=True)


def test_collect_existing_files_handles_mixed_paths():
    """_collect_existing_files handles a mix of existing and non-existing paths."""
    with tempfile.TemporaryDirectory() as workspace_root:
        # Create one file
        with open(os.path.join(workspace_root, "app.py"), "w") as f:
            f.write("print('hello')")

        result = orch._collect_existing_files(["app.py", "missing.txt"], workspace_root)
        assert len(result) == 1
        assert result[0].path == "app.py"


def test_collect_existing_files_handles_empty_list():
    """_collect_existing_files returns empty list for empty input."""
    with tempfile.TemporaryDirectory() as workspace_root:
        result = orch._collect_existing_files([], workspace_root)
        assert len(result) == 0


def test_collect_existing_files_handles_subdirectory_paths():
    """_collect_existing_files finds files in subdirectories."""
    with tempfile.TemporaryDirectory() as workspace_root:
        # Create a file in a subdirectory
        subdir = os.path.join(workspace_root, "src")
        os.makedirs(subdir)
        with open(os.path.join(subdir, "main.py"), "w") as f:
            f.write("print('main')")

        result = orch._collect_existing_files(["src/main.py"], workspace_root)
        assert len(result) == 1
        assert result[0].path == "src/main.py"
