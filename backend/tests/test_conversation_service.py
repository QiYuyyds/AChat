"""Tests for the conversation service (phase 2)."""

import pytest

from app.services import conversation_service as cs


# ─── Create / list / get ────────────────────────────────────────────────────
async def test_create_single_conversation(db, agents):
    conv = await cs.create_conversation(mode="single", agent_ids=[agents["alice"]])
    assert conv.mode == "single"
    assert conv.agent_ids == [agents["alice"]]
    assert conv.title == "Alice"  # default single-agent title is the agent name
    assert conv.workspace_mode == "sandbox"
    assert conv.workspace_bound_path is None
    assert conv.fs_write_approval_mode == "review"


async def test_create_group_conversation(db, agents):
    conv = await cs.create_conversation(
        mode="group", agent_ids=[agents["alice"], agents["orch"]]
    )
    assert conv.mode == "group"
    assert set(conv.agent_ids) == {agents["alice"], agents["orch"]}
    assert conv.title == "群聊（2）"  # group title: 群聊（N）


async def test_create_single_rejects_multiple_agents(db, agents):
    with pytest.raises(ValueError):
        await cs.create_conversation(
            mode="single", agent_ids=[agents["alice"], agents["orch"]]
        )


async def test_create_rejects_unknown_agent(db, agents):
    with pytest.raises(ValueError, match="Agents not found"):
        await cs.create_conversation(mode="single", agent_ids=["ag_missing"])


async def test_list_orders_pinned_first(db, agents):
    c1 = await cs.create_conversation(mode="single", agent_ids=[agents["alice"]])
    c2 = await cs.create_conversation(mode="single", agent_ids=[agents["alice"]])
    # Pin the older one; it should jump to the front.
    await cs.toggle_pin_conversation(c1.id)
    listed = await cs.list_conversations()
    assert [c.id for c in listed][0] == c1.id
    assert {c.id for c in listed} == {c1.id, c2.id}


async def test_get_conversation_missing_raises(db, agents):
    with pytest.raises(ValueError, match="Conversation not found"):
        await cs.get_conversation("conv_nope")


# ─── Rename / pin / archive / approval mode ─────────────────────────────────
async def test_rename(db, agents):
    conv = await cs.create_conversation(mode="single", agent_ids=[agents["alice"]])
    renamed = await cs.rename_conversation(conv.id, "  New Title  ")
    assert renamed.title == "New Title"


async def test_rename_empty_raises(db, agents):
    conv = await cs.create_conversation(mode="single", agent_ids=[agents["alice"]])
    with pytest.raises(ValueError):
        await cs.rename_conversation(conv.id, "   ")


async def test_toggle_pin_and_archive(db, agents):
    conv = await cs.create_conversation(mode="single", agent_ids=[agents["alice"]])
    pinned = await cs.toggle_pin_conversation(conv.id)
    assert pinned.pinned_at is not None
    unpinned = await cs.toggle_pin_conversation(conv.id)
    assert unpinned.pinned_at is None

    archived = await cs.toggle_archive_conversation(conv.id)
    assert archived.archived is True


async def test_set_approval_mode(db, agents):
    conv = await cs.create_conversation(mode="single", agent_ids=[agents["alice"]])
    updated = await cs.set_conversation_approval_mode(conv.id, "auto")
    assert updated.fs_write_approval_mode == "auto"


async def test_add_agents_promotes_to_group(db, agents):
    conv = await cs.create_conversation(mode="single", agent_ids=[agents["alice"]])
    updated = await cs.add_agents_to_conversation(conv.id, [agents["orch"]])
    assert updated.mode == "group"
    assert set(updated.agent_ids) == {agents["alice"], agents["orch"]}


# ─── Send message ───────────────────────────────────────────────────────────
async def test_send_message_creates_user_message(db, agents):
    conv = await cs.create_conversation(mode="single", agent_ids=[agents["alice"]])
    result = await cs.send_message(conversation_id=conv.id, content="hello")

    assert result.message_id.startswith("msg_")
    # Single chat → one responder → noop runner returns one run id.
    assert len(result.run_ids) == 1

    msgs = await cs.list_messages(conv.id)
    assert len(msgs) == 1
    assert msgs[0].role == "user"
    assert msgs[0].parts == [{"type": "text", "content": "hello"}]


async def test_send_message_group_no_mention_goes_to_orchestrator(db, agents):
    conv = await cs.create_conversation(
        mode="group", agent_ids=[agents["alice"], agents["orch"]]
    )
    result = await cs.send_message(conversation_id=conv.id, content="anyone?")
    # No @mention in a group → orchestrator responds (one run).
    assert len(result.run_ids) == 1


async def test_send_message_group_mention_targets_agent(db, agents):
    conv = await cs.create_conversation(
        mode="group", agent_ids=[agents["alice"], agents["orch"]]
    )
    result = await cs.send_message(
        conversation_id=conv.id, content="hi", mentioned_agent_ids=[agents["alice"]]
    )
    assert len(result.run_ids) == 1


async def test_send_deploy_command_no_candidates(db, agents):
    conv = await cs.create_conversation(mode="single", agent_ids=[agents["alice"]])
    result = await cs.send_message(conversation_id=conv.id, content="/deploy")

    assert result.run_ids == []
    assert result.deploy is not None
    assert result.deploy.kind == "no_candidates"

    # The user "/deploy" message + the system response message.
    msgs = await cs.list_messages(conv.id)
    assert [m.role for m in msgs] == ["user", "system"]


