"""Tests for TaskScheduler undispatchable-task handling.

An undispatchable task (no agent configured / agent not found) must be moved
to ``blocked`` so it stops the scan loop and becomes visible on the board,
instead of silently staying ``todo`` forever.
"""

from __future__ import annotations

import pytest

from app.db.models import Task
from app.services import task_scheduler as ts
from app.services.task_scheduler import TaskSchedulerService, _SchedulerState


def _task() -> Task:
    return Task(id="t_blk", user_id="u1", title="T", status="todo", version=1)


def _state(agent_id: str | None) -> _SchedulerState:
    return _SchedulerState(
        user_id="u1",
        agent_id=agent_id,
        interval_seconds=300,
        max_concurrent=3,
        running=True,
    )


@pytest.mark.asyncio
async def test_no_agent_marks_task_blocked(monkeypatch):
    calls: list[tuple[str, str, int]] = []

    async def fake_move(user_id, task_id, *, new_status, if_version, sort_order=None):
        calls.append((user_id, task_id, new_status, if_version))

    monkeypatch.setattr(ts.task_service, "move_task", fake_move)

    svc = TaskSchedulerService()
    await svc._dispatch_task(_state(None), _task())

    assert calls == [("u1", "t_blk", "blocked", 1)]


@pytest.mark.asyncio
async def test_missing_agent_marks_task_blocked(monkeypatch):
    calls: list[str] = []

    async def fake_move(user_id, task_id, *, new_status, if_version, sort_order=None):
        calls.append(new_status)

    async def fake_get_agent(agent_id):
        return None

    monkeypatch.setattr(ts.task_service, "move_task", fake_move)
    monkeypatch.setattr("app.infra.cache_helpers.get_agent_cached", fake_get_agent)

    svc = TaskSchedulerService()
    await svc._dispatch_task(_state("ag_missing"), _task())

    assert calls == ["blocked"]
