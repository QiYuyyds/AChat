"""Tests for ratio-aware cross-run history pruning (ratio-aware-pre-run-injection change).

Covers:
- 4.1 ratio < 0.65 → no pruning, tool_result preserved
- 4.2 ratio ≥ 0.65 → pruning executes (existing behavior)
- 4.3 model_context_limit=None → ratio=0.0, full injection
- 4.4 Session Memory exists, no ContextSummary → <session_memory> injected
- 4.5 Both ContextSummary + SessionMemory → only ContextSummary injected
- 4.6 Neither exists → no summary block
- 4.7 ORM safety: ratio < 0.65 skip prune → DB parts_list unchanged
"""

from app.db.engine import get_db
from app.db.models import ContextSummary, Conversation, Message
from app.services import conversation_context as cc
from app.services.conversation_context import BuildHistoryOptions
from app.utils.clock import now_ms

# ─── helpers ────────────────────────────────────────────────────────────────


async def _seed_conversation(agent_ids: list[str]) -> str:
    now = now_ms()
    conv_id = "conv_ratio"
    async with get_db() as db:
        conv = Conversation(
            id=conv_id,
            title="ratio test",
            mode="single",
            archived=False,
            fs_write_approval_mode="review",
            created_at=now,
            updated_at=now,
        )
        conv.agent_ids_list = agent_ids
        conv.pinned_message_ids_list = []
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


def _agent_turn_parts(
    call_id: str, tool_name: str, args: dict, result: str, text: str | None = None,
) -> list[dict]:
    parts = []
    if text:
        parts.append({"type": "text", "content": text})
    parts.append({"type": "tool_use", "callId": call_id, "toolName": tool_name, "args": args})
    parts.append({"type": "tool_result", "callId": call_id, "result": result, "isError": False})
    return parts


async def _seed_tool_turns(conv_id: str, agent_id: str, count: int = 4) -> None:
    """Seed user + agent tool turns alternating."""
    ts = 100
    for i in range(count):
        await _add_message(
            f"u{i}", conv_id, "user", [{"type": "text", "content": f"question {i}"}], ts,
        )
        ts += 10
        await _add_message(
            f"a{i}",
            conv_id,
            "agent",
            _agent_turn_parts(
                f"c{i}", "bash", {"command": f"ls {i}"},
                f"output_line_{i}_data", text=f"answer {i}",
            ),
            ts,
            agent_id=agent_id,
        )
        ts += 10


# ─── 4.1 ratio < 0.65: no pruning, tool_result preserved ────────────────────


async def test_ratio_below_threshold_no_prune(db, agents):
    alice = agents["alice"]
    conv_id = await _seed_conversation([alice])
    await _seed_tool_turns(conv_id, alice, count=4)

    history = await cc.build_history_for(
        alice, conv_id,
        BuildHistoryOptions(
            model_context_limit=1_000_000,
            prompt_estimate=2000,
        ),
    )

    # tool_result content from old turns should be preserved verbatim
    all_content = "\n".join(m.get("content", "") for m in history)
    assert "output_line_0_data" in all_content
    assert "output_line_1_data" in all_content


# ─── 4.2 ratio ≥ 0.65: pruning executes ─────────────────────────────────────


async def test_ratio_above_threshold_prune(db, agents):
    alice = agents["alice"]
    conv_id = await _seed_conversation([alice])

    # Use large tool_result content to push ratio above 0.65.
    # Each result ~5000 chars ≈ 1250 tokens; 4 turns ≈ 5000 tokens.
    # With model_context_limit=8000, prompt_estimate=1000:
    #   ratio = (5000 + 1000) / 8000 = 0.75 ≥ 0.65 → pruning triggers.
    big_result = "x" * 5000
    ts = 100
    for i in range(4):
        await _add_message(
            f"u{i}", conv_id, "user", [{"type": "text", "content": f"question {i}"}], ts,
        )
        ts += 10
        await _add_message(
            f"a{i}",
            conv_id,
            "agent",
            _agent_turn_parts(
                f"c{i}", "bash", {"command": f"ls {i}"},
                big_result, text=f"answer {i}",
            ),
            ts,
            agent_id=alice,
        )
        ts += 10

    history = await cc.build_history_for(
        alice, conv_id,
        BuildHistoryOptions(
            model_context_limit=8000,
            prompt_estimate=1000,
        ),
    )

    all_content = "\n".join(m.get("content", "") for m in history)
    # Old turns (0, 1) are folded into a fold marker — check marker is present
    assert "folded" in all_content
    assert "question 0" in all_content  # first_user in fold marker
    # The big_result should NOT appear in full for old turns (pruned + folded)
    assert big_result not in all_content
    # Recent turns (2, 3) should preserve text
    assert "answer 2" in all_content
    assert "answer 3" in all_content


# ─── 4.3 model_context_limit=None: ratio=0.0, full injection ────────────────


async def test_unknown_context_limit_no_prune(db, agents):
    alice = agents["alice"]
    conv_id = await _seed_conversation([alice])
    await _seed_tool_turns(conv_id, alice, count=4)

    history = await cc.build_history_for(alice, conv_id)

    all_content = "\n".join(m.get("content", "") for m in history)
    assert "output_line_0_data" in all_content
    assert "output_line_1_data" in all_content


# ─── 4.4 Session Memory exists, no ContextSummary → injected ────────────────


