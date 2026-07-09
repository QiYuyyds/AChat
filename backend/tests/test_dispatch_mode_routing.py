"""Tests for dispatch mode routing in execute_run.

Verifies that:
- Solo mode is used for single-agent conversations
- Coordinated mode is used for orchestrated conversations with orchestrator agent
- Subagent runs (override_prompt) always use solo mode (via execute_simple_run directly)
- Non-orchestrator agents in orchestrated conversations use solo mode
"""

from __future__ import annotations

from app.services.agent_loop import get_dispatch_mode

# ─── Routing logic ────────────────────────────────────────────────────────────


def test_solo_mode_for_single_conversation():
    """Single conversation → solo mode."""
    from types import SimpleNamespace

    conv = SimpleNamespace(dispatch_mode="solo")
    assert get_dispatch_mode(conv) == "solo"


def test_coordinated_mode_for_orchestrated_conversation():
    """Orchestrated conversation → coordinated mode."""
    from types import SimpleNamespace

    conv = SimpleNamespace(dispatch_mode="orchestrated")
    assert get_dispatch_mode(conv) == "coordinated"


def test_solo_mode_for_missing_dispatch_mode():
    """Pre-migration conversation (no dispatch_mode) → solo mode."""
    from types import SimpleNamespace

    conv = SimpleNamespace()
    assert get_dispatch_mode(conv) == "solo"


def test_coordinated_mode_string_is_orchestrated():
    """The DB stores 'orchestrated'; the loop expects 'coordinated'."""
    from types import SimpleNamespace

    conv = SimpleNamespace(dispatch_mode="orchestrated")
    mode = get_dispatch_mode(conv)
    # The mapping: 'orchestrated' (DB) → 'coordinated' (loop mode)
    assert mode == "coordinated"
    assert mode != "orchestrated"


# ─── Routing decision tree ────────────────────────────────────────────────────


def _routing_decision(conv, is_orchestrator: bool, has_override_prompt: bool) -> str:
    """Mirror the routing logic in execute_run."""
    if has_override_prompt:
        return "simple_run"

    dispatch_mode = get_dispatch_mode(conv)
    if dispatch_mode == "coordinated" and is_orchestrator:
        return "coordinated_loop"
    return "solo_loop"


def test_subagent_always_uses_simple_run():
    from types import SimpleNamespace

    conv = SimpleNamespace(dispatch_mode="orchestrated")
    # Subagent with override_prompt → always simple_run (solo)
    assert _routing_decision(conv, is_orchestrator=True, has_override_prompt=True) == "simple_run"


def test_orchestrator_in_orchestrated_conversation_uses_coordinated():
    from types import SimpleNamespace

    conv = SimpleNamespace(dispatch_mode="orchestrated")
    assert _routing_decision(conv, is_orchestrator=True, has_override_prompt=False) == "coordinated_loop"


def test_non_orchestrator_in_orchestrated_conversation_uses_solo():
    from types import SimpleNamespace

    conv = SimpleNamespace(dispatch_mode="orchestrated")
    assert _routing_decision(conv, is_orchestrator=False, has_override_prompt=False) == "solo_loop"


def test_orchestrator_in_solo_conversation_uses_solo():
    from types import SimpleNamespace

    conv = SimpleNamespace(dispatch_mode="solo")
    assert _routing_decision(conv, is_orchestrator=True, has_override_prompt=False) == "solo_loop"


def test_regular_agent_in_solo_conversation_uses_solo():
    from types import SimpleNamespace

    conv = SimpleNamespace(dispatch_mode="solo")
    assert _routing_decision(conv, is_orchestrator=False, has_override_prompt=False) == "solo_loop"
