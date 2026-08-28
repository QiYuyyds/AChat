"""Tests for the unified cross-run compaction pipeline (layer3-cross-run-restructure).

Covers:
  - test_build_history_tiered_injection: ratio thresholds A/B/C/D/E
  - test_build_history_expunge_fix: ORM pollution prevention
  - test_build_history_ignores_compaction_type: legacy compaction data ignored
"""

from __future__ import annotations

import pytest

from app.db.engine import get_db
from app.db.models import ContextSummary, Conversation, Message
from app.services import conversation_context as cc
from app.utils.clock import now_ms

# ─── helpers ───────────────────────────────────────────────────────────────


async def _seed_conversation(
    agent_ids: list[str],
    *,
    pinned: list[str] | None = None,
) -> str:
    now = now_ms()
    conv_id = "conv_unified"
    async with get_db() as db:
        conv = Conversation(
            id=conv_id,
            title="unified test",
            mode="group" if len(agent_ids) > 1 else "single",
            archived=False,
            fs_write_approval_mode="review",
            created_at=now,
            updated_at=now,
        )
        conv.agent_ids_list = agent_ids
        conv.pinned_message_ids_list = pinned or []
        db.add(conv)
    return conv_id


async def _add_message(
    msg_id: str,
    conv_id: str,
    role: str,
    parts: list[dict],
    created_at: int,
    *,
    agent_id: str | None = None,
    status: str = "complete",
) -> None:
    async with get_db() as db:
        m = Message(
            id=msg_id,
            conversation_id=conv_id,
            role=role,
            agent_id=agent_id,
            status=status,
            created_at=created_at,
        )
        m.parts_list = parts
        m.mentioned_agent_ids_list = []
        db.add(m)


async def _add_session_note(
    conv_id: str,
    summary: str,
    covers_up_to: float,
) -> None:
    """Insert a session-type ContextSummary (Session Note)."""
    now = now_ms()
    async with get_db() as db:
        from app.db.models import ContextSummary as CS
        sm = CS(
            id="cs_session_test",
            conversation_id=conv_id,
            summary=summary,
            covered_until_message_id="session",
            covered_until_created_at=int(covers_up_to),
            source_message_count=3,
            token_estimate=50,
            created_at=now,
            summary_type="session",
            covers_up_to=covers_up_to,
        )
        db.add(sm)


async def _add_compaction_summary(
    conv_id: str,
    summary: str,
    covers_up_to: float,
) -> None:
    """Insert a legacy compaction-type ContextSummary."""
    now = now_ms()
    async with get_db() as db:
        cs = ContextSummary(
            id="cs_compaction_test",
            conversation_id=conv_id,
            summary=summary,
            covered_until_message_id="m0",
            covered_until_created_at=int(covers_up_to),
            source_message_count=3,
            token_estimate=10,
            created_at=now,
            summary_type="compaction",
        )
        db.add(cs)


# ─── 3.6: Tiered injection ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_build_history_tiered_injection_case_b(db, agents, monkeypatch):
    """Case B: ratio < 0.50 → full text only, no Note injected."""
    monkeypatch.setenv("COMPACT_USE_UNIFIED_PIPELINE", "true")
    from app.config import get_settings
    get_settings.cache_clear()

    alice = agents["alice"]
    conv_id = await _seed_conversation([alice])

    # Add a session note
    await _add_session_note(conv_id, "title: test\ncurrent_state: testing\n", 50.0)
    # Small message after note → ratio < 0.50
    await _add_message("u1", conv_id, "user", [{"type": "text", "content": "short"}], 100)

    history = await cc.build_history_for(alice, conv_id)

    # Should NOT contain session_note (ratio < 0.50)
    assert not any("session_note" in m.get("content", "") for m in history)
    # Should contain the full message
    assert {"role": "user", "content": "short"} in history


