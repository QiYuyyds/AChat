"""Tests for the DAG executor module.

Covers:
- validate_dag: duplicate id, self-dep, missing ref, cycle, valid plan
- topological_waves: diamond, chain, parallel-only, mixed
- execute_dag with mocked spawn_subagent_loop: all-complete, one-fails-skips-downstream, diamond
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from app.observability.run_collector import run_span_collector
from app.observability.span_names import resolve_span_name
from app.schemas.dispatch import DispatchPlanItem
from app.services.dag_executor import (
    DagExecContext,
    execute_dag,
    topological_waves,
    validate_dag,
)


def _item(
    tid: str,
    agent_id: str = "ag_1",
    task: str = "do something",
    depends_on: list[str] | None = None,
) -> DispatchPlanItem:
    return DispatchPlanItem(
        id=tid,
        agentId=agent_id,
        task=task,
        dependsOn=depends_on,
    )


def _ctx() -> DagExecContext:
    return DagExecContext(
        conversation_id="conv_test",
        trigger_message_id="msg_test",
        parent_run_id="run_test",
        cancel_event=asyncio.Event(),
    )


# ─── validate_dag ─────────────────────────────────────────────────────────────


def test_validate_dag_valid_plan():
    tasks = [
        _item("t1"),
        _item("t2", depends_on=["t1"]),
        _item("t3", depends_on=["t1"]),
        _item("t4", depends_on=["t2", "t3"]),
    ]
    errors = validate_dag(tasks)
    assert errors == []


def test_validate_dag_duplicate_id():
    tasks = [_item("t1"), _item("t1")]
    errors = validate_dag(tasks)
    assert any("Duplicate" in e for e in errors)


def test_validate_dag_self_dependency():
    tasks = [_item("t1", depends_on=["t1"])]
    errors = validate_dag(tasks)
    assert any("depends on itself" in e for e in errors)


def test_validate_dag_missing_reference():
    tasks = [_item("t1", depends_on=["t99"])]
    errors = validate_dag(tasks)
    assert any("unknown task" in e for e in errors)


def test_validate_dag_cycle():
    tasks = [
        _item("t1", depends_on=["t2"]),
        _item("t2", depends_on=["t1"]),
    ]
    errors = validate_dag(tasks)
    assert any("Cycle" in e for e in errors)


def test_validate_dag_empty_plan():
    errors = validate_dag([])
    assert errors == []


# ─── topological_waves ────────────────────────────────────────────────────────


def test_topological_waves_parallel_only():
    tasks = [_item("t1"), _item("t2"), _item("t3")]
    waves = topological_waves(tasks)
    assert len(waves) == 1
    assert {t.id for t in waves[0]} == {"t1", "t2", "t3"}


def test_topological_waves_chain():
    tasks = [
        _item("t1"),
        _item("t2", depends_on=["t1"]),
        _item("t3", depends_on=["t2"]),
    ]
    waves = topological_waves(tasks)
    assert len(waves) == 3
    assert waves[0][0].id == "t1"
    assert waves[1][0].id == "t2"
    assert waves[2][0].id == "t3"


def test_topological_waves_diamond():
    tasks = [
        _item("t1"),
        _item("t2", depends_on=["t1"]),
        _item("t3", depends_on=["t1"]),
        _item("t4", depends_on=["t2", "t3"]),
    ]
    waves = topological_waves(tasks)
    assert len(waves) == 3
    assert waves[0][0].id == "t1"
    assert {t.id for t in waves[1]} == {"t2", "t3"}
    assert waves[2][0].id == "t4"


def test_topological_waves_mixed():
    tasks = [
        _item("a"),
        _item("b"),
        _item("c", depends_on=["a"]),
        _item("d", depends_on=["a", "b"]),
        _item("e", depends_on=["c", "d"]),
    ]
    waves = topological_waves(tasks)
    assert len(waves) == 3
    assert {t.id for t in waves[0]} == {"a", "b"}
    assert {t.id for t in waves[1]} == {"c", "d"}
    assert {t.id for t in waves[2]} == {"e"}


def test_topological_waves_cycle_raises():
    tasks = [
        _item("t1", depends_on=["t2"]),
        _item("t2", depends_on=["t1"]),
    ]
    with pytest.raises(ValueError, match="Cycle"):
        topological_waves(tasks)


# ─── execute_dag (mocked spawn_subagent_loop) ─────────────────────────────────


def _mock_spawn(status_map: dict[str, str], text_map: dict[str, str] | None = None):
    """Create a mock spawn_subagent_loop that returns canned results."""
    texts = text_map or {}

    async def _spawn(
        agent_id: str,
        task_description: str,
        conversation_id: str,
        trigger_message_id: str,
        parent_run_id: str,
        parent_cancel_event: asyncio.Event,
        on_start=None,
        workspace_path: str | None = None,
        dispatch_depth: int = 0,
        dispatch_visibility: str = "visible",
        user_id: str | None = None,
        dag_id: str | None = None,
        dag_task_id: str | None = None,
        override_messages: list[dict] | None = None,
        override_system_prompt: str | None = None,
    ):
        # Use the task description as the key (we set task= to the task id in tests)
        status = status_map.get(task_description, "complete")
        text = texts.get(task_description, f"result for {task_description}")
        run_id = f"run_{task_description}"
        if on_start is not None:
            on_start(run_id)
        from app.services.agent_loop import LoopRunResult

        return LoopRunResult(status=status, text=text, run_id=run_id)

    return _spawn


@pytest.mark.asyncio
async def test_execute_dag_all_complete(monkeypatch):
    tasks = [
        _item("t1", task="t1"),
        _item("t2", task="t2", depends_on=["t1"]),
    ]
    monkeypatch.setattr(
        "app.services.agent_loop.spawn_subagent_loop",
        _mock_spawn({"t1": "complete", "t2": "complete"}),
    )

    results = await execute_dag(tasks, _ctx())

    assert set(results.keys()) == {"t1", "t2"}
    assert results["t1"].status == "complete"
    assert results["t2"].status == "complete"
    assert "result for t1" in results["t1"].summary
    assert "result for t2" in results["t2"].summary


@pytest.mark.asyncio
async def test_execute_dag_one_fails_skips_downstream(monkeypatch):
    tasks = [
        _item("t1", task="t1"),
        _item("t2", task="t2", depends_on=["t1"]),
        _item("t3", task="t3"),
    ]
    monkeypatch.setattr(
        "app.services.agent_loop.spawn_subagent_loop",
        _mock_spawn({"t1": "failed", "t2": "complete", "t3": "complete"}),
    )

    results = await execute_dag(tasks, _ctx())

    assert results["t1"].status == "failed"
    assert results["t2"].status == "skipped"
    assert "Upstream" in results["t2"].summary
    assert results["t3"].status == "complete"


@pytest.mark.asyncio
async def test_execute_dag_diamond(monkeypatch):
    tasks = [
        _item("t1", task="t1"),
        _item("t2", task="t2", depends_on=["t1"]),
        _item("t3", task="t3", depends_on=["t1"]),
        _item("t4", task="t4", depends_on=["t2", "t3"]),
    ]
    monkeypatch.setattr(
        "app.services.agent_loop.spawn_subagent_loop",
        _mock_spawn({"t1": "complete", "t2": "complete", "t3": "complete", "t4": "complete"}),
    )

    results = await execute_dag(tasks, _ctx())

    assert all(r.status == "complete" for r in results.values())
    assert set(results.keys()) == {"t1", "t2", "t3", "t4"}


@pytest.mark.asyncio
async def test_execute_dag_node_result_has_child_run_id(monkeypatch):
    tasks = [_item("t1", task="t1")]
    monkeypatch.setattr(
        "app.services.agent_loop.spawn_subagent_loop",
        _mock_spawn({"t1": "complete"}),
    )

    results = await execute_dag(tasks, _ctx())

    assert results["t1"].child_run_id == "run_t1"


@pytest.mark.asyncio
async def test_execute_dag_skipped_node_has_no_child_run_id(monkeypatch):
    tasks = [
        _item("t1", task="t1"),
        _item("t2", task="t2", depends_on=["t1"]),
    ]
    monkeypatch.setattr(
        "app.services.agent_loop.spawn_subagent_loop",
        _mock_spawn({"t1": "failed", "t2": "complete"}),
    )

    results = await execute_dag(tasks, _ctx())

    assert results["t2"].child_run_id is None
    assert results["t2"].status == "skipped"


# ─── DAG trace observability tests ─────────────────────────────────────────


class _FakeSpan:
    """Minimal span stub that records attributes for testing."""

    def __init__(self, name: str, attrs: dict) -> None:
        self.name = name
        self.attrs = dict(attrs)

    def is_recording(self) -> bool:
        return True

    def set_attribute(self, key: str, value: object) -> None:
        self.attrs[key] = value


class _SpanTracker:
    """Tracks span creation and parent-child hierarchy via a simple stack.

    Not context-variable aware, so tests should avoid parallel nodes.
    """

    def __init__(self) -> None:
        self.spans: list[_FakeSpan] = []
        self._stack: list[_FakeSpan] = []

    def start_span(self, span_key: str, *, suffix: str | None = None, **attrs):
        name = resolve_span_name(span_key, suffix)
        span = _FakeSpan(name, attrs)
        parent_name = self._stack[-1].name if self._stack else None
        span.attrs["_parent"] = parent_name

        @contextlib.contextmanager
        def _wrapper():
            self._stack.append(span)
            self.spans.append(span)
            yield span
            self._stack.pop()

        return _wrapper()


@pytest.fixture
def trace_enabled(monkeypatch):
    """Enable tracing with span tracking for DAG executor tests."""
    tracker = _SpanTracker()
    monkeypatch.setattr("app.services.dag_executor.is_trace_enabled", lambda: True)
    monkeypatch.setattr("app.services.dag_executor.start_span", tracker.start_span)
    run_span_collector.clear("run_test")
    yield tracker
    run_span_collector.clear("run_test")


@pytest.mark.asyncio
async def test_dag_spans_created_with_hierarchy(trace_enabled, monkeypatch):
    """3.1: dag.execute / dag.wave / dag.node spans created with correct hierarchy."""
    tasks = [
        _item("t1", task="t1"),
        _item("t2", task="t2", depends_on=["t1"]),
    ]
    monkeypatch.setattr(
        "app.services.agent_loop.spawn_subagent_loop",
        _mock_spawn({"t1": "complete", "t2": "complete"}),
    )

    await execute_dag(tasks, _ctx())

    spans = trace_enabled.spans
    names = [s.name for s in spans]

    # dag.execute span exists
    assert any("dag.execute" in n for n in names)

    # 2 dag.wave spans (one per wave)
    wave_spans = [s for s in spans if "dag.wave" in s.name]
    assert len(wave_spans) == 2

    # 2 dag.node spans (one per node)
    node_spans = [s for s in spans if "dag.node" in s.name]
    assert len(node_spans) == 2

    # dag.wave parent is dag.execute
    for ws in wave_spans:
        assert ws.attrs["_parent"] is not None
        assert "dag.execute" in ws.attrs["_parent"]

    # dag.node parent is dag.wave
    for ns in node_spans:
        assert ns.attrs["_parent"] is not None
        assert "dag.wave" in ns.attrs["_parent"]


@pytest.mark.asyncio
async def test_dag_skipped_node_produces_span(trace_enabled, monkeypatch):
    """3.2: skipped nodes produce dag.node span with node_status=skipped."""
    tasks = [
        _item("t1", task="t1"),
        _item("t2", task="t2", depends_on=["t1"]),
    ]
    monkeypatch.setattr(
        "app.services.agent_loop.spawn_subagent_loop",
        _mock_spawn({"t1": "failed", "t2": "complete"}),
    )

    await execute_dag(tasks, _ctx())

    spans = trace_enabled.spans
    node_spans = [s for s in spans if "dag.node" in s.name]

    # 2 node spans: t1 (failed) + t2 (skipped)
    assert len(node_spans) == 2

    skipped_nodes = [
        s
        for s in node_spans
        if s.attrs.get("agenthub.node_status") == "skipped"
    ]
    assert len(skipped_nodes) == 1
    assert skipped_nodes[0].attrs.get("agenthub.task_id") == "t2"
    assert "Upstream" in skipped_nodes[0].attrs.get("agenthub.error", "")

    # Skipped node should not have any child spans (no tool.dispatch)
    skipped_name = skipped_nodes[0].name
    children = [s for s in spans if s.attrs.get("_parent") == skipped_name]
    assert len(children) == 0


@pytest.mark.asyncio
async def test_dag_collector_includes_execute_and_nodes(trace_enabled, monkeypatch):
    """3.3: run_span_collector.collect includes dag.execute and dag.node snapshots."""
    tasks = [
        _item("t1", task="t1"),
        _item("t2", task="t2", depends_on=["t1"]),
    ]
    monkeypatch.setattr(
        "app.services.agent_loop.spawn_subagent_loop",
        _mock_spawn({"t1": "complete", "t2": "complete"}),
    )

    await execute_dag(tasks, _ctx())

    snapshots = run_span_collector.collect("run_test")

    # dag.execute snapshot
    execute_snaps = [s for s in snapshots if "dag.execute" in s.get("name", "")]
    assert len(execute_snaps) == 1
    assert execute_snaps[0]["agenthub.task_count"] == 2
    assert execute_snaps[0]["agenthub.wave_count"] == 2

    # dag.node snapshots (2 nodes)
    node_snaps = [s for s in snapshots if "dag.node" in s.get("name", "")]
    assert len(node_snaps) == 2
    for snap in node_snaps:
        assert "agenthub.task_id" in snap
        assert "agenthub.node_status" in snap


@pytest.mark.asyncio
async def test_dag_collector_includes_skipped_node(trace_enabled, monkeypatch):
    """3.3b: skipped node appears in collector with node_status=skipped."""
    tasks = [
        _item("t1", task="t1"),
        _item("t2", task="t2", depends_on=["t1"]),
    ]
    monkeypatch.setattr(
        "app.services.agent_loop.spawn_subagent_loop",
        _mock_spawn({"t1": "failed", "t2": "complete"}),
    )

    await execute_dag(tasks, _ctx())

    snapshots = run_span_collector.collect("run_test")
    node_snaps = [s for s in snapshots if "dag.node" in s.get("name", "")]

    skipped_snaps = [
        s
        for s in node_snaps
        if s.get("agenthub.node_status") == "skipped"
    ]
    assert len(skipped_snaps) == 1
    assert skipped_snaps[0]["agenthub.task_id"] == "t2"


# ─── Upstream output injection tests (dag-edge-data-flow) ───────────────────


def _mock_spawn_with_capture(
    status_map: dict[str, str],
    text_map: dict[str, str] | None = None,
    structured_map: dict[str, dict] | None = None,
):
    """Mock spawn that captures task_description for inspection.

    ``structured_map`` keys are the *original* task id (matching task= field),
    values are dicts with keys: workspace_changes, key_decisions, artifact_ids.
    """
    texts = text_map or {}
    structured = structured_map or {}
    captured: dict[str, str] = {}  # task_id -> received task_description

    async def _spawn(
        agent_id: str,
        task_description: str,
        conversation_id: str,
        trigger_message_id: str,
        parent_run_id: str,
        parent_cancel_event: asyncio.Event,
        on_start=None,
        workspace_path: str | None = None,
        dispatch_depth: int = 0,
        dispatch_visibility: str = "visible",
        user_id: str | None = None,
        dag_id: str | None = None,
        dag_task_id: str | None = None,
        override_messages: list[dict] | None = None,
        override_system_prompt: str | None = None,
    ):
        # The task_description may be enriched; the original task id is the
        # first line / original task value we set in tests
        # We capture the full description for inspection
        # Determine the "key" — original task id (first 2 chars for "tN")
        key = task_description[:2] if len(task_description) >= 2 else task_description
        captured[key] = task_description

        status = status_map.get(key, "complete")
        text = texts.get(key, f"result for {key}")
        run_id = f"run_{key}"
        if on_start is not None:
            on_start(run_id)
        from app.services.agent_loop import LoopRunResult

        sdata = structured.get(key, {})
        return LoopRunResult(
            status=status,
            text=text,
            run_id=run_id,
            workspace_changes=sdata.get("workspace_changes", []),
            artifact_ids=sdata.get("artifact_ids", []),
            key_decisions=sdata.get("key_decisions", []),
        )

    return _spawn, captured


@pytest.mark.asyncio
async def test_upstream_output_injected_into_downstream(monkeypatch):
    """5.1: When t2 depends on t1 and t1 completes, t2's task_description
    contains t1's structured output (summary, key_decisions, etc.)."""
    tasks = [
        _item("t1", task="t1"),
        _item("t2", task="t2", depends_on=["t1"]),
    ]
    spawn, captured = _mock_spawn_with_capture(
        {"t1": "complete", "t2": "complete"},
        {"t1": "API design done"},
        {"t1": {
            "workspace_changes": ["src/api.py"],
            "key_decisions": ["use REST"],
            "artifact_ids": ["art_1"],
        }},
    )
    monkeypatch.setattr("app.services.agent_loop.spawn_subagent_loop", spawn)

    results = await execute_dag(tasks, _ctx())

    # t2's captured task_description should contain upstream output
    t2_desc = captured["t2"]
    assert "上游任务输出" in t2_desc
    assert "API design done" in t2_desc
    assert "use REST" in t2_desc
    assert "src/api.py" in t2_desc
    assert "art_1" in t2_desc

    # t1 should NOT have upstream block (no upstream)
    t1_desc = captured["t1"]
    assert "上游任务输出" not in t1_desc

    # NodeResult should have structured fields populated
    assert results["t1"].workspace_changes == ["src/api.py"]
    assert results["t1"].key_decisions == ["use REST"]
    assert results["t1"].artifact_ids == ["art_1"]


