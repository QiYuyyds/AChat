"""Tests for AgentSessionRegistry — DAG session tracking, mailbox, and cleanup."""

from __future__ import annotations

from app.services.agent_session_registry import (
    AgentSession,
    AgentSessionRegistry,
)
from app.utils.clock import now_ms


def _make_session(
    task_id: str = "t1",
    run_id: str = "run_t1",
    agent_id: str = "ag_1",
    created_at: int | None = None,
) -> AgentSession:
    return AgentSession(
        task_id=task_id,
        run_id=run_id,
        agent_id=agent_id,
        conversation_id="conv_test",
        parent_run_id="run_parent",
        dispatch_depth=1,
        status="running",
        created_at=created_at if created_at is not None else now_ms(),
    )


# ─── 8.1 register / get / update_status / set_system_prompt ───────────────────


def test_register_and_get():
    reg = AgentSessionRegistry()
    session = _make_session()
    reg.register_with_dag("dag_1", "t1", session)
    assert reg.get("t1") is session


def test_update_status():
    reg = AgentSessionRegistry()
    reg.register_with_dag("dag_1", "t1", _make_session())
    reg.update_status("t1", "completed")
    assert reg.get("t1").status == "completed"


def test_update_status_failed():
    reg = AgentSessionRegistry()
    reg.register_with_dag("dag_1", "t1", _make_session())
    reg.update_status("t1", "failed")
    assert reg.get("t1").status == "failed"


def test_set_system_prompt():
    reg = AgentSessionRegistry()
    reg.register_with_dag("dag_1", "t1", _make_session())
    reg.set_system_prompt("t1", "you are a helpful agent")
    assert reg.get_system_prompt("t1") == "you are a helpful agent"


def test_get_nonexistent_returns_none():
    reg = AgentSessionRegistry()
    assert reg.get("nonexistent") is None
    assert reg.get_system_prompt("nonexistent") is None


# ─── 8.2 mailbox add / drain ──────────────────────────────────────────────────


def test_mailbox_add_and_drain():
    reg = AgentSessionRegistry()
    reg.add_to_mailbox("run_parent", "message 1")
    reg.add_to_mailbox("run_parent", "message 2")
    msgs = reg.drain_mailbox("run_parent")
    assert msgs == ["message 1", "message 2"]


def test_mailbox_drain_empty():
    reg = AgentSessionRegistry()
    assert reg.drain_mailbox("run_parent") == []


def test_mailbox_drain_clears():
    reg = AgentSessionRegistry()
    reg.add_to_mailbox("run_parent", "msg")
    reg.drain_mailbox("run_parent")
    assert reg.drain_mailbox("run_parent") == []


# ─── 8.3 mark_dag_completed ──────────────────────────────────────────────────


def test_mark_dag_completed_sets_all_running_to_completed():
    reg = AgentSessionRegistry()
    reg.register_with_dag("dag_1", "t1", _make_session("t1"))
    reg.register_with_dag("dag_1", "t2", _make_session("t2"))
    reg.register_with_dag("dag_1", "t3", _make_session("t3"))

    # Mark t2 as already failed — should stay failed
    reg.update_status("t2", "failed")

    reg.mark_dag_completed("dag_1")

    assert reg.get("t1").status == "completed"
    assert reg.get("t2").status == "failed"  # already failed, not changed
    assert reg.get("t3").status == "completed"


def test_mark_dag_completed_unknown_dag():
    reg = AgentSessionRegistry()
    # Should not raise
    reg.mark_dag_completed("nonexistent")


def test_get_by_dag():
    reg = AgentSessionRegistry()
    reg.register_with_dag("dag_1", "t1", _make_session("t1"))
    reg.register_with_dag("dag_1", "t2", _make_session("t2"))
    reg.register_with_dag("dag_2", "t3", _make_session("t3"))

    assert reg.get_by_dag("dag_1") == {"t1", "t2"}
    assert reg.get_by_dag("dag_2") == {"t3"}
    assert reg.get_by_dag("nonexistent") == set()


# ─── 8.4 cleanup_expired ─────────────────────────────────────────────────────


def test_cleanup_expired_removes_old_sessions():
    reg = AgentSessionRegistry()
    now = now_ms()
    # 400 seconds ago — older than default 300s expiry
    old_ts = now - 400_000
    reg.register_with_dag("dag_1", "t1", _make_session("t1", created_at=old_ts))
    reg.update_status("t1", "completed")

    reg.cleanup_expired(expiry_seconds=300)
    assert reg.get("t1") is None


def test_cleanup_expired_keeps_recent_sessions():
    reg = AgentSessionRegistry()
    now = now_ms()
    # 100 seconds ago — younger than 300s expiry
    recent_ts = now - 100_000
    reg.register_with_dag("dag_1", "t1", _make_session("t1", created_at=recent_ts))
    reg.update_status("t1", "completed")

    reg.cleanup_expired(expiry_seconds=300)
    assert reg.get("t1") is not None


def test_cleanup_expired_keeps_running_sessions():
    reg = AgentSessionRegistry()
    now = now_ms()
    old_ts = now - 400_000
    reg.register_with_dag("dag_1", "t1", _make_session("t1", created_at=old_ts))
    # Session is still running — should not be cleaned up
    reg.cleanup_expired(expiry_seconds=300)
    assert reg.get("t1") is not None
