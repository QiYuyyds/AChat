"""Unit tests for checkpoint_service: save/load/list/clean."""

import pytest

from app.services.checkpoint_service import (
    MAX_CHECKPOINTS_PER_RUN,
    clean_run_checkpoints,
    list_checkpoints,
    load_latest_checkpoint,
    save_checkpoint,
)

SAMPLE_MESSAGES = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Hello"},
]


async def _seed_run(run_id: str = "run_test", agent_id: str = "ag_alice") -> None:
    """Insert a minimal AgentRun row so the FK on checkpoints is satisfied."""
    from app.db.engine import get_db
    from app.db.models import AgentRun, Conversation, Workspace
    from app.utils.clock import now_ms

    now = now_ms()
    async with get_db() as db:
        conv = Conversation(
            id="conv_test",
            title="test",
            mode="single",
            agent_ids=["ag_alice"],
            created_at=now,
            updated_at=now,
        )
        conv.agent_ids_list = ["ag_alice"]
        db.add(conv)
        ws = Workspace(
            id="ws_test",
            conversation_id="conv_test",
            root_path="/tmp/test",
            mode="sandbox",
            created_at=now,
        )
        db.add(ws)
        db.add(
            AgentRun(
                id=run_id,
                conversation_id="conv_test",
                agent_id=agent_id,
                status="running",
                started_at=now,
            )
        )


@pytest.mark.asyncio
async def test_save_and_load_checkpoint(db):
    """save_checkpoint persists; load_latest_checkpoint returns it."""
    await _seed_run("run_save_load")
    cp = await save_checkpoint("run_save_load", turn_number=1, messages=SAMPLE_MESSAGES)
    assert cp is not None
    assert cp.run_id == "run_save_load"
    assert cp.turn_number == 1

    latest = await load_latest_checkpoint("run_save_load")
    assert latest is not None
    assert latest.turn_number == 1


@pytest.mark.asyncio
async def test_load_latest_returns_highest_turn(db):
    """load_latest_checkpoint returns the highest turn_number."""
    await _seed_run("run_latest")
    await save_checkpoint("run_latest", turn_number=1, messages=SAMPLE_MESSAGES)
    await save_checkpoint("run_latest", turn_number=2, messages=SAMPLE_MESSAGES)
    await save_checkpoint("run_latest", turn_number=3, messages=SAMPLE_MESSAGES)

    latest = await load_latest_checkpoint("run_latest")
    assert latest is not None
    assert latest.turn_number == 3


@pytest.mark.asyncio
async def test_list_checkpoints_descending(db):
    """list_checkpoints returns checkpoints ordered by turn_number descending."""
    await _seed_run("run_list")
    await save_checkpoint("run_list", turn_number=1, messages=SAMPLE_MESSAGES)
    await save_checkpoint("run_list", turn_number=2, messages=SAMPLE_MESSAGES)

    checkpoints = await list_checkpoints("run_list")
    assert len(checkpoints) == 2
    assert checkpoints[0].turn_number == 2
    assert checkpoints[1].turn_number == 1


@pytest.mark.asyncio
async def test_clean_old_checkpoints_evicts_oldest(db):
    """Saving more than MAX_CHECKPOINTS_PER_RUN evicts the oldest."""
    await _seed_run("run_clean")
    for turn in range(1, MAX_CHECKPOINTS_PER_RUN + 2):
        await save_checkpoint("run_clean", turn_number=turn, messages=SAMPLE_MESSAGES)

    checkpoints = await list_checkpoints("run_clean")
    assert len(checkpoints) == MAX_CHECKPOINTS_PER_RUN
    turn_numbers = {c.turn_number for c in checkpoints}
    assert 1 not in turn_numbers
    assert MAX_CHECKPOINTS_PER_RUN + 1 in turn_numbers


@pytest.mark.asyncio
async def test_clean_run_checkpoints_keeps_latest(db):
    """clean_run_checkpoints with keep_latest=True retains only the latest."""
    await _seed_run("run_post_run")
    await save_checkpoint("run_post_run", turn_number=1, messages=SAMPLE_MESSAGES)
    await save_checkpoint("run_post_run", turn_number=2, messages=SAMPLE_MESSAGES)
    await save_checkpoint("run_post_run", turn_number=3, messages=SAMPLE_MESSAGES)

    deleted = await clean_run_checkpoints("run_post_run", keep_latest=True)
    assert deleted == 2
    checkpoints = await list_checkpoints("run_post_run")
    assert len(checkpoints) == 1
    assert checkpoints[0].turn_number == 3


@pytest.mark.asyncio
async def test_load_latest_no_checkpoint(db):
    """load_latest_checkpoint returns None when no checkpoints exist."""
    latest = await load_latest_checkpoint("run_nonexistent")
    assert latest is None


@pytest.mark.asyncio
async def test_list_checkpoints_empty(db):
    """list_checkpoints returns empty list for a run with no checkpoints."""
    checkpoints = await list_checkpoints("run_no_checkpoints")
    assert len(checkpoints) == 0