@pytest.mark.asyncio
async def test_multi_upstream_outputs_concatenated(monkeypatch):
    """5.2: Multi-upstream dependency — outputs are concatenated in dependsOn order."""
    tasks = [
        _item("t1", task="t1"),
        _item("t2", task="t2"),
        _item("t3", task="t3", depends_on=["t1", "t2"]),
    ]
    spawn, captured = _mock_spawn_with_capture(
        {"t1": "complete", "t2": "complete", "t3": "complete"},
        {"t1": "result A", "t2": "result B"},
    )
    monkeypatch.setattr("app.services.agent_loop.spawn_subagent_loop", spawn)

    await execute_dag(tasks, _ctx())

    t3_desc = captured["t3"]
    # Both upstream results present, in dependsOn order (t1 before t2)
    assert "上游任务输出" in t3_desc
    pos_a = t3_desc.find("result A")
    pos_b = t3_desc.find("result B")
    assert pos_a != -1 and pos_b != -1
    assert pos_a < pos_b


@pytest.mark.asyncio
async def test_task_inputs_from_task_id_used(monkeypatch):
    """5.3: task.inputs field is read and from_task_id is used to look up upstream."""
    from app.schemas.dispatch import DispatchTaskInput

    tasks = [
        _item("t1", task="t1"),
        _item("t2", task="t2"),
        _item("t3", task="t3", depends_on=["t1", "t2"]),
    ]
    # Override t3 with explicit inputs
    tasks[2] = DispatchPlanItem(
        id="t3",
        agentId="ag_1",
        task="t3",
        dependsOn=["t1", "t2"],
        inputs=[
            DispatchTaskInput(fromTaskId="t1", outputId="out1"),
            DispatchTaskInput(fromTaskId="t2", outputId="out2"),
        ],
    )
    spawn, captured = _mock_spawn_with_capture(
        {"t1": "complete", "t2": "complete", "t3": "complete"},
        {"t1": "output from t1", "t2": "output from t2"},
    )
    monkeypatch.setattr("app.services.agent_loop.spawn_subagent_loop", spawn)

    await execute_dag(tasks, _ctx())

    t3_desc = captured["t3"]
    assert "output from t1" in t3_desc
    assert "output from t2" in t3_desc


