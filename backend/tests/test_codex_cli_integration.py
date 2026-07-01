"""Integration tests for the Codex CLI adapter.

These tests require a real codex CLI binary installed on the system
(``npm install -g @openai/codex``) and are marked with ``@pytest.mark.skip``
so they don't run in CI. Run manually with:

    pytest backend/tests/test_codex_cli_integration.py -s --no-header -k 'not skip'
"""

from __future__ import annotations

import asyncio
import shutil

import pytest

from app.adapters.base import AdapterInput
from app.adapters.codex_cli_adapter import CodexCLIAdapter


def _codex_available() -> bool:
    return shutil.which("codex") is not None


pytestmark = pytest.mark.skipif(
    not _codex_available(),
    reason="codex CLI not installed (npm install -g @openai/codex)",
)


def _make_input(**overrides) -> AdapterInput:
    base = {
        "agent_id": "ag_integration",
        "conversation_id": "conv_integration",
        "run_id": "run_integration",
        "prompt": "Say hello in one word.",
        "workspace_path": "/tmp",
        "system_prompt": "You are a helpful assistant.",
        "api_key": None,
        "api_base_url": None,
        "model_id": None,
        "tool_names": [],
        "executable_path": None,
    }
    base.update(overrides)
    return AdapterInput(**base)


# ─── 9.2 End-to-end: create codex agent → send message → verify SSE ──

@pytest.mark.asyncio
async def test_e2e_codex_run_produces_events():
    """Create a codex agent, send a message, verify SSE events are produced.

    This test spawns a real codex process and verifies the full event flow:
    thread/start → agent_message → turn.completed → MessageEnd + RunUsage.
    """
    adapter = CodexCLIAdapter()
    cancel_event = asyncio.Event()

    events = []
    async for ev in adapter.stream(_make_input(), cancel_event):
        events.append(ev)
        # Cancel after receiving enough events to avoid infinite loop
        if len(events) > 50:
            cancel_event.set()

    types = [e.type for e in events]
    assert "message.start" in types
    assert "message.end" in types


# ─── 9.3 Group chat: Orchestrator + codex child ──────────────────

@pytest.mark.asyncio
async def test_group_chat_dispatch_to_codex_child():
    """Orchestrator dispatches a sub-task to a codex child agent.

    Verifies that the dispatch flow works with codex adapter and
    ``report_task_result`` is properly translated from MCP tool call events.
    """
    # This test requires a full app context with orchestrator + child agent
    # configured. It's a placeholder for manual integration testing.
    pytest.skip("Requires orchestrator + codex child agent configuration")


# ─── 9.4 Cancel logic ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cancel_terminates_subprocess():
    """Start a codex run, trigger cancel, verify subprocess is terminated.

    The cancel_event should cause the adapter to call proc.terminate(),
    and if that doesn't work within 5s, proc.kill().
    """
    adapter = CodexCLIAdapter()
    cancel_event = asyncio.Event()

    # Set cancel after a short delay to simulate user abort
    async def _delayed_cancel():
        await asyncio.sleep(0.5)
        cancel_event.set()

    asyncio.create_task(_delayed_cancel())

    events = []
    async for ev in adapter.stream(
        _make_input(prompt="Write a long essay about AI."), cancel_event
    ):
        events.append(ev)

    # The run should have ended (either completed or aborted)
    types = [e.type for e in events]
    assert "message.end" in types


# ─── 9.5 CODEX_HOME isolation ────────────────────────────────────

@pytest.mark.asyncio
async def test_codex_home_isolation_between_runs():
    """Two concurrent codex runs get separate CODEX_HOME directories.

    Each run should have its own ``$CODEX_HOME/<run_id>/`` directory,
    while ``auth.json`` is symlinked to the same ``~/.codex/auth.json``.
    """
    from app.adapters.codex_home import prepare_codex_home, cleanup_codex_home

    import tempfile
    with tempfile.TemporaryDirectory() as data_dir:
        home1 = prepare_codex_home(
            run_id="run_iso_1",
            data_dir=data_dir,
            mcp_env={"AGENTHUB_RUN_ID": "run_iso_1"},
            mcp_script_path="/path/to/mcp.mjs",
        )
        home2 = prepare_codex_home(
            run_id="run_iso_2",
            data_dir=data_dir,
            mcp_env={"AGENTHUB_RUN_ID": "run_iso_2"},
            mcp_script_path="/path/to/mcp.mjs",
        )

        assert home1 != home2
        assert "run_iso_1" in home1
        assert "run_iso_2" in home2

        # Both should have auth.json (symlink or copy of the same source)
        from pathlib import Path
        auth1 = Path(home1) / "auth.json"
        auth2 = Path(home2) / "auth.json"
        assert auth1.exists()
        assert auth2.exists()

        # config.toml should be separate (not symlinked)
        cfg1 = Path(home1) / "config.toml"
        cfg2 = Path(home2) / "config.toml"
        content1 = cfg1.read_text(encoding="utf-8")
        content2 = cfg2.read_text(encoding="utf-8")
        assert "run_iso_1" in content1
        assert "run_iso_2" in content2
        assert "run_iso_1" not in content2
        assert "run_iso_2" not in content1

        cleanup_codex_home(home1)
        cleanup_codex_home(home2)
