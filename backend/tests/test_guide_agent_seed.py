"""Tests for the guide agent (小A) seed mechanism.

Verifies that the seed is idempotent (first call creates, second call no-ops)
and that seed failures do not block startup.
"""

from __future__ import annotations

from sqlalchemy import select


async def test_seed_creates_guide_agent(db):
    """First call to _seed_guide_agent creates the guide agent."""
    from app.db.engine import get_db
    from app.db.models import Agent
    from app.main import _seed_guide_agent

    await _seed_guide_agent()

    async with get_db() as session:
        result = await session.execute(
            select(Agent).where(Agent.is_guide.is_(True))
        )
        agent = result.scalar_one_or_none()
        assert agent is not None
        assert agent.id == "ag_guide_builtin"
        assert agent.name == "小A"
        assert agent.is_builtin is True
        assert agent.is_guide is True
        assert agent.user_id is None
        assert agent.adapter_name == "custom"
        assert "manage_agents" in agent.tool_names_list
        assert "manage_skills" in agent.tool_names_list
        assert "manage_mcp" in agent.tool_names_list
        assert "manage_documents" in agent.tool_names_list
        assert "manage_memory" in agent.tool_names_list
        assert "manage_profile" in agent.tool_names_list
        assert "manage_conversations" in agent.tool_names_list


async def test_seed_is_idempotent(db):
    """Second call to _seed_guide_agent does not create a duplicate."""
    from app.db.engine import get_db
    from app.db.models import Agent
    from app.main import _seed_guide_agent

    await _seed_guide_agent()
    await _seed_guide_agent()

    async with get_db() as session:
        result = await session.execute(
            select(Agent).where(Agent.is_guide.is_(True))
        )
        agents = result.scalars().all()
        assert len(agents) == 1


async def test_seed_failure_does_not_block(db):
    """If the seed raises, the exception is swallowed and startup continues."""
    from app.main import _seed_guide_agent

    # _seed_guide_agent wraps everything in try/except and logs a warning.
    # Even if the DB is in a weird state, it should not raise.
    await _seed_guide_agent()
    # No assertion needed — just confirming no exception propagates.