@pytest.mark.asyncio
async def test_task_inputs_none_falls_back_to_depends_on(monkeypatch):
    """5.4: task.inputs is None → falls back to depends_on derivation."""
    tasks = [
        _item("t1", task="t1"),
        _item("t2", task="t2", depends_on=["t1"]),
    ]
    # t2 has no explicit inputs field (defaults to None)
    assert tasks[1].inputs is None
    spawn, captured = _mock_spawn_with_capture(
        {"t1": "complete", "t2": "complete"},
        {"t1": "fallback test output"},
    )
    monkeypatch.setattr("app.services.agent_loop.spawn_subagent_loop", spawn)

    await execute_dag(tasks, _ctx())

    t2_desc = captured["t2"]
    assert "fallback test output" in t2_desc
    assert "上游任务输出" in t2_desc


@pytest.mark.asyncio
async def test_empty_fields_omitted(monkeypatch):
    """5.5: Empty fields are omitted (no key_decisions → section not shown)."""
    tasks = [
        _item("t1", task="t1"),
        _item("t2", task="t2", depends_on=["t1"]),
    ]
    spawn, captured = _mock_spawn_with_capture(
        {"t1": "complete", "t2": "complete"},
        {"t1": "only summary"},
        {"t1": {"workspace_changes": [], "key_decisions": [], "artifact_ids": []}},
    )
    monkeypatch.setattr("app.services.agent_loop.spawn_subagent_loop", spawn)

    await execute_dag(tasks, _ctx())

    t2_desc = captured["t2"]
    assert "only summary" in t2_desc
    assert "关键决策" not in t2_desc
    assert "变更文件" not in t2_desc
    assert "产出 Artifact" not in t2_desc


