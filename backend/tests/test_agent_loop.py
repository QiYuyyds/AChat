"""Tests for the Unified Agent Loop (solo / coordinated / subagent modes).

Covers:
- get_dispatch_mode: solo default, orchestrated → coordinated mapping
- build_solo_system_prompt: soft self-verify suffix appended
- build_coordinated_system_prompt: coordinated guidance appended
- AgentLoopConfig / LoopRunResult dataclass defaults
"""

from __future__ import annotations

from app.services.agent_loop import (
    AgentLoopConfig,
    LoopRunResult,
    _format_agent_roster,
    build_coordinated_system_prompt,
    build_solo_system_prompt,
    get_dispatch_mode,
)

# ─── get_dispatch_mode ────────────────────────────────────────────────────────


def test_get_dispatch_mode_none_conversation_returns_solo():
    assert get_dispatch_mode(None) == "solo"


def test_get_dispatch_mode_default_solo():
    from types import SimpleNamespace

    conv = SimpleNamespace(dispatch_mode="solo")
    assert get_dispatch_mode(conv) == "solo"


def test_get_dispatch_mode_orchestrated_maps_to_coordinated():
    from types import SimpleNamespace

    conv = SimpleNamespace(dispatch_mode="orchestrated")
    assert get_dispatch_mode(conv) == "coordinated"


def test_get_dispatch_mode_missing_attr_returns_solo():
    """Backward compat: pre-migration conversations lack dispatch_mode."""
    from types import SimpleNamespace

    conv = SimpleNamespace()
    assert get_dispatch_mode(conv) == "solo"


def test_get_dispatch_mode_none_value_returns_solo():
    from types import SimpleNamespace

    conv = SimpleNamespace(dispatch_mode=None)
    assert get_dispatch_mode(conv) == "solo"


# ─── Prompt builders ──────────────────────────────────────────────────────────


def test_build_solo_system_prompt_appends_suffix():
    base = "You are a helpful agent."
    result = build_solo_system_prompt(base)
    assert result.startswith(base)
    assert "自检" in result
    assert "typecheck" in result or "lint" in result


def test_build_coordinated_system_prompt_appends_suffix():
    base = "You are a coordinator."
    result = build_coordinated_system_prompt(base)
    assert result.startswith(base)
    assert "task_dispatch" in result
    assert "协调者" in result
    assert "派发" in result
    # Without roster, the placeholder is replaced with empty string
    assert "{agent_roster}" not in result


def test_build_coordinated_system_prompt_with_roster():
    from types import SimpleNamespace

    base = "You are a coordinator."
    roster = (
        "- agentId: `ag_front` | 名称: 前端工程师 | "
        "描述: 写 UI | 能力: react, css"
    )
    result = build_coordinated_system_prompt(base, roster)
    assert "ag_front" in result
    assert "前端工程师" in result
    assert "react, css" in result


def test_coordinated_prompt_has_dispatch_first_priority():
    """The prompt should emphasize dispatching as the primary behavior."""
    result = build_coordinated_system_prompt("base")
    assert "优先派发" in result


def test_solo_prompt_does_not_contain_dispatch_guidance():
    base = "base"
    result = build_solo_system_prompt(base)
    assert "task_dispatch" not in result
    assert "派发" not in result


def test_coordinated_prompt_contains_self_do_guidance():
    result = build_coordinated_system_prompt("base")
    # The prompt still mentions doing things yourself, but as a secondary option
    assert "自己干" in result or "自己做" in result


def test_format_agent_roster_excludes_orchestrator():
    from types import SimpleNamespace

    agents = [
        SimpleNamespace(
            id="ag_orch",
            name="Orchestrator",
            description="coordinator",
            capabilities=["planning"],
        ),
        SimpleNamespace(
            id="ag_front",
            name="前端",
            description="frontend dev",
            capabilities=["react", "css"],
        ),
    ]
    roster = _format_agent_roster(agents, "ag_orch")
    assert "ag_front" in roster
    assert "前端" in roster
    assert "ag_orch" not in roster


def test_format_agent_roster_empty_returns_message():
    roster = _format_agent_roster([], "ag_orch")
    assert "没有" in roster


# ─── Dataclass defaults ───────────────────────────────────────────────────────


def test_agent_loop_config_defaults():
    cfg = AgentLoopConfig(
        mode="solo",
        conversation_id="conv_1",
        trigger_message_id="msg_1",
    )
    assert cfg.cancel_event is None
    assert cfg.parent_run_id is None
    assert cfg.override_prompt is None
    assert cfg.override_workspace_path is None


def test_loop_run_result_defaults():
    result = LoopRunResult(status="complete")
    assert result.text == ""
    assert result.artifact_ids == []
    assert result.output_message_ids == []


def test_loop_run_result_with_values():
    result = LoopRunResult(
        status="failed",
        text="something went wrong",
        artifact_ids=["art_1"],
        output_message_ids=["msg_1"],
    )
    assert result.status == "failed"
    assert result.text == "something went wrong"
    assert "art_1" in result.artifact_ids
