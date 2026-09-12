"""Unit tests for worktree_service.isolated_workspace.

The lifecycle context manager is tested with the three worktree functions
(create / merge / cleanup) mocked; the agent-name query runs against the
real seeded test DB (agents fixture) so the degrade guards are exercised
end-to-end.
"""

from __future__ import annotations

import logging

import pytest

from app.services import worktree_service as wt
from app.services.worktree_service import WorktreeRef, isolated_workspace


def _make_wt(task_id: str = "task_1") -> WorktreeRef:
    return WorktreeRef(
        task_id=task_id,
        branch_name="agent/alice/task_1",
        path="/tmp/wt/task_1",
        main_workspace_path="/tmp/ws",
        is_git=False,
        conversation_id="conv_1",
        user_id=None,
        agent_id="ag_alice",
    )


async def test_create_exception_degrades_to_none(
    agents, monkeypatch, caplog
):
    """create_worktree raising → yield None, no merge/cleanup, warning logged."""

    async def _boom(**kwargs):
        raise RuntimeError("disk exploded")

    async def _unexpected(*args, **kwargs):
        raise AssertionError("merge/cleanup must not run when create failed")

    calls: list[str] = []
    monkeypatch.setattr(wt, "create_worktree", _boom)
    monkeypatch.setattr(wt, "merge_worktree_back", _unexpected)
    monkeypatch.setattr(wt, "cleanup_worktree", _track(calls, "cleanup"))

    with caplog.at_level(logging.WARNING, logger="app.services.worktree_service"):
        async with isolated_workspace(
            main_workspace="/tmp/ws",
            task_id="task_1",
            agent_id="ag_alice",
            conversation_id="conv_1",
            user_id=None,
        ) as result:
            assert result is None

    assert calls == []
    assert any("create_worktree failed" in rec.message for rec in caplog.records)


async def test_merge_failure_still_cleans_up(agents, monkeypatch):
    """merge_worktree_back raising → cleanup still runs, exception not raised."""
    stub = _make_wt()

    async def _fake_create(**kwargs):
        return stub

    async def _merge_fail(wt_ref):
        raise RuntimeError("conflict storm")

    cleanup_calls: list[WorktreeRef] = []
    monkeypatch.setattr(wt, "create_worktree", _fake_create)
    monkeypatch.setattr(wt, "merge_worktree_back", _merge_fail)

    async def _fake_cleanup(wt_ref):
        cleanup_calls.append(wt_ref)

    monkeypatch.setattr(wt, "cleanup_worktree", _fake_cleanup)

    async with isolated_workspace(
        main_workspace="/tmp/ws",
        task_id="task_1",
        agent_id="ag_alice",
        conversation_id="conv_1",
        user_id=None,
    ) as result:
        assert result is stub

    assert cleanup_calls == [stub]


async def test_normal_flow_merges_then_cleans_up(agents, monkeypatch):
    """Success path: yields the worktree; merge runs before cleanup, once each."""
    stub = _make_wt()

    async def _fake_create(**kwargs):
        return stub

    order: list[str] = []

    async def _fake_merge(wt_ref):
        order.append("merge")

    async def _fake_cleanup(wt_ref):
        order.append("cleanup")

    monkeypatch.setattr(wt, "create_worktree", _fake_create)
    monkeypatch.setattr(wt, "merge_worktree_back", _fake_merge)
    monkeypatch.setattr(wt, "cleanup_worktree", _fake_cleanup)

    async with isolated_workspace(
        main_workspace="/tmp/ws",
        task_id="task_1",
        agent_id="ag_alice",
        conversation_id="conv_1",
        user_id=None,
    ) as result:
        assert result is stub
        assert order == []  # nothing runs until the body exits

    assert order == ["merge", "cleanup"]


@pytest.mark.parametrize(
    ("main_workspace", "agent_id"),
    [(None, "ag_alice"), ("", "ag_alice"), ("/tmp/ws", None), (None, None)],
)
async def test_missing_workspace_or_agent_degrades_without_create(
    agents, monkeypatch, main_workspace, agent_id
):
    """No workspace or no agent → yield None; nothing created / merged / cleaned."""
    create_calls: list[dict] = []

    async def _fake_create(**kwargs):
        create_calls.append(kwargs)
        return _make_wt()

    async def _unexpected(*args, **kwargs):
        raise AssertionError("merge/cleanup must not run when nothing was created")

    monkeypatch.setattr(wt, "create_worktree", _fake_create)
    monkeypatch.setattr(wt, "merge_worktree_back", _unexpected)
    monkeypatch.setattr(wt, "cleanup_worktree", _unexpected)

    async with isolated_workspace(
        main_workspace=main_workspace,
        task_id="task_1",
        agent_id=agent_id,
        conversation_id="conv_1",
        user_id=None,
    ) as result:
        assert result is None

    assert create_calls == []


async def test_create_returning_none_degrades(agents, monkeypatch):
    """create_worktree returning None (its own failure mode) → yield None."""

    async def _fake_create(**kwargs):
        return None

    monkeypatch.setattr(wt, "create_worktree", _fake_create)

    async with isolated_workspace(
        main_workspace="/tmp/ws",
        task_id="task_1",
        agent_id="ag_alice",
        conversation_id="conv_1",
        user_id=None,
    ) as result:
        assert result is None


def _track(calls: list[str], label: str):
    async def _fn(*args, **kwargs):
        calls.append(label)

    return _fn