async def test_session_memory_injected_when_no_context_summary(db, agents):
    alice = agents["alice"]
    conv_id = await _seed_conversation([alice])
    await _add_message("u1", conv_id, "user", [{"type": "text", "content": "hello"}], 100)

    now = now_ms()
    async with get_db() as session:
        session.add(ContextSummary(
            id="sm1",
            conversation_id=conv_id,
            summary="session summary content",
            covered_until_message_id="session",
            covered_until_created_at=100,
            source_message_count=5,
            token_estimate=20,
            summary_type="session",
            covers_up_to=100.0,
            created_at=now,
        ))

    history = await cc.build_history_for(alice, conv_id)

    assert history[0]["role"] == "user"
    assert "<session_memory" in history[0]["content"]
    assert "session summary content" in history[0]["content"]
    assert "covers_up_to" in history[0]["content"]


# ─── 4.5 Both exist → only ContextSummary ───────────────────────────────────


async def test_context_summary_takes_priority_over_session_memory(db, agents):
    alice = agents["alice"]
    conv_id = await _seed_conversation([alice])
    await _add_message("u1", conv_id, "user", [{"type": "text", "content": "hello"}], 200)

    now = now_ms()
    async with get_db() as session:
        session.add(ContextSummary(
            id="cs1",
            conversation_id=conv_id,
            summary="compaction summary",
            covered_until_message_id="m0",
            covered_until_created_at=50,
            source_message_count=3,
            token_estimate=10,
            summary_type="compaction",
            created_at=now,
        ))
        session.add(ContextSummary(
            id="sm1",
            conversation_id=conv_id,
            summary="session summary content",
            covered_until_message_id="session",
            covered_until_created_at=100,
            source_message_count=5,
            token_estimate=20,
            summary_type="session",
            covers_up_to=100.0,
            created_at=now + 1,
        ))

    history = await cc.build_history_for(alice, conv_id)

    all_content = "\n".join(m.get("content", "") for m in history)
    assert "<conversation_summary" in all_content
    assert "compaction summary" in all_content
    assert "<session_memory" not in all_content


# ─── 4.6 Neither exists → no summary block ──────────────────────────────────


async def test_no_summary_no_session_memory_no_block(db, agents):
    alice = agents["alice"]
    conv_id = await _seed_conversation([alice])
    await _add_message("u1", conv_id, "user", [{"type": "text", "content": "hello"}], 100)

    history = await cc.build_history_for(alice, conv_id)

    all_content = "\n".join(m.get("content", "") for m in history)
    assert "<conversation_summary" not in all_content
    assert "<session_memory" not in all_content
    assert history == [{"role": "user", "content": "hello"}]


# ─── 4.7 ORM safety: ratio < 0.65 skip prune → DB parts_list unchanged ─────


async def test_orm_safety_no_prune_db_unchanged(db, agents):
    alice = agents["alice"]
    conv_id = await _seed_conversation([alice])
    await _seed_tool_turns(conv_id, alice, count=4)

    # Snapshot DB parts_list before build_history_for
    async with get_db() as session:
        from sqlalchemy import select

        msgs = (await session.execute(
            select(Message).where(Message.conversation_id == conv_id).order_by(Message.created_at)
        )).scalars().all()
        before_parts = {m.id: [dict(p) for p in m.parts_list] for m in msgs}

    # Call with large context limit → ratio < 0.65 → no prune
    await cc.build_history_for(
        alice, conv_id,
        BuildHistoryOptions(model_context_limit=1_000_000, prompt_estimate=2000),
    )

    # Verify DB parts_list is unchanged
    async with get_db() as session:
        msgs = (await session.execute(
            select(Message).where(Message.conversation_id == conv_id).order_by(Message.created_at)
        )).scalars().all()
        for m in msgs:
            assert m.parts_list == before_parts[m.id], f"Message {m.id} parts_list was modified"


# ─── 4.5 No LIMIT: max_turns=None loads all uncompacted messages ─────────────


async def test_no_limit_loads_all_messages_token_budget_drops_oldest(db, agents):
    """When max_turns=None (default), DB loads all uncompacted messages.

    Token budget then drops oldest non-pinned messages to fit.
    """
    alice = agents["alice"]
    conv_id = await _seed_conversation([alice])

    # Seed 10 user messages — more than old DEFAULT_MAX_TURNS (20) is overkill,
    # but we want to verify all are loaded without LIMIT.
    big_content = "x" * 2000  # ~500 tokens per message
    ts = 100
    for i in range(10):
        await _add_message(
            f"u{i}", conv_id, "user",
            [{"type": "text", "content": f"msg_{i} {big_content}"}], ts,
        )
        ts += 10

    # Use a tiny token_budget to force dropping oldest messages.
    history = await cc.build_history_for(
        alice, conv_id,
        BuildHistoryOptions(
            token_budget=600,  # only ~1-2 messages fit
        ),
    )

    all_content = "\n".join(m.get("content", "") for m in history)
    # Oldest messages should be dropped; newest should survive
    assert "msg_9" in all_content
    assert "msg_0" not in all_content


# ─── 4.6 Explicit max_turns applies LIMIT ────────────────────────────────────


async def test_explicit_max_turns_applies_limit(db, agents):
    """When max_turns is explicitly set, DB query applies .limit(max_turns)."""
    alice = agents["alice"]
    conv_id = await _seed_conversation([alice])

    # Seed 10 user messages
    ts = 100
    for i in range(10):
        await _add_message(
            f"u{i}", conv_id, "user",
            [{"type": "text", "content": f"message_{i}"}], ts,
        )
        ts += 10

    # Explicit max_turns=5 should only load the 5 most recent
    history = await cc.build_history_for(
        alice, conv_id,
        BuildHistoryOptions(max_turns=5),
    )

    all_content = "\n".join(m.get("content", "") for m in history)
    # Only the 5 most recent messages should appear
    assert "message_9" in all_content
    assert "message_5" in all_content
    assert "message_4" not in all_content
    assert "message_0" not in all_content
