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
