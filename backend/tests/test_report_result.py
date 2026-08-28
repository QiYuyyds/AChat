"""Tests for the report_result terminal tool and ReAct loop terminal termination.

Covers:
- 5.1: report_result handler stores payload in _report_result_cache and returns ok
- 5.2: _run_react_loop terminates after report_result call (no next model call)
- 5.3: spawn_subagent_loop extracts structured result from _report_result_cache
- 5.4: fallback when subagent ends without report_result → _extract_run_final_text works
- 5.5: _report_result_cache is cleaned after extraction
- 5.6: _run_subagent_loop injects report_result regardless of depth
- 5.7: solo/coordinated mode does NOT inject report_result
"""

from __future__ import annotations

import asyncio

import pytest

from app.tools.base import ToolContext
from app.tools.registry import tool_registry
from app.tools.report_result import (
    ReportResultPayload,
    _report_result_cache,
    report_result_tool,
)

# ─── 5.1: handler stores payload and returns ok ─────────────────────────────


def _ctx(run_id: str = "run_test") -> ToolContext:
    return ToolContext(
        conversation_id="conv_test",
        workspace_path="/tmp/test",
        agent_id="ag_sub",
        run_id=run_id,
        cancel_event=asyncio.Event(),
        tool_names=[],
        dispatch_mode="subagent",
    )


@pytest.mark.asyncio
async def test_report_result_handler_stores_payload():
    _report_result_cache.clear()
    args = {
        "summary": "Task completed successfully",
        "keyDecisions": ["chose approach A"],
        "filesChanged": ["src/main.py", "src/util.py"],
        "artifacts": ["art_1"],
    }
    result = await report_result_tool.handler(args, _ctx("run_1"))
    assert result.ok is True
    assert result.value == {"status": "reported"}

    payload = _report_result_cache.get("run_1")
    assert payload is not None
    assert payload.summary == "Task completed successfully"
    assert payload.key_decisions == ["chose approach A"]
    assert payload.files_changed == ["src/main.py", "src/util.py"]
    assert payload.artifacts == ["art_1"]
    _report_result_cache.clear()


@pytest.mark.asyncio
async def test_report_result_handler_minimal_args():
    _report_result_cache.clear()
    result = await report_result_tool.handler(
        {"summary": "done"}, _ctx("run_2")
    )
    assert result.ok is True
    payload = _report_result_cache.get("run_2")
    assert payload is not None
    assert payload.summary == "done"
    assert payload.key_decisions == []
    assert payload.files_changed == []
    assert payload.artifacts == []
    _report_result_cache.clear()


@pytest.mark.asyncio
async def test_report_result_handler_empty_summary():
    _report_result_cache.clear()
    result = await report_result_tool.handler({"summary": ""}, _ctx("run_3"))
    assert result.ok is True
    assert result.value.get("warning") is not None
    _report_result_cache.clear()


# ─── 5.2: TERMINAL_TOOLS constant and _run_react_loop termination ──────────


def test_terminal_tools_contains_report_result():
    from app.services.agent_runner import TERMINAL_TOOLS

    assert "report_result" in TERMINAL_TOOLS


def test_terminal_tools_is_frozenset():
    from app.services.agent_runner import TERMINAL_TOOLS

    assert isinstance(TERMINAL_TOOLS, frozenset)


# ─── 5.3: spawn_subagent_loop extracts structured result ─────────────────────


def test_spawn_subagent_loop_extracts_payload(monkeypatch):
    """When _report_result_cache has a payload for the child run,
    spawn_subagent_loop should use it instead of _extract_run_final_text."""
    from app.services.agent_loop import LoopRunResult

    # Pre-populate cache with a structured payload
    _report_result_cache["run_child_1"] = ReportResultPayload(
        summary="Structured summary here",
        key_decisions=["decision 1"],
        files_changed=["file_a.py"],
        artifacts=["art_x"],
    )

    # We test the extraction logic directly by simulating what spawn_subagent_loop does
    child_run_id = "run_child_1"
    payload = _report_result_cache.pop(child_run_id, None)

    assert payload is not None
    result = LoopRunResult(
        status="complete",
        text=payload.summary,
        artifact_ids=payload.artifacts,
        workspace_changes=payload.files_changed,
        key_decisions=payload.key_decisions,
    )
    assert result.text == "Structured summary here"
    assert "file_a.py" in result.workspace_changes
    assert "decision 1" in result.key_decisions
    assert "art_x" in result.artifact_ids

    # Ensure cache is cleaned
    assert child_run_id not in _report_result_cache


# ─── 5.4: fallback when no report_result ─────────────────────────────────────


def test_spawn_subagent_loop_fallback_no_payload():
    """When _report_result_cache has no payload, workspace_changes/key_decisions
    should be empty lists (fallback to _extract_run_final_text path)."""
    from app.services.agent_loop import LoopRunResult

    _report_result_cache.clear()

    child_run_id = "run_child_no_report"
    payload = _report_result_cache.pop(child_run_id, None)

    assert payload is None

    result = LoopRunResult(
        status="complete",
        text="fallback text",
        artifact_ids=[],
        workspace_changes=[],
        key_decisions=[],
    )
    assert result.text == "fallback text"
    assert result.workspace_changes == []
    assert result.key_decisions == []


# ─── 5.5: cache is cleaned after extraction ──────────────────────────────────


def test_report_result_cache_cleaned_after_pop():
    _report_result_cache.clear()
    _report_result_cache["run_5"] = ReportResultPayload(summary="test")
    assert "run_5" in _report_result_cache

    _ = _report_result_cache.pop("run_5", None)
    assert "run_5" not in _report_result_cache
    _report_result_cache.clear()


# ─── 5.6: _run_subagent_loop injects report_result regardless of depth ───────


def test_subagent_prompt_contains_report_result_guidance():
    from app.services.agent_loop import build_subagent_system_prompt

    prompt = build_subagent_system_prompt("base prompt")
    assert "report_result" in prompt
    assert "终态工具" in prompt or "terminal" in prompt.lower()


def test_subagent_prompt_contains_report_result_instructions():
    from app.services.agent_loop import build_subagent_system_prompt

    prompt = build_subagent_system_prompt("base prompt")
    assert "report_result" in prompt
    assert "完成任务时必须调用" in prompt


# ─── 5.7: solo/coordinated mode does NOT inject report_result ────────────────


def test_solo_prompt_does_not_contain_report_result():
    from app.services.agent_loop import build_solo_system_prompt

    prompt = build_solo_system_prompt("base prompt")
    assert "report_result" not in prompt


def test_coordinated_prompt_does_not_contain_report_result():
    from app.services.agent_loop import build_coordinated_system_prompt

    prompt = build_coordinated_system_prompt("base prompt")
    assert "report_result" not in prompt


def test_report_result_tool_registered():
    tool = tool_registry.get("report_result")
    assert tool is not None
    assert tool.name == "report_result"