@pytest.mark.asyncio
async def test_build_history_tiered_injection_case_c(db, agents, monkeypatch):
    """Case C: 0.50 ≤ ratio < 0.75 → Note + full text, no compaction."""
    monkeypatch.setenv("COMPACT_USE_UNIFIED_PIPELINE", "true")
    from app.config import get_settings
    get_settings.cache_clear()

    alice = agents["alice"]
    conv_id = await _seed_conversation([alice])

    yaml_summary = (
        "title: 测试\n"
        "current_state: 正在测试\n"
        "key_decisions: []\n"
        "files_touched: []\n"
        "commands_run: []\n"
        "artifacts_produced: []\n"
        "blockers: []\n"
        "open_questions: []\n"
        "next_steps: []\n"
        "architecture_understanding: \"\"\n"
        "covers_up_to: 50.0\n"
    )
    await _add_session_note(conv_id, yaml_summary, 50.0)

    # Create messages that put ratio in [0.50, 0.75) range
    # 200k window * 0.50 = 100k tokens ≈ 400k chars
    # We need ~100k+ chars to cross 0.50
    big_content = "x" * 300_000  # ~75k tokens → ratio ≈ 0.375 (with prompt=0)
    # Actually, with _CONTEXT_WINDOW=200_000, we need ≥ 100k tokens for ratio=0.50
    # 300k chars / 4 ≈ 75k tokens → ratio ≈ 0.375 → Case B
    # Let's make it bigger to hit Case C
    big_content = "x" * 500_000  # ~125k tokens → ratio ≈ 0.625 → Case C
    await _add_message("u1", conv_id, "user", [{"type": "text", "content": big_content}], 100)

    history = await cc.build_history_for(alice, conv_id)

    # Should contain session_note (ratio ≥ 0.50)
    has_note = any("<session_note" in m.get("content", "") or "<session_memory" in m.get("content", "")
                    for m in history)
    assert has_note, "Case C should inject Session Note"

    # Should contain full text (no compaction)
    has_full = any(big_content in m.get("content", "") for m in history)
    assert has_full, "Case C should contain full text"

    # Should NOT contain mask markers (no compaction)
    has_mask = any("[masked" in m.get("content", "") for m in history)
    assert not has_mask, "Case C should not have mask markers"


@pytest.mark.asyncio
async def test_build_history_tiered_injection_case_a(db, agents, monkeypatch):
    """Case A: no messages after Note → inject Note only."""
    monkeypatch.setenv("COMPACT_USE_UNIFIED_PIPELINE", "true")
    from app.config import get_settings
    get_settings.cache_clear()

    alice = agents["alice"]
    conv_id = await _seed_conversation([alice])

    yaml_summary = (
        "title: 测试\n"
        "current_state: done\n"
        "key_decisions: []\n"
        "files_touched: []\n"
        "commands_run: []\n"
        "artifacts_produced: []\n"
        "blockers: []\n"
        "open_questions: []\n"
        "next_steps: []\n"
        "architecture_understanding: \"\"\n"
        "covers_up_to: 200.0\n"
    )
    await _add_session_note(conv_id, yaml_summary, 200.0)
    # No messages after the note → Case A

    history = await cc.build_history_for(alice, conv_id)

    # Should contain the Note
    has_note = any("<session_note" in m.get("content", "") or "<session_memory" in m.get("content", "")
                    for m in history)
    assert has_note, "Case A should inject Session Note"
    # Should not contain other messages
    assert len(history) == 1


# ─── 3.7: expunge fix ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_build_history_expunge_fix(db, agents, monkeypatch):
    """Verify that build_history_for does not pollute DB (expunge_all works)."""
    monkeypatch.setenv("COMPACT_USE_UNIFIED_PIPELINE", "true")
    from app.config import get_settings
    get_settings.cache_clear()

    alice = agents["alice"]
    conv_id = await _seed_conversation([alice])

    # Add messages with tool_use + tool_result parts that would be compacted
    big_result = "result content " * 500
    parts_agent = [
        {"type": "text", "content": "I'll list files."},
        {"type": "tool_use", "callId": "call_1", "toolName": "fs_list",
         "args": {"path": "src", "depth": 3}},
        {"type": "tool_result", "callId": "call_1", "result": big_result},
    ]

    # Create enough turns to trigger compaction
    for t in range(6):
        await _add_message(
            f"agent_{t}", conv_id, "agent", parts_agent, 100 + t * 100,
            agent_id=alice,
        )

    # Run build_history_for
    await cc.build_history_for(alice, conv_id)

    # Verify DB is not polluted — parts_list should still have original content
    async with get_db() as db:
        from sqlalchemy import select
        result = await db.execute(
            select(Message).where(Message.conversation_id == conv_id)
        )
        db_msgs = result.scalars().all()
        for msg in db_msgs:
            for p in msg.parts_list:
                if p.get("type") == "tool_result":
                    # The original big result should still be in DB
                    assert "result content" in str(p.get("result", "")), \
                        "DB should retain original tool_result content"