@pytest.mark.asyncio
async def test_fallback_natural_language_summary(monkeypatch):
    """5.6: Fallback — upstream didn't call report_result → natural language
    summary injected, other fields empty."""
    tasks = [
        _item("t1", task="t1"),
        _item("t2", task="t2", depends_on=["t1"]),
    ]
    # Mock returns LoopRunResult with no structured fields (all empty lists)
    spawn, captured = _mock_spawn_with_capture(
        {"t1": "complete", "t2": "complete"},
        {"t1": "natural language fallback summary"},
    )
    monkeypatch.setattr("app.services.agent_loop.spawn_subagent_loop", spawn)

    await execute_dag(tasks, _ctx())

    t2_desc = captured["t2"]
    assert "natural language fallback summary" in t2_desc
    # No structured fields should appear
    assert "关键决策" not in t2_desc
    assert "变更文件" not in t2_desc
    assert "产出 Artifact" not in t2_desc


@pytest.mark.asyncio
async def test_token_budget_truncation(monkeypatch):
    """5.7: Token budget control — 3+ upstream nodes with long summaries
    → truncation kicks in."""
    long_summary = "x" * 4000  # ~1000 tokens per summary
    tasks = [
        _item("t1", task="t1"),
        _item("t2", task="t2"),
        _item("t3", task="t3"),
        _item("t4", task="t4", depends_on=["t1", "t2", "t3"]),
    ]
    spawn, captured = _mock_spawn_with_capture(
        {"t1": "complete", "t2": "complete", "t3": "complete", "t4": "complete"},
        {"t1": long_summary, "t2": long_summary, "t3": long_summary},
    )
    monkeypatch.setattr("app.services.agent_loop.spawn_subagent_loop", spawn)

    await execute_dag(tasks, _ctx())

    t4_desc = captured["t4"]
    assert "summary truncated" in t4_desc
    assert "tokens omitted" in t4_desc


@pytest.mark.asyncio
async def test_noderesult_structured_fields_populated(monkeypatch):
    """5.8: NodeResult has workspace_changes / artifact_ids / key_decisions /
    error_detail populated correctly."""
    tasks = [
        _item("t1", task="t1"),
        _item("t2", task="t2", depends_on=["t1"]),
    ]
    spawn, _ = _mock_spawn_with_capture(
        {"t1": "complete", "t2": "failed"},
        {"t1": "ok", "t2": "error occurred"},
        {"t1": {
            "workspace_changes": ["file_a.py", "file_b.py"],
            "key_decisions": ["chose X"],
            "artifact_ids": ["art_1", "art_2"],
        }},
    )
    monkeypatch.setattr("app.services.agent_loop.spawn_subagent_loop", spawn)

    results = await execute_dag(tasks, _ctx())

    # t1 completed with structured fields
    assert results["t1"].workspace_changes == ["file_a.py", "file_b.py"]
    assert results["t1"].artifact_ids == ["art_1", "art_2"]
    assert results["t1"].key_decisions == ["chose X"]
    assert results["t1"].error_detail is None

    # t2 failed — error_detail should be populated
    assert results["t2"].status == "failed"
    assert results["t2"].error_detail == "error occurred"


# ─── Non-regression tests (dag-edge-data-flow) ─────────────────────────────


@pytest.mark.asyncio
async def test_no_depends_no_injection(monkeypatch):
    """6.2: Tasks with no dependsOn → no upstream injection (task_description unchanged)."""
    tasks = [
        _item("t1", task="t1"),
        _item("t2", task="t2"),
    ]
    spawn, captured = _mock_spawn_with_capture(
        {"t1": "complete", "t2": "complete"},
    )
    monkeypatch.setattr("app.services.agent_loop.spawn_subagent_loop", spawn)

    await execute_dag(tasks, _ctx())

    # Neither task should have upstream block
    assert "上游任务输出" not in captured["t1"]
    assert "上游任务输出" not in captured["t2"]
    assert captured["t1"] == "t1"
    assert captured["t2"] == "t2"


@pytest.mark.asyncio
async def test_skipped_upstream_not_injected(monkeypatch):
    """6.3: Skipped upstream → not injected.

    If t1 fails and t2 is skipped, t3 (depends on t1 and t2) should be
    skipped too — no injection happens for skipped nodes.
    """
    tasks = [
        _item("t1", task="t1"),
        _item("t2", task="t2", depends_on=["t1"]),
        _item("t3", task="t3", depends_on=["t2"]),
    ]
    spawn, captured = _mock_spawn_with_capture(
        {"t1": "failed", "t2": "complete", "t3": "complete"},
    )
    monkeypatch.setattr("app.services.agent_loop.spawn_subagent_loop", spawn)

    results = await execute_dag(tasks, _ctx())

    # t1 failed, t2 skipped (upstream failed), t3 skipped (upstream t2 skipped)
    assert results["t1"].status == "failed"
    assert results["t2"].status == "skipped"
    assert results["t3"].status == "skipped"

    # Only t1 was actually executed (spawn called), t2 and t3 were skipped
    # So captured only has t1
    assert "t1" in captured
    assert "t2" not in captured
    assert "t3" not in captured


