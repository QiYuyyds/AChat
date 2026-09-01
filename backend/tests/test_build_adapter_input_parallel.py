"""Tests for the parallelized ``build_adapter_input`` context pipeline.

speed-up-first-token-latency tasks 3.1/3.3/3.4:
- ``build_history_for`` and ``PromptAssembler.assemble`` run concurrently via
  ``asyncio.gather(..., return_exceptions=True)``;
- degradation semantics unchanged: history failure → empty history, assemble
  failure → no enrichment, and neither side affects the other's result;
- system prompt composition order is unchanged (base → enriched → [session]),
  keeping the ``[cache-debug] sys_prompt_hash`` byte-stream stable;
- full-message token estimation runs through ``asyncio.to_thread`` without
  changing results.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest_asyncio

from app.db.engine import get_db
from app.db.models import Agent, Conversation, Message, ModelProfile, Workspace
from app.services.agent_runner import RunArgs, build_adapter_input
from app.utils.clock import now_ms
from app.utils.ids import new_message_id

ENRICHED_STATIC = "ENRICHED-STATIC-BLOCK"
DYNAMIC_PREFIX = "DYNAMIC-PREFIX"


@pytest_asyncio.fixture
async def sdk_agent_setup(db, tmp_path):
    """SDK (custom) agent + conversation + workspace + default ModelProfile.

    Follows the CURRENT model schema (Agent has no model_provider/model_id;
    ModelProfile is separate; Conversation has no user_id).
    """
    from app.utils.ids import new_conversation_id, new_workspace_id

    ws_root = tmp_path / "ws"
    ws_root.mkdir(parents=True, exist_ok=True)
    conv_id = new_conversation_id()
    now = now_ms()

    async with get_db() as session:
        agent = Agent(
            id="ag_parallel_test",
            name="ParallelAgent",
            avatar="P",
            description="parallel build_adapter_input test agent",
            system_prompt="You are a test agent.",
            adapter_name="custom",
            is_builtin=False,
            is_orchestrator=False,
            created_at=now,
        )
        agent.capabilities_list = []
        agent.tool_names_list = []

        conv = Conversation(
            id=conv_id,
            title="Parallel Test",
            mode="single",
            archived=False,
            fs_write_approval_mode="auto",
            created_at=now,
            updated_at=now,
        )
        conv.agent_ids_list = [agent.id]
        conv.pinned_message_ids_list = []
        conv.bookmarked_message_ids_list = []

        profile = ModelProfile(
            id="mp_parallel_test",
            name="Parallel Test Profile",
            provider="deepseek",
            model_id="deepseek-chat",
            is_default=True,
            supports_vision=False,
            created_at=now,
            updated_at=now,
        )

        session.add(agent)
        session.add(conv)
        session.add(profile)
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
        "agent_id": "ag_parallel_test",
        "conversation_id": conv_id,
        "workspace_root": str(ws_root),
    }


async def _add_history_messages(conversation_id: str, agent_id: str, rounds: int = 3) -> None:
    """Insert completed user/agent message pairs with text parts."""
    async with get_db() as session:
        for i in range(rounds):
            base = now_ms() + i
            session.add(Message(
                id=new_message_id(),
                conversation_id=conversation_id,
                role="user",
                parts=[{"type": "text", "content": f"user question {i}"}],
                status="complete",
                created_at=base,
            ))
            session.add(Message(
                id=new_message_id(),
                conversation_id=conversation_id,
                role="agent",
                agent_id=agent_id,
                parts=[{"type": "text", "content": f"agent answer {i}"}],
                status="complete",
                created_at=base + 1,
            ))
        await session.commit()


def _mock_assembler_ctx():
    ctx = MagicMock()
    ctx.render_static = MagicMock(return_value=ENRICHED_STATIC)
    ctx.render_dynamic = MagicMock(return_value=DYNAMIC_PREFIX)
    ctx.filled = []
    return ctx


def _mock_assembler():
    assembler = MagicMock()
    assembler.assemble = AsyncMock(return_value=_mock_assembler_ctx())
    return assembler


async def _build_input(sdk_agent_setup, monkeypatch, assembler, workspace=None):
    from sqlalchemy import select

    from app.services import agent_runner

    monkeypatch.setattr(agent_runner, "_get_prompt_assembler", lambda: assembler)
    # Cache-only location: warm the cache so [session] gets a real city and no
    # background probe is scheduled mid-test.
    monkeypatch.setattr(agent_runner, "_cached_location", "测试市")

    async with get_db() as session:
        agent = await session.get(Agent, sdk_agent_setup["agent_id"])
        if workspace is None:
            workspace = (
                await session.execute(
                    select(Workspace).where(
                        Workspace.conversation_id == sdk_agent_setup["conversation_id"]
                    )
                )
            ).scalars().first()

    args = RunArgs(
        agent_id=sdk_agent_setup["agent_id"],
        conversation_id=sdk_agent_setup["conversation_id"],
        trigger_message_id="msg_parallel_test",
    )
    return await build_adapter_input(
        args=args,
        agent=agent,
        run_id="run_parallel_test",
        prompt="Hello there",
        workspace=workspace,
        tool_names=[],
        system_prompt_override=None,
        attachments=[],
    )


# ─── both sides succeed ──────────────────────────────────────────────────────


async def test_history_and_assemble_both_produce(sdk_agent_setup, monkeypatch):
    """Long-ish history: history rebuild and assemble both produce their output."""
    await _add_history_messages(
        sdk_agent_setup["conversation_id"], sdk_agent_setup["agent_id"], rounds=3
    )

    result = await _build_input(sdk_agent_setup, monkeypatch, _mock_assembler())

    # history: serialized user + agent turns present
    history_texts = " ".join(str(m.get("content")) for m in (result.history or []))
    assert "user question 0" in history_texts
    assert "agent answer 2" in history_texts

    # assemble: static block in the system prompt, dynamic prefix on the prompt
    assert ENRICHED_STATIC in result.system_prompt
    assert result.prompt.startswith(DYNAMIC_PREFIX)


async def test_system_prompt_composition_order_unchanged(sdk_agent_setup, monkeypatch):
    """Base prompt → enriched block → [session] — same byte order as the
    sequential implementation, so sys_prompt_hash stays stable."""
    result = await _build_input(sdk_agent_setup, monkeypatch, _mock_assembler())

    sp = result.system_prompt
    assert sp.index("You are a test agent.") < sp.index(ENRICHED_STATIC)
    assert sp.index(ENRICHED_STATIC) < sp.index("[session]")
    assert "location=" in sp  # [session] block format unchanged

    # user prompt: dynamic prefix then prompt text, time bucket in the tail
    assert result.prompt.index(DYNAMIC_PREFIX) < result.prompt.index("Hello there")
    assert result.prompt.rstrip().endswith("]")


# ─── degradation semantics (gather return_exceptions) ────────────────────────


async def test_history_failure_degrades_assemble_unaffected(sdk_agent_setup, monkeypatch):
    """build_history_for raising → empty history; assemble result still applied."""
    from app.services import agent_runner

    async def boom(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("history db exploded")

    monkeypatch.setattr(agent_runner, "build_history_for", boom)

    result = await _build_input(sdk_agent_setup, monkeypatch, _mock_assembler())

    assert result.history is None  # degraded to no-history
    assert ENRICHED_STATIC in result.system_prompt  # assemble unaffected
    assert result.prompt.startswith(DYNAMIC_PREFIX)


async def test_assemble_failure_degrades_history_unaffected(sdk_agent_setup, monkeypatch):
    """assembler.assemble raising → no enrichment; history result still applied."""
    await _add_history_messages(
        sdk_agent_setup["conversation_id"], sdk_agent_setup["agent_id"], rounds=2
    )
    assembler = _mock_assembler()
    assembler.assemble = AsyncMock(side_effect=RuntimeError("retrieval down"))

    result = await _build_input(sdk_agent_setup, monkeypatch, assembler)

    assert ENRICHED_STATIC not in result.system_prompt  # no enrichment
    assert result.prompt.startswith("Hello there")  # no dynamic prefix injected
    history_texts = " ".join(str(m.get("content")) for m in (result.history or []))
    assert "user question 0" in history_texts  # history unaffected


async def test_both_failing_degrade_independently(sdk_agent_setup, monkeypatch):
    """Both sides raising degrades to empty history + no enrichment, no crash."""
    from app.services import agent_runner

    async def boom(*args, **kwargs):  # noqa: ARG001
        raise RuntimeError("everything exploded")

    monkeypatch.setattr(agent_runner, "build_history_for", boom)
    assembler = _mock_assembler()
    assembler.assemble = AsyncMock(side_effect=RuntimeError("assemble down"))

    result = await _build_input(sdk_agent_setup, monkeypatch, assembler)

    assert result.history is None
    assert ENRICHED_STATIC not in result.system_prompt
    assert "[session]" in result.system_prompt  # session metadata still injected