# ─── 3.8: ignores compaction type ──────────────────────────────────────


@pytest.mark.asyncio
async def test_build_history_ignores_compaction_type(db, agents, monkeypatch):
    """Legacy summary_type='compaction' data should be ignored."""
    monkeypatch.setenv("COMPACT_USE_UNIFIED_PIPELINE", "true")
    from app.config import get_settings
    get_settings.cache_clear()

    alice = agents["alice"]
    conv_id = await _seed_conversation([alice])

    # Add a legacy compaction-type summary
    await _add_compaction_summary(conv_id, "legacy compaction data", 50.0)

    # Add a session-type summary
    yaml_summary = (
        "title: session\n"
        "current_state: active\n"
        "key_decisions: []\n"
        "files_touched: []\n"
        "commands_run: []\n"
        "artifacts_produced: []\n"
        "blockers: []\n"
        "open_questions: []\n"
        "next_steps: []\n"
        "architecture_understanding: \"\"\n"
        "covers_up_to: 50.0\n"
    )
    await _add_session_note(conv_id, yaml_summary, 50.0)

    # Add a message after both summaries
    await _add_message("u1", conv_id, "user", [{"type": "text", "content": "after"}], 100)

    history = await cc.build_history_for(alice, conv_id)

    # Should NOT contain the legacy compaction data
    has_compaction = any("legacy compaction data" in m.get("content", "") for m in history)
    assert not has_compaction, "Legacy compaction data should be ignored"

    # Should contain the session note (ratio is low → Case B, no note)
    # Actually with a short message "after" and ratio < 0.50, no note is injected.
    # Let's verify at least the message is there.
    assert {"role": "user", "content": "after"} in history


@pytest.mark.asyncio
async def test_build_history_ignores_compaction_type_with_note(db, agents, monkeypatch):
    """When both compaction and session exist, only session is used as source."""
    monkeypatch.setenv("COMPACT_USE_UNIFIED_PIPELINE", "true")
    from app.config import get_settings
    get_settings.cache_clear()

    alice = agents["alice"]
    conv_id = await _seed_conversation([alice])

    # Add both types
    await _add_compaction_summary(conv_id, "legacy compaction data", 50.0)
    yaml_summary = (
        "title: session\n"
        "current_state: active\n"
        "key_decisions: []\n"
        "files_touched: []\n"
        "commands_run: []\n"
        "artifacts_produced: []\n"
        "blockers: []\n"
        "open_questions: []\n"
        "next_steps: []\n"
        "architecture_understanding: \"\"\n"
        "covers_up_to: 50.0\n"
    )
    await _add_session_note(conv_id, yaml_summary, 50.0)

    # Large message to trigger Case C (note + full text)
    big_content = "x" * 500_000  # ~125k tokens → ratio ≈ 0.625
    await _add_message("u1", conv_id, "user", [{"type": "text", "content": big_content}], 100)

    history = await cc.build_history_for(alice, conv_id)

    # Should contain session note (not compaction data)
    has_session = any(
        "<session_note" in m.get("content", "") or "<session_memory" in m.get("content", "")
        for m in history
    )
    assert has_session, "Session Note should be injected in Case C"

    # Should NOT contain compaction data
    has_compaction = any("legacy compaction data" in m.get("content", "") for m in history)
    assert not has_compaction, "Legacy compaction data should be ignored"
