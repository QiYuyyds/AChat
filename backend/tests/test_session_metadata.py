"""Unit tests for session metadata blunting and injection (Task 4.5).

Verifies:
- _blunt_metadata produces correct locale/offset/time_bucket tags
- Static metadata (language/timezone) appears in system_prompt, not in user message
- Dynamic metadata (current_time) appears in user message tail, not in system_prompt
- CLI adapters do not get metadata injected
- Exact timestamp does not appear in prompt (blunted form only)
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest_asyncio

# ─── _blunt_metadata unit tests ──────────────────────────────────────────────


def test_blunt_metadata_morning():
    """05:00-11:59 → Morning."""
    from app.services.agent_runner import _blunt_metadata

    dt = datetime(2026, 7, 5, 8, 30, 0)  # Sunday 08:30
    lang, tz, loc, bucket = _blunt_metadata("zh-CN", "GMT+8", "Beijing", dt)
    assert lang == "zh-CN"
    assert tz == "GMT+8"
    assert loc == "Beijing"
    assert bucket == "2026年7月5日_Sunday_Morning"


def test_blunt_metadata_afternoon():
    """12:00-17:59 → Afternoon."""
    from app.services.agent_runner import _blunt_metadata

    dt = datetime(2026, 7, 5, 14, 0, 0)  # Sunday 14:00
    _, _, _, bucket = _blunt_metadata("zh-CN", "GMT+8", "Beijing", dt)
    assert bucket == "2026年7月5日_Sunday_Afternoon"


def test_blunt_metadata_evening_boundary():
    """18:00 → Evening (boundary check)."""
    from app.services.agent_runner import _blunt_metadata

    dt = datetime(2026, 7, 5, 18, 0, 0)  # Sunday 18:00
    _, _, _, bucket = _blunt_metadata("zh-CN", "GMT+8", "Beijing", dt)
    assert bucket == "2026年7月5日_Sunday_Evening"


def test_blunt_metadata_late_night_boundary():
    """23:00 → LateNight (boundary check)."""
    from app.services.agent_runner import _blunt_metadata

    dt = datetime(2026, 7, 5, 23, 0, 0)  # Sunday 23:00
    _, _, _, bucket = _blunt_metadata("zh-CN", "GMT+8", "Beijing", dt)
    assert bucket == "2026年7月5日_Sunday_LateNight"


def test_blunt_metadata_late_night_early_hours():
    """00:00-04:59 → LateNight."""
    from app.services.agent_runner import _blunt_metadata

    dt = datetime(2026, 7, 5, 2, 0, 0)  # Sunday 02:00
    _, _, _, bucket = _blunt_metadata("zh-CN", "GMT+8", "Beijing", dt)
    assert bucket == "2026年7月5日_Sunday_LateNight"


def test_blunt_metadata_morning_boundary():
    """05:00 → Morning (boundary check)."""
    from app.services.agent_runner import _blunt_metadata

    dt = datetime(2026, 7, 5, 5, 0, 0)  # Sunday 05:00
    _, _, _, bucket = _blunt_metadata("zh-CN", "GMT+8", "Beijing", dt)
    assert bucket == "2026年7月5日_Sunday_Morning"


def test_blunt_metadata_weekday():
    """Weekday is correctly extracted."""
    from app.services.agent_runner import _blunt_metadata

    dt = datetime(2026, 7, 6, 10, 0, 0)  # Monday 10:00
    _, _, _, bucket = _blunt_metadata("en-US", "GMT-5", "New York", dt)
    assert bucket == "2026年7月6日_Monday_Morning"


def test_blunt_metadata_no_exact_timestamp():
    """The blunted form does not contain the exact timestamp (hour/minute/second)."""
    from app.services.agent_runner import _blunt_metadata

    dt = datetime(2026, 7, 5, 11, 14, 58)
    _, _, _, bucket = _blunt_metadata("zh-CN", "GMT+8", "Beijing", dt)
    # The bucket should contain the date and Sunday_Morning,
    # but NOT the exact hour/minute/second values.
    assert "2026年7月5日" in bucket
    assert "Sunday_Morning" in bucket
    assert ":" not in bucket  # no time separator
    # No minute/second digits leaked (hour 11 maps to Morning, that's fine)
    assert "14" not in bucket  # minute
    assert "58" not in bucket  # second


# ─── build_adapter_input injection tests ─────────────────────────────────────


@pytest_asyncio.fixture
async def custom_agent_setup(db, tmp_path):
    """Create a custom (SDK) agent, conversation, and workspace."""
    from app.db.engine import get_db
    from app.db.models import Agent, Conversation, Workspace
    from app.utils.clock import now_ms
    from app.utils.ids import new_conversation_id, new_workspace_id

    ws_root = tmp_path / "ws"
    ws_root.mkdir(parents=True, exist_ok=True)
    conv_id = new_conversation_id()
    now = now_ms()

    async with get_db() as session:
        agent = Agent(
            id="ag_meta_custom",
            name="MetaCustom",
            avatar="M",
            description="metadata test agent",
            system_prompt="You are a test agent.",
            adapter_name="custom",
            is_builtin=False,
            is_orchestrator=False,
            supports_vision=False,
            model_provider="openai",
            model_id="gpt-4",
            created_at=now,
        )
        agent.capabilities_list = []
        agent.tool_names_list = []

        conv = Conversation(
            id=conv_id,
            title="Meta Test",
            mode="single",
            archived=False,
            fs_write_approval_mode="auto",
            created_at=now,
            updated_at=now,
        )
        conv.agent_ids_list = [agent.id]
        conv.pinned_message_ids_list = []
        conv.bookmarked_message_ids_list = []

        session.add(agent)
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

    return {
        "agent_id": "ag_meta_custom",
        "conversation_id": conv_id,
        "workspace_root": str(ws_root),
    }


@pytest_asyncio.fixture
async def cli_agent_setup(db, tmp_path):
    """Create a CLI (claude-code) agent, conversation, and workspace."""
    from app.db.engine import get_db
    from app.db.models import Agent, Conversation, Workspace
    from app.utils.clock import now_ms
    from app.utils.ids import new_conversation_id, new_workspace_id

    ws_root = tmp_path / "ws_cli"
    ws_root.mkdir(parents=True, exist_ok=True)
    conv_id = new_conversation_id()
    now = now_ms()

    async with get_db() as session:
        agent = Agent(
            id="ag_meta_cli",
            name="CliAgent",
            avatar="C",
            description="CLI test agent",
            system_prompt="You are a CLI agent.",
            adapter_name="claude-code",
            is_builtin=False,
            is_orchestrator=False,
            supports_vision=False,
            created_at=now,
        )
        agent.capabilities_list = []
        agent.tool_names_list = []

        conv = Conversation(
            id=conv_id,
            title="CLI Meta Test",
            mode="single",
            archived=False,
            fs_write_approval_mode="auto",
            created_at=now,
            updated_at=now,
        )
        conv.agent_ids_list = [agent.id]
        conv.pinned_message_ids_list = []
        conv.bookmarked_message_ids_list = []

        session.add(agent)
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

    return {
        "agent_id": "ag_meta_cli",
        "conversation_id": conv_id,
        "workspace_root": str(ws_root),
    }


async def test_sdk_static_metadata_in_system_prompt(db, custom_agent_setup, monkeypatch):
    """SDK agent: language/timezone/location appear in system_prompt, not in user message."""
    from app.services import agent_runner
    from app.services.prompt_assembler import CHAT_SCHEMA, RuntimeContext

    # Mock assembler with empty context (isolate metadata injection)
    test_ctx = RuntimeContext(schema=CHAT_SCHEMA, filled=[])
    mock_assembler = MagicMock()
    mock_assembler.assemble = AsyncMock(return_value=test_ctx)
    monkeypatch.setattr(agent_runner, "_get_prompt_assembler", lambda: mock_assembler)
    # Mock IP geolocation to return a fixed city (avoids real network call)
    monkeypatch.setattr(agent_runner, "_detect_location", AsyncMock(return_value="重庆"))
    # Reset module-level cache so the mock is used
    agent_runner._cached_location = None

    from sqlalchemy import select

    from app.db.engine import get_db
    from app.db.models import Agent, Workspace
    from app.services.agent_runner import RunArgs, build_adapter_input

    async with get_db() as session:
        agent = await session.get(Agent, custom_agent_setup["agent_id"])
        workspace = (
            await session.execute(
                select(Workspace).where(
                    Workspace.conversation_id == custom_agent_setup["conversation_id"]
                )
            )
        ).scalars().first()

    args = RunArgs(
        agent_id=custom_agent_setup["agent_id"],
        conversation_id=custom_agent_setup["conversation_id"],
        trigger_message_id="msg_test",
    )

    result = await build_adapter_input(
        args=args,
        agent=agent,
        run_id="run_test",
        prompt="Hello world",
        workspace=workspace,
        tool_names=[],
        system_prompt_override=None,
        attachments=[],
    )

    # Static metadata (language/timezone/location) in system_prompt
    assert "language=" in result.system_prompt
    assert "zh-CN" in result.system_prompt
    assert "timezone=" in result.system_prompt
    assert "GMT+8" in result.system_prompt
    assert "location=" in result.system_prompt
    assert "重庆" in result.system_prompt

    # Static metadata NOT in user message (prompt)
    assert "language=" not in result.prompt
    assert "timezone=" not in result.prompt
    assert "location=" not in result.prompt


async def test_sdk_dynamic_metadata_in_user_tail(db, custom_agent_setup, monkeypatch):
    """SDK agent: current_time bucket appears in user message tail, not in system_prompt."""
    from app.services import agent_runner
    from app.services.prompt_assembler import CHAT_SCHEMA, RuntimeContext

    test_ctx = RuntimeContext(schema=CHAT_SCHEMA, filled=[])
    mock_assembler = MagicMock()
    mock_assembler.assemble = AsyncMock(return_value=test_ctx)
    monkeypatch.setattr(agent_runner, "_get_prompt_assembler", lambda: mock_assembler)
    # Mock IP geolocation to return a fixed city (avoids real network call)
    monkeypatch.setattr(agent_runner, "_detect_location", AsyncMock(return_value="重庆"))
    # Reset module-level cache so the mock is used
    agent_runner._cached_location = None

    from sqlalchemy import select

    from app.db.engine import get_db
    from app.db.models import Agent, Workspace
    from app.services.agent_runner import RunArgs, build_adapter_input

    async with get_db() as session:
        agent = await session.get(Agent, custom_agent_setup["agent_id"])
        workspace = (
            await session.execute(
                select(Workspace).where(
                    Workspace.conversation_id == custom_agent_setup["conversation_id"]
                )
            )
        ).scalars().first()

    args = RunArgs(
        agent_id=custom_agent_setup["agent_id"],
        conversation_id=custom_agent_setup["conversation_id"],
        trigger_message_id="msg_test",
    )

    result = await build_adapter_input(
        args=args,
        agent=agent,
        run_id="run_test",
        prompt="Hello world",
        workspace=workspace,
        tool_names=[],
        system_prompt_override=None,
        attachments=[],
    )

    # Dynamic metadata (current_time) in user message (prompt)
    assert "[current_time:" in result.prompt
    # Should match pattern like "2026年7月5日_Sunday_Morning"
    import re
    match = re.search(r"\[current_time: (.+?)\]", result.prompt)
    assert match is not None, f"current_time bucket not found in prompt: {result.prompt}"
    bucket = match.group(1)
    # Verify it contains a date prefix and weekday_period suffix
    # Format: {date}_{Weekday}_{Period}
    parts = bucket.split("_")
    assert len(parts) == 3, f"Expected 3 parts (date/weekday/period), got: {parts}"
    date_part, weekday, period = parts
    assert "年" in date_part and "月" in date_part and "日" in date_part
    assert weekday in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    assert period in ["Morning", "Afternoon", "Evening", "LateNight"]

    # Dynamic metadata NOT in system_prompt
    assert "[current_time:" not in result.system_prompt
    assert bucket not in result.system_prompt

    # Original prompt is preserved
    assert "Hello world" in result.prompt

    # current_time is at the tail (after "Hello world")
    assert result.prompt.index("Hello world") < result.prompt.index("[current_time:")


async def test_cli_agent_no_metadata(db, cli_agent_setup, monkeypatch):
    """CLI agent: no session metadata injected."""
    from app.services import agent_runner

    # Mock assembler (should not be called for CLI)
    mock_assembler = MagicMock()
    mock_assembler.assemble = AsyncMock()
    monkeypatch.setattr(agent_runner, "_get_prompt_assembler", lambda: mock_assembler)

    from sqlalchemy import select

    from app.db.engine import get_db
    from app.db.models import Agent, Workspace
    from app.services.agent_runner import RunArgs, build_adapter_input

    async with get_db() as session:
        agent = await session.get(Agent, cli_agent_setup["agent_id"])
        workspace = (
            await session.execute(
                select(Workspace).where(
                    Workspace.conversation_id == cli_agent_setup["conversation_id"]
                )
            )
        ).scalars().first()

    args = RunArgs(
        agent_id=cli_agent_setup["agent_id"],
        conversation_id=cli_agent_setup["conversation_id"],
        trigger_message_id="msg_test",
    )

    result = await build_adapter_input(
        args=args,
        agent=agent,
        run_id="run_test",
        prompt="Hello CLI",
        workspace=workspace,
        tool_names=[],
        system_prompt_override=None,
        attachments=[],
    )

    # No metadata in system_prompt
    assert "language=" not in result.system_prompt
    assert "timezone=" not in result.system_prompt
    assert "location=" not in result.system_prompt
    assert "[session]" not in result.system_prompt

    # No metadata in prompt
    assert "[current_time:" not in result.prompt
    assert "language=" not in result.prompt