# ─── Non-regression: DAG context defaults (dag-session-registry) ─────────────


@pytest.mark.asyncio
async def test_dag_exec_context_defaults_dag_id_empty(monkeypatch):
    """9.2: dispatch_plan default behavior unchanged — dag_id is empty string
    when not explicitly set (backward compat)."""
    ctx = _ctx()  # dag_id defaults to ""
    assert ctx.dag_id == ""
    assert ctx.all_task_ids == []


@pytest.mark.asyncio
async def test_dag_without_dag_id_works(monkeypatch):
    """9.1/9.2: Existing DAG tests pass — dag_id="" does not break execution."""
    tasks = [
        _item("t1", task="t1"),
        _item("t2", task="t2", depends_on=["t1"]),
    ]
    monkeypatch.setattr(
        "app.services.agent_loop.spawn_subagent_loop",
        _mock_spawn({"t1": "complete", "t2": "complete"}),
    )

    results = await execute_dag(tasks, _ctx())

    assert results["t1"].status == "complete"
    assert results["t2"].status == "complete"


@pytest.mark.asyncio
async def test_dag_with_dag_id_registers_sessions(monkeypatch):
    """9.1: When dag_id is set, sessions are registered in AgentSessionRegistry."""
    from app.services.agent_session_registry import agent_session_registry

    agent_session_registry.clear()

    tasks = [
        _item("t1", task="t1"),
        _item("t2", task="t2", depends_on=["t1"]),
    ]
    monkeypatch.setattr(
        "app.services.agent_loop.spawn_subagent_loop",
        _mock_spawn({"t1": "complete", "t2": "complete"}),
    )

    ctx = DagExecContext(
        conversation_id="conv_test",
        trigger_message_id="msg_test",
        parent_run_id="run_test",
        cancel_event=asyncio.Event(),
        dag_id="dag_test_123",
        all_task_ids=["t1", "t2"],
    )

    results = await execute_dag(tasks, ctx)  # noqa: F841

    # Sessions should be registered
    s1 = agent_session_registry.get("t1")
    s2 = agent_session_registry.get("t2")
    assert s1 is not None
    assert s2 is not None
    assert s1.status == "completed"
    assert s2.status == "completed"

    # DAG task set should contain both
    task_ids = agent_session_registry.get_by_dag("dag_test_123")
    assert task_ids == {"t1", "t2"}

    agent_session_registry.clear()


@pytest.mark.asyncio
async def test_spawn_subagent_loop_without_dag_id(monkeypatch):
    """9.3: spawn_subagent_loop without dag_id/dag_task_id works (None defaults)."""
    from app.services.agent_loop import LoopRunResult, spawn_subagent_loop

    # Mock run_with_args to avoid actual execution
    async def _mock_consume():
        return LoopRunResult(status="complete", text="ok", run_id="child_run")

    # We can't easily test spawn_subagent_loop in isolation since it calls
    # run_with_args. Instead, verify the function accepts the call with None
    # defaults by checking the signature.
    import inspect

    sig = inspect.signature(spawn_subagent_loop)
    assert sig.parameters["dag_id"].default is None
    assert sig.parameters["dag_task_id"].default is None


# ─── Incremental retry tests (dag-incremental-retry) ─────────────────────────


def _mock_spawn_with_retry_capture(
    status_map: dict[str, str],
    text_map: dict[str, str] | None = None,
):
    """Mock spawn that captures override_messages/override_system_prompt."""
    texts = text_map or {}
    captured: dict[str, dict] = {}  # task_id -> {override_messages, override_system_prompt, ...}

    async def _spawn(
        agent_id: str,
        task_description: str,
        conversation_id: str,
        trigger_message_id: str,
        parent_run_id: str,
        parent_cancel_event: asyncio.Event,
        on_start=None,
        workspace_path: str | None = None,
        dispatch_depth: int = 0,
        dispatch_visibility: str = "visible",
        user_id: str | None = None,
        dag_id: str | None = None,
        dag_task_id: str | None = None,
        override_messages: list[dict] | None = None,
        override_system_prompt: str | None = None,
    ):
        key = task_description[:2] if len(task_description) >= 2 else task_description
        captured[key] = {
            "override_messages": override_messages,
            "override_system_prompt": override_system_prompt,
            "task_description": task_description,
        }
        status = status_map.get(key, "complete")
        text = texts.get(key, f"result for {key}")
        run_id = f"run_{key}"
        if on_start is not None:
            on_start(run_id)
        from app.services.agent_loop import LoopRunResult

        return LoopRunResult(status=status, text=text, run_id=run_id)

    return _spawn, captured


@pytest.mark.asyncio
async def test_retry_mode_with_active_session_rebuilds_history(monkeypatch):
    """7.3: retry mode with active session → rebuilds messages + reuses system_prompt."""
    from app.services.agent_session_registry import (
        AgentSession,
        agent_session_registry,
    )

    agent_session_registry.clear()

    # Register an original session for task t1 under original_dag_id
    session = AgentSession(
        task_id="t1",
        run_id="run_orig_t1",
        agent_id="ag_1",
        conversation_id="conv_test",
        parent_run_id="run_parent",
        dispatch_depth=1,
        status="completed",
        system_prompt="original system prompt",
        created_at=0,  # epoch ms — old enough to be "expired" by timestamp
        # but still in registry since we haven't called cleanup_expired
    )
    agent_session_registry.register_with_dag("dag_orig", "t1", session)

    # Mock build_run_messages to return canned history
    async def _mock_build_run_messages(run_id, conversation_id, agent_id, include_hidden=False):
        return [
            {"role": "user", "content": "original task"},
            {"role": "assistant", "content": "original response"},
        ]

    monkeypatch.setattr(
        "app.services.conversation_context.build_run_messages",
        _mock_build_run_messages,
    )

    monkeypatch.setattr(
        "app.services.compact_pipeline.COMPACT_MASK_RATIO",
        999.0,  # disable compaction
    )

    spawn, captured = _mock_spawn_with_retry_capture({"t1": "complete"})
    monkeypatch.setattr("app.services.agent_loop.spawn_subagent_loop", spawn)

    tasks = [_item("t1", task="t1")]
    ctx = DagExecContext(
        conversation_id="conv_test",
        trigger_message_id="msg_test",
        parent_run_id="run_test",
        cancel_event=asyncio.Event(),
        dag_id="dag_retry",
        all_task_ids=["t1"],
        original_dag_id="dag_orig",
        retry_mode=True,
    )

    results = await execute_dag(tasks, ctx)

    assert results["t1"].status == "complete"

    # Verify override_messages was set
    t1_data = captured["t1"]
    assert t1_data["override_messages"] is not None
    # Should contain: [system_prompt, user, assistant, user(retry instruction)]
    msgs = t1_data["override_messages"]
    assert msgs[0]["role"] == "system"
    assert msgs[0]["content"] == "original system prompt"
    assert msgs[-1]["role"] == "user"
    assert "之前" in msgs[-1]["content"]

    # Verify override_system_prompt was reused
    assert t1_data["override_system_prompt"] == "original system prompt"

    agent_session_registry.clear()


