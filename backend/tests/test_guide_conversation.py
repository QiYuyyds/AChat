"""Tests for guide-mode conversation support (M3).

Covers:
  - guide conversation creation (agent_ids override, title, sandbox-only)
  - guide conversations excluded from list_conversations
  - guide conversations cannot be deleted
  - GuideSideEffectEvent schema
"""

import pytest
import pytest_asyncio

from app.schemas.events import GuideSideEffectEvent
from app.services import conversation_service as cs
from app.utils.clock import now_ms

# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def guide_agent(db):
    """Seed the guide agent (ag_guide_builtin) so conversation creation works."""
    from app.db.engine import get_db
    from app.db.models import Agent

    now = now_ms()
    async with get_db() as session:
        guide = Agent(
            id="ag_guide_builtin",
            name="小A",
            avatar="🅰️",
            description="Guide agent",
            system_prompt="You are the guide agent 小A.",
            adapter_name="custom",
            model_provider="deepseek",
            model_id="deepseek-v4-flash",
            is_builtin=True,
            is_orchestrator=False,
            is_guide=True,
            supports_vision=False,
            created_at=now,
            user_id=None,
        )
        guide.capabilities_list = []
        guide.tool_names_list = [
            "manage_agents",
            "manage_skills",
            "manage_mcp",
            "manage_documents",
            "manage_memory",
            "manage_profile",
            "manage_conversations",
        ]
        session.add(guide)


# ─── Create guide conversation ───────────────────────────────────────────────


async def test_create_guide_conversation(db, agents, guide_agent):
    """Guide mode overrides agent_ids to ag_guide_builtin and forces sandbox."""
    conv = await cs.create_conversation(
        mode="guide",
        agent_ids=["anything"],  # should be overridden
        user_id="test_user_1",
    )
    assert conv.mode == "guide"
    assert conv.agent_ids == ["ag_guide_builtin"]
    assert conv.title == "小A"
    assert conv.workspace_mode == "sandbox"
    assert conv.workspace_bound_path is None


async def test_create_guide_conversation_ignores_bound_path(db, agents, guide_agent):
    """Guide mode ignores bound_path even when provided."""
    conv = await cs.create_conversation(
        mode="guide",
        agent_ids=["ag_guide_builtin"],
        bound_path="/some/path",
        user_id="test_user_1",
    )
    assert conv.workspace_mode == "sandbox"
    assert conv.workspace_bound_path is None


# ─── List filtering ─────────────────────────────────────────────────────────


async def test_list_excludes_guide_conversations(db, agents, guide_agent):
    """Guide conversations should not appear in list_conversations."""
    normal = await cs.create_conversation(
        mode="single",
        agent_ids=[agents["alice"]],
        user_id="test_user_1",
    )
    guide = await cs.create_conversation(
        mode="guide",
        agent_ids=["ag_guide_builtin"],
        user_id="test_user_1",
    )

    listed = await cs.list_conversations(user_id="test_user_1")
    listed_ids = {c.id for c in listed}
    assert normal.id in listed_ids
    assert guide.id not in listed_ids


# ─── Delete rejection ────────────────────────────────────────────────────────


async def test_delete_guide_conversation_rejected(db, agents, guide_agent):
    """Deleting a guide conversation should raise ValueError."""
    conv = await cs.create_conversation(
        mode="guide",
        agent_ids=["ag_guide_builtin"],
        user_id="test_user_1",
    )
    with pytest.raises(ValueError, match="cannot be deleted"):
        await cs.delete_conversation(conv.id)


async def test_delete_normal_conversation_still_works(db, agents):
    """Non-guide conversations should still be deletable."""
    conv = await cs.create_conversation(
        mode="single",
        agent_ids=[agents["alice"]],
        user_id="test_user_1",
    )
    await cs.delete_conversation(conv.id)
    with pytest.raises(ValueError, match="not found"):
        await cs.get_conversation(conv.id)


# ─── GuideSideEffectEvent schema ─────────────────────────────────────────────


def test_guide_side_effect_event_schema():
    """GuideSideEffectEvent carries target, action, and optional payload."""
    evt = GuideSideEffectEvent(
        conversation_id="conv_1",
        timestamp=now_ms(),
        target="agents",
        action="create",
    )
    assert evt.type == "guide_side_effect"
    assert evt.target == "agents"
    assert evt.action == "create"
    assert evt.payload is None


def test_guide_side_effect_event_with_payload():
    """GuideSideEffectEvent accepts an optional payload dict."""
    evt = GuideSideEffectEvent(
        conversation_id="conv_1",
        timestamp=now_ms(),
        target="memory",
        action="optimize",
        payload={"deleted": 5, "merged": 2},
    )
    assert evt.payload == {"deleted": 5, "merged": 2}


def test_guide_side_effect_event_invalid_target():
    """GuideSideEffectEvent rejects invalid target values."""
    with pytest.raises(ValueError):
        GuideSideEffectEvent(
            conversation_id="conv_1",
            timestamp=now_ms(),
            target="invalid_target",
            action="create",
        )


def test_guide_side_effect_event_invalid_action():
    """GuideSideEffectEvent rejects invalid action values."""
    with pytest.raises(ValueError):
        GuideSideEffectEvent(
            conversation_id="conv_1",
            timestamp=now_ms(),
            target="agents",
            action="invalid_action",
        )


# ─── EventBus publish with user_id ──────────────────────────────────────────


async def test_guide_side_effect_event_bus_user_filtering():
    """EventBus publishes GuideSideEffectEvent and filters by user_id."""
    import asyncio

    from app.services.event_bus import event_bus

    async with event_bus.subscribe(user_id="user_1") as queue:
        evt = GuideSideEffectEvent(
            conversation_id="conv_1",
            timestamp=now_ms(),
            target="agents",
            action="create",
        )
        event_bus.publish(evt, user_id="user_1")

        # Give the event loop a tick to deliver
        await asyncio.sleep(0.05)

        assert not queue.empty()
        received = queue.get_nowait()
        assert received.target == "agents"


async def test_guide_side_effect_event_bus_filters_other_user():
    """Events published for user_2 should not be delivered to user_1 subscriber."""
    import asyncio

    from app.services.event_bus import event_bus

    async with event_bus.subscribe(user_id="user_1") as queue:
        evt = GuideSideEffectEvent(
            conversation_id="conv_1",
            timestamp=now_ms(),
            target="skills",
            action="delete",
        )
        event_bus.publish(evt, user_id="user_2")

        await asyncio.sleep(0.05)

        assert queue.empty()