# ─── Pin / bookmark messages ────────────────────────────────────────────────
async def test_toggle_bookmark_and_pin_message(db, agents):
    conv = await cs.create_conversation(mode="single", agent_ids=[agents["alice"]])
    sent = await cs.send_message(conversation_id=conv.id, content="m")

    bm = await cs.toggle_bookmarked_message(conv.id, sent.message_id)
    assert bm["bookmarked"] is True
    assert sent.message_id in bm["bookmarkedMessageIds"]

    pm = await cs.toggle_pinned_message(conv.id, sent.message_id)
    assert pm["pinned"] is True
    assert sent.message_id in pm["pinnedMessageIds"]


async def test_pin_limit_enforced(db, agents):
    conv = await cs.create_conversation(mode="single", agent_ids=[agents["alice"]])
    ids = []
    for i in range(cs.PIN_LIMIT_PER_CONVERSATION + 1):
        sent = await cs.send_message(conversation_id=conv.id, content=f"m{i}")
        ids.append(sent.message_id)

    for mid in ids[: cs.PIN_LIMIT_PER_CONVERSATION]:
        await cs.toggle_pinned_message(conv.id, mid)

    with pytest.raises(ValueError, match="PIN_LIMIT_EXCEEDED"):
        await cs.toggle_pinned_message(conv.id, ids[cs.PIN_LIMIT_PER_CONVERSATION])


# ─── Withdraw / regenerate / edit ───────────────────────────────────────────
async def test_withdraw_latest_user_message(db, agents):
    conv = await cs.create_conversation(mode="single", agent_ids=[agents["alice"]])
    sent = await cs.send_message(conversation_id=conv.id, content="oops")

    result = await cs.withdraw_latest_user_message(conv.id, sent.message_id)
    # withdraw deletes the user message plus anything it triggered downstream
    # (the real phase-5 runner may already have persisted an agent reply).
    assert sent.message_id in result.deleted_message_ids

    # the withdrawn user message is gone; any late finalize-emitted agent error
    # message is the aborted run's, never the original user turn.
    msgs = await cs.list_messages(conv.id)
    assert all(m.role != "user" for m in msgs)
    assert sent.message_id not in {m.id for m in msgs}


async def test_withdraw_non_latest_raises(db, agents):
    conv = await cs.create_conversation(mode="single", agent_ids=[agents["alice"]])
    first = await cs.send_message(conversation_id=conv.id, content="first")
    await cs.send_message(conversation_id=conv.id, content="second")

    with pytest.raises(ValueError, match="latest user message"):
        await cs.withdraw_latest_user_message(conv.id, first.message_id)


async def test_edit_and_resend_replaces_message(db, agents):
    conv = await cs.create_conversation(mode="single", agent_ids=[agents["alice"]])
    sent = await cs.send_message(conversation_id=conv.id, content="typo")

    result = await cs.edit_and_resend_latest_user_message(conv.id, sent.message_id, "fixed")
    assert sent.message_id in result.deleted_message_ids
    assert result.new_message.parts == [{"type": "text", "content": "fixed"}]

    # the resent user message replaces the original; agent replies (now spawned
    # by the real phase-5 runner) are async and not asserted here.
    msgs = await cs.list_messages(conv.id)
    user_msgs = [m for m in msgs if m.role == "user"]
    assert len(user_msgs) == 1
    assert user_msgs[0].id == result.new_message.id
    assert sent.message_id not in {m.id for m in msgs}


# ─── Clear history / delete (cascade) ───────────────────────────────────────
async def _wait_runs_settled(conv_id: str, timeout_s: float = 30.0) -> None:
    """Wait until send_message's spawned runs are fully done.

    send_message spawns real async runs; the clear-history guard rejects
    while any run is active. Two race windows exist: the run task lives in
    agent_runner._active_runs before its AgentRun row is inserted, and the
    row stays queued/running until the loop finalizes — so both the
    in-process registry and the DB must be quiet.
    """
    import asyncio

    from sqlalchemy import select

    from app.db.engine import get_db
    from app.db.models import AgentRun
    from app.services import agent_runner as ar

    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        if not ar._active_runs:
            async with get_db() as session:
                active = await session.execute(
                    select(AgentRun.id).where(
                        AgentRun.conversation_id == conv_id,
                        AgentRun.status.in_(["queued", "running"]),
                    )
                )
                if active.first() is None:
                    return
        await asyncio.sleep(0.05)
    raise TimeoutError(f"agent runs for {conv_id} still active after {timeout_s}s")


async def test_clear_conversation_history(db, agents):
    conv = await cs.create_conversation(mode="single", agent_ids=[agents["alice"]])
    await cs.send_message(conversation_id=conv.id, content="a")
    await cs.send_message(conversation_id=conv.id, content="b")
    await _wait_runs_settled(conv.id)

    # The async runs may have posted agent replies before settling, so the
    # exact deleted count is whatever exists at clear time — the contract is
    # that EVERYTHING is wiped, not a specific number of user messages.
    before = len(await cs.list_messages(conv.id))

    result = await cs.clear_conversation_history(conv.id)
    assert result.deleted_message_count == before

    msgs = await cs.list_messages(conv.id)
    assert msgs == []


async def test_delete_conversation_cascades(db, agents):
    conv = await cs.create_conversation(mode="single", agent_ids=[agents["alice"]])
    await cs.send_message(conversation_id=conv.id, content="x")

    await cs.delete_conversation(conv.id)

    # Conversation gone.
    with pytest.raises(ValueError, match="Conversation not found"):
        await cs.get_conversation(conv.id)

    # Messages cascaded away (FK pragma must be on for this to pass).
    from sqlalchemy import func, select

    from app.db.engine import get_db
    from app.db.models import Message

    async with get_db() as session:
        count = await session.execute(
            select(func.count()).select_from(Message).where(
                Message.conversation_id == conv.id
            )
        )
        assert int(count.scalar_one()) == 0