@pytest.mark.asyncio
async def test_retry_mode_session_not_found_creates_fresh_run(monkeypatch):
    """7.3: retry mode with no session → no override, fresh run."""
    from app.services.agent_session_registry import agent_session_registry

    agent_session_registry.clear()

    # No session registered — mock build_run_messages should not be called
    call_count = [0]

    async def _mock_build_run_messages(*args, **kwargs):
        call_count[0] += 1
        return []

    monkeypatch.setattr(
        "app.services.conversation_context.build_run_messages",
        _mock_build_run_messages,
    )

    # Also mock AgentRun query to return None
    class _FakeResult:
        def scalar_one_or_none(self):
            return None

    class _FakeDB:
        async def execute(self, stmt):
            return _FakeResult()

    @contextlib.asynccontextmanager
    async def _fake_db():
        yield _FakeDB()

    monkeypatch.setattr("app.db.engine.get_local_db", _fake_db)

    spawn, captured = _mock_spawn_with_retry_capture({"t1": "complete"})
    monkeypatch.setattr("app.services.agent_loop.spawn_subagent_loop", spawn)

    tasks = [_item("t1", task="t1")]
    ctx = DagExecContext(
        conversation_id="conv_test",
        trigger_message_id="msg_test",
        parent_run_id="run_test",
        cancel_event=asyncio.Event(),
        dag_id="dag_retry",
        all_task_ids=["t1"],
        original_dag_id="dag_orig",
        retry_mode=True,
    )

    results = await execute_dag(tasks, ctx)

    assert results["t1"].status == "complete"
    # No override was applied (fresh run)
    assert captured["t1"]["override_messages"] is None
    assert captured["t1"]["override_system_prompt"] is None

    agent_session_registry.clear()


@pytest.mark.asyncio
async def test_retry_mode_expired_session_falls_back_to_agentrun(monkeypatch):
    """7.3: retry mode with expired session → falls back to AgentRun table query."""
    from app.services.agent_session_registry import agent_session_registry

    agent_session_registry.clear()

    # Mock build_run_messages to return canned history
    async def _mock_build_run_messages(run_id, conversation_id, agent_id, include_hidden=False):
        return [{"role": "user", "content": "old message"}]

    monkeypatch.setattr(
        "app.services.conversation_context.build_run_messages",
        _mock_build_run_messages,
    )
    monkeypatch.setattr(
        "app.services.compact_pipeline.COMPACT_MASK_RATIO",
        999.0,  # disable compaction
    )

    # Mock AgentRun table query to return a run_id
    class _FakeResult:
        def scalar_one_or_none(self):
            return "run_from_db"

    class _FakeDB:
        async def execute(self, stmt):
            return _FakeResult()

    @contextlib.asynccontextmanager
    async def _fake_db():
        yield _FakeDB()

    monkeypatch.setattr("app.db.engine.get_local_db", _fake_db)

    spawn, captured = _mock_spawn_with_retry_capture({"t1": "complete"})
    monkeypatch.setattr("app.services.agent_loop.spawn_subagent_loop", spawn)

    tasks = [_item("t1", task="t1")]
    ctx = DagExecContext(
        conversation_id="conv_test",
        trigger_message_id="msg_test",
        parent_run_id="run_test",
        cancel_event=asyncio.Event(),
        dag_id="dag_retry",
        all_task_ids=["t1"],
        original_dag_id="dag_orig",
        retry_mode=True,
    )

    results = await execute_dag(tasks, ctx)

    assert results["t1"].status == "complete"
    # Override was set via AgentRun fallback
    t1_data = captured["t1"]
    assert t1_data["override_messages"] is not None
    # system_prompt is None because session was not found (AgentRun fallback)
    assert t1_data["override_system_prompt"] is None

    agent_session_registry.clear()


@pytest.mark.asyncio
async def test_retry_mode_with_compaction(monkeypatch):
    """7.4: retry with ratio >= 0.75 → compaction applied."""
    from app.services.agent_session_registry import (
        AgentSession,
        agent_session_registry,
    )

    agent_session_registry.clear()

    session = AgentSession(
        task_id="t1",
        run_id="run_orig_t1",
        agent_id="ag_1",
        conversation_id="conv_test",
        parent_run_id="run_parent",
        dispatch_depth=1,
        status="completed",
        system_prompt="original system prompt",
        created_at=0,
    )
    agent_session_registry.register_with_dag("dag_orig", "t1", session)

    # Create a large message to trigger compaction
    big_content = "x" * 200_000  # ~50k tokens per message
    async def _mock_build_run_messages(run_id, conversation_id, agent_id, include_hidden=False):
        return [
            {"role": "user", "content": big_content},
            {"role": "assistant", "content": big_content},
            {"role": "user", "content": big_content},
        ]

    monkeypatch.setattr(
        "app.services.conversation_context.build_run_messages",
        _mock_build_run_messages,
    )

    # Mock compaction pipeline
    compact_called = [False]

    def _mock_run_compact_pipeline_unified(messages, stage, **kwargs):
        compact_called[0] = True
        return messages  # return as-is (mocked)

    monkeypatch.setattr(
        "app.services.compact_pipeline.run_compact_pipeline_unified",
        _mock_run_compact_pipeline_unified,
    )
    monkeypatch.setattr(
        "app.services.compact_pipeline.COMPACT_MASK_RATIO",
        0.75,
    )

    spawn, captured = _mock_spawn_with_retry_capture({"t1": "complete"})
    monkeypatch.setattr("app.services.agent_loop.spawn_subagent_loop", spawn)

    tasks = [_item("t1", task="t1")]
    ctx = DagExecContext(
        conversation_id="conv_test",
        trigger_message_id="msg_test",
        parent_run_id="run_test",
        cancel_event=asyncio.Event(),
        dag_id="dag_retry",
        all_task_ids=["t1"],
        original_dag_id="dag_orig",
        retry_mode=True,
    )

    results = await execute_dag(tasks, ctx)

    assert results["t1"].status == "complete"
    assert compact_called[0] is True
    assert captured["t1"]["override_messages"] is not None

    agent_session_registry.clear()


@pytest.mark.asyncio
async def test_retry_mode_child_no_report_result_empty_fields(monkeypatch):
    """7.8: child didn't call report_result → workspaceChanges/keyDecisions empty."""
    from app.services.agent_session_registry import (
        AgentSession,
        agent_session_registry,
    )

    agent_session_registry.clear()

    session = AgentSession(
        task_id="t1",
        run_id="run_orig_t1",
        agent_id="ag_1",
        conversation_id="conv_test",
        parent_run_id="run_parent",
        dispatch_depth=1,
        status="completed",
        system_prompt="sys prompt",
        created_at=0,
    )
    agent_session_registry.register_with_dag("dag_orig", "t1", session)

    async def _mock_build_run_messages(run_id, conversation_id, agent_id, include_hidden=False):
        return [{"role": "user", "content": "old"}]

    monkeypatch.setattr(
        "app.services.conversation_context.build_run_messages",
        _mock_build_run_messages,
    )
    monkeypatch.setattr(
        "app.services.compact_pipeline.COMPACT_MASK_RATIO",
        999.0,
    )

    # Mock spawn returns LoopRunResult with no structured fields
    async def _spawn_no_structured(**kwargs):
        from app.services.agent_loop import LoopRunResult
        # Simulate no report_result → all structured fields empty
        return LoopRunResult(
            status="complete",
            text="did the work",
            run_id="run_t1",
            workspace_changes=[],
            key_decisions=[],
            artifact_ids=[],
        )

    monkeypatch.setattr("app.services.agent_loop.spawn_subagent_loop", _spawn_no_structured)

    tasks = [_item("t1", task="t1")]
    ctx = DagExecContext(
        conversation_id="conv_test",
        trigger_message_id="msg_test",
        parent_run_id="run_test",
        cancel_event=asyncio.Event(),
        dag_id="dag_retry",
        all_task_ids=["t1"],
        original_dag_id="dag_orig",
        retry_mode=True,
    )

    results = await execute_dag(tasks, ctx)

    assert results["t1"].status == "complete"
    assert results["t1"].workspace_changes == []
    assert results["t1"].key_decisions == []
    assert results["t1"].artifact_ids == []

    agent_session_registry.clear()


# ─── Non-regression: retry mode doesn't affect full mode ────────────────────


@pytest.mark.asyncio
async def test_full_mode_no_retry_overrides(monkeypatch):
    """8.1: mode=full (default) → no retry logic, behavior unchanged."""
    from app.services.agent_session_registry import agent_session_registry

    agent_session_registry.clear()

    spawn, captured = _mock_spawn_with_retry_capture({"t1": "complete"})
    monkeypatch.setattr("app.services.agent_loop.spawn_subagent_loop", spawn)

    tasks = [_item("t1", task="t1")]
    ctx = DagExecContext(
        conversation_id="conv_test",
        trigger_message_id="msg_test",
        parent_run_id="run_test",
        cancel_event=asyncio.Event(),
        dag_id="dag_new",
        all_task_ids=["t1"],
        # retry_mode defaults to False, original_dag_id defaults to None
    )

    results = await execute_dag(tasks, ctx)

    assert results["t1"].status == "complete"
    # No override was applied
    assert captured["t1"]["override_messages"] is None
    assert captured["t1"]["override_system_prompt"] is None

    agent_session_registry.clear()


@pytest.mark.asyncio
async def test_retry_mode_dependsOn_original_dag_completed(monkeypatch):
    """7.7: retry dependsOn can reference original DAG's completed task IDs."""
    from app.services.agent_session_registry import (
        AgentSession,
        agent_session_registry,
    )

    agent_session_registry.clear()

    # Register sessions for t1 (completed) and t2 (failed) under original DAG
    for tid, status in [("t1", "completed"), ("t2", "failed")]:
        session = AgentSession(
            task_id=tid,
            run_id=f"run_orig_{tid}",
            agent_id="ag_1",
            conversation_id="conv_test",
            parent_run_id="run_parent",
            dispatch_depth=1,
            status=status,
            system_prompt=f"prompt for {tid}",
            created_at=0,
        )
        agent_session_registry.register_with_dag("dag_orig", tid, session)

    async def _mock_build_run_messages(run_id, conversation_id, agent_id, include_hidden=False):
        return [{"role": "user", "content": "old message"}]

    monkeypatch.setattr(
        "app.services.conversation_context.build_run_messages",
        _mock_build_run_messages,
    )
    monkeypatch.setattr(
        "app.services.compact_pipeline.COMPACT_MASK_RATIO",
        999.0,
    )

    spawn, captured = _mock_spawn_with_retry_capture({"t2": "complete"})
    monkeypatch.setattr("app.services.agent_loop.spawn_subagent_loop", spawn)

    # Retry only t2, depending on t1 (from original DAG)
    tasks = [
        _item("t1", task="t1"),  # t1 already succeeded, skip it in retry
        _item("t2", task="t2", depends_on=["t1"]),
    ]
    ctx = DagExecContext(
        conversation_id="conv_test",
        trigger_message_id="msg_test",
        parent_run_id="run_test",
        cancel_event=asyncio.Event(),
        dag_id="dag_retry",
        all_task_ids=["t1", "t2"],
        original_dag_id="dag_orig",
        retry_mode=True,
    )

    results = await execute_dag(tasks, ctx)

    # Both should complete
    assert results["t1"].status == "complete"
    assert results["t2"].status == "complete"

    # t2 should have override_messages (retry for failed task)
    assert captured["t2"]["override_messages"] is not None

    agent_session_registry.clear()


# ─── Session retention tests ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sessions_retained_after_dag_completion(monkeypatch):
    """7.5: sessions exist after DAG completion (not immediately deleted)."""
    from app.services.agent_session_registry import agent_session_registry

    agent_session_registry.clear()

    tasks = [_item("t1", task="t1")]
    monkeypatch.setattr(
        "app.services.agent_loop.spawn_subagent_loop",
        _mock_spawn({"t1": "complete"}),
    )

    ctx = DagExecContext(
        conversation_id="conv_test",
        trigger_message_id="msg_test",
        parent_run_id="run_test",
        cancel_event=asyncio.Event(),
        dag_id="dag_retention",
        all_task_ids=["t1"],
    )

    await execute_dag(tasks, ctx)

    # Session should still exist (marked completed, not deleted)
    session = agent_session_registry.get("t1")
    assert session is not None
    assert session.status == "completed"

    agent_session_registry.clear()


@pytest.mark.asyncio
async def test_cleanup_expired_marks_old_sessions(monkeypatch):
    """7.6: cleanup_expired() marks old sessions as expired (removed from registry)."""
    from app.services.agent_session_registry import agent_session_registry

    agent_session_registry.clear()

    tasks = [_item("t1", task="t1")]
    monkeypatch.setattr(
        "app.services.agent_loop.spawn_subagent_loop",
        _mock_spawn({"t1": "complete"}),
    )

    ctx = DagExecContext(
        conversation_id="conv_test",
        trigger_message_id="msg_test",
        parent_run_id="run_test",
        cancel_event=asyncio.Event(),
        dag_id="dag_cleanup_test",
        all_task_ids=["t1"],
    )

    await execute_dag(tasks, ctx)

    # Session exists
    assert agent_session_registry.get("t1") is not None

    # Force expiry with 0 second TTL
    agent_session_registry.cleanup_expired(expiry_seconds=0)

    # Session should be removed (expired)
    assert agent_session_registry.get("t1") is None

    agent_session_registry.clear()


# ─── dispatch_plan structured return tests ───────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_plan_return_includes_dagId_and_structured_fields(monkeypatch):
    """7.1: dispatch_plan return includes dagId / workspaceChanges / artifactIds / keyDecisions / errorDetail."""
    from unittest.mock import AsyncMock, patch

    from app.services.dag_executor import NodeResult
    from app.tools.base import ToolContext
    from app.tools.dispatch_plan import _handler

    async def mock_execute_dag(tasks, ctx):
        return {
            "t1": NodeResult(
                task_id="t1",
                status="complete",
                summary="done",
                workspace_changes=["file_a.py"],
                artifact_ids=["art_1"],
                key_decisions=["chose X"],
            ),
            "t2": NodeResult(
                task_id="t2",
                status="failed",
                summary="failed",
                error_detail="something went wrong",
            ),
        }

    ctx = ToolContext(
        conversation_id="conv_1",
        workspace_path="/tmp",
        agent_id="ag_1",
        run_id="run_test",
        cancel_event=asyncio.Event(),
        dispatch_mode="coordinated",
        dispatch_depth=0,
        user_id="user_1",
    )

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _fake_db_cm():
        class FakeResult:
            def scalar_one_or_none(self):
                class FakeRun:
                    trigger_message_id = "msg_trigger"
                return FakeRun()
        class FakeDB:
            async def execute(self, stmt):
                return FakeResult()
        yield FakeDB()

    with (
        patch("app.tools.dispatch_plan.execute_dag", new=mock_execute_dag),
        patch("app.tools.dispatch_plan.validate_dag", return_value=[]),
        patch(
            "app.tools.dispatch_plan._verify_agents_in_conversation",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.tools.dispatch_plan._is_plan_approval_enabled",
            new=AsyncMock(return_value=False),
        ),
        patch("app.tools.dispatch_plan.event_bus"),
        patch("app.tools.dispatch_plan.get_local_db", _fake_db_cm),
        patch("app.services.agent_session_registry.agent_session_registry"),
    ):
        result = await _handler(
            {"tasks": [{"id": "t1", "task": "do work"}, {"id": "t2", "task": "fail"}]},
            ctx,
        )

    assert result.ok is True
    assert "dagId" in result.value
    dag_id = result.value["dagId"]
    assert isinstance(dag_id, str)
    assert len(dag_id) > 0

    t1 = result.value["tasks"]["t1"]
    assert t1["status"] == "complete"
    assert t1["workspaceChanges"] == ["file_a.py"]
    assert t1["artifactIds"] == ["art_1"]
    assert t1["keyDecisions"] == ["chose X"]
    assert "errorDetail" not in t1

    t2 = result.value["tasks"]["t2"]
    assert t2["status"] == "failed"
    assert t2["errorDetail"] == "something went wrong"


@pytest.mark.asyncio
async def test_dispatch_plan_mode_full_default(monkeypatch):
    """8.1/7.2: dispatch_plan(mode=full) default behavior unchanged."""

    from app.tools.dispatch_plan import _PARAMETERS

    # Verify mode is in parameters with default "full"
    assert "mode" in _PARAMETERS["properties"]
    assert _PARAMETERS["properties"]["mode"]["default"] == "full"
    assert "originalDagId" in _PARAMETERS["properties"]


@pytest.mark.asyncio
async def test_dispatch_plan_retry_without_originalDagId_returns_error(monkeypatch):
    """2.3: mode=retry without originalDagId → error."""
    from app.tools.base import ToolContext
    from app.tools.dispatch_plan import _handler

    ctx = ToolContext(
        conversation_id="conv_1",
        workspace_path="/tmp",
        agent_id="ag_1",
        run_id="run_test",
        cancel_event=asyncio.Event(),
        dispatch_mode="coordinated",
        dispatch_depth=0,
        user_id="user_1",
    )

    result = await _handler(
        {"tasks": [{"id": "t1", "task": "retry work"}], "mode": "retry"},
        ctx,
    )

    assert result.ok is False
    assert "originalDagId" in result.error


@pytest.mark.asyncio
async def test_dispatch_plan_invalid_mode_returns_error(monkeypatch):
    """mode with invalid value → error."""
    from app.tools.base import ToolContext
    from app.tools.dispatch_plan import _handler

    ctx = ToolContext(
        conversation_id="conv_1",
        workspace_path="/tmp",
        agent_id="ag_1",
        run_id="run_test",
        cancel_event=asyncio.Event(),
        dispatch_mode="coordinated",
        dispatch_depth=0,
        user_id="user_1",
    )

    result = await _handler(
        {"tasks": [{"id": "t1", "task": "work"}], "mode": "invalid"},
        ctx,
    )

    assert result.ok is False
    assert "Invalid mode" in result.error


# ─── spawn_subagent_loop new params ──────────────────────────────────────────


def test_spawn_subagent_loop_has_override_params():
    """Verify spawn_subagent_loop signature includes override_messages/override_system_prompt."""
    import inspect

    from app.services.agent_loop import spawn_subagent_loop

    sig = inspect.signature(spawn_subagent_loop)
    params = sig.parameters
    assert "override_messages" in params
    assert "override_system_prompt" in params
    assert params["override_messages"].default is None
    assert params["override_system_prompt"].default is None


# ─── DagExecContext new fields ───────────────────────────────────────────────


def test_dag_exec_context_has_retry_fields():
    """Verify DagExecContext has original_dag_id and retry_mode fields."""
    ctx = DagExecContext(
        conversation_id="conv_1",
        trigger_message_id="msg_1",
        parent_run_id="run_1",
        cancel_event=asyncio.Event(),
        original_dag_id="dag_orig",
        retry_mode=True,
    )
    assert ctx.original_dag_id == "dag_orig"
    assert ctx.retry_mode is True


def test_dag_exec_context_retry_defaults():
    """Verify DagExecContext retry fields default to None/False."""
    ctx = DagExecContext(
        conversation_id="conv_1",
        trigger_message_id="msg_1",
        parent_run_id="run_1",
        cancel_event=asyncio.Event(),
    )
    assert ctx.original_dag_id is None
    assert ctx.retry_mode is False
