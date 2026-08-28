"""Tests for conversation_context.build_history_for (phase 5 dep, spec 13)."""

from app.db.engine import get_db
from app.db.models import Artifact, ContextSummary, Conversation, Message
from app.services import conversation_context as cc
from app.services.conversation_context import BuildHistoryOptions
from app.utils.clock import now_ms


async def _seed_conversation(
    agent_ids: list[str],
    *,
    pinned: list[str] | None = None,
) -> str:
    now = now_ms()
    conv_id = "conv_ctx"
    async with get_db() as db:
        conv = Conversation(
            id=conv_id,
            title="ctx test",
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


async def test_build_history_maps_roles_and_order(db, agents):
    alice = agents["alice"]
    conv_id = await _seed_conversation([alice])
    await _add_message("m1", conv_id, "user", [{"type": "text", "content": "hi"}], 100)
    await _add_message(
        "m2",
        conv_id,
        "agent",
        [{"type": "text", "content": "hello there"}],
        200,
        agent_id=alice,
    )

    history = await cc.build_history_for(alice, conv_id)

    assert history == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello there"},
    ]


async def test_build_history_skips_system_and_thinking(db, agents):
    alice = agents["alice"]
    conv_id = await _seed_conversation([alice])
    await _add_message("s1", conv_id, "system", [{"type": "text", "content": "sys"}], 50)
    await _add_message("u1", conv_id, "user", [{"type": "text", "content": "q"}], 100)
    # thinking/tool parts on an agent message are dropped from cross-run history.
    await _add_message(
        "a1",
        conv_id,
        "agent",
        [
            {"type": "thinking", "content": "secret"},
            {"type": "text", "content": "answer"},
        ],
        200,
        agent_id=alice,
    )

    history = await cc.build_history_for(alice, conv_id)

    assert history == [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "answer"},
    ]


async def test_pinned_message_injected_outside_recent_window(db, agents):
    alice = agents["alice"]
    conv_id = await _seed_conversation([alice], pinned=["pin1"])
    # An old pinned user message.
    await _add_message("pin1", conv_id, "user", [{"type": "text", "content": "pinned"}], 10)
    # A newer message inside the maxTurns=1 window.
    await _add_message("u2", conv_id, "user", [{"type": "text", "content": "recent"}], 500)

    history = await cc.build_history_for(
        alice, conv_id, BuildHistoryOptions(max_turns=1)
    )

    # Pinned old message still injected, sorted before the recent one.
    assert {"role": "user", "content": "pinned"} in history
    assert {"role": "user", "content": "recent"} in history
    assert history.index({"role": "user", "content": "pinned"}) < history.index(
        {"role": "user", "content": "recent"}
    )


async def test_exclude_message_id_dropped(db, agents):
    alice = agents["alice"]
    conv_id = await _seed_conversation([alice])
    await _add_message("u1", conv_id, "user", [{"type": "text", "content": "keep"}], 100)
    await _add_message("u2", conv_id, "user", [{"type": "text", "content": "skip"}], 200)

    history = await cc.build_history_for(
        alice, conv_id, BuildHistoryOptions(exclude_message_id="u2")
    )

    assert history == [{"role": "user", "content": "keep"}]


async def test_artifact_ref_folds_to_title(db, agents):
    alice = agents["alice"]
    conv_id = await _seed_conversation([alice])
    now = now_ms()
    async with get_db() as session:
        art = Artifact(
            id="art1",
            conversation_id=conv_id,
            type="document",
            title="My Doc",
            content="{}",
            version=1,
            created_by_agent_id=alice,
            created_at=now,
        )
        session.add(art)
    await _add_message(
        "a1",
        conv_id,
        "agent",
        [{"type": "artifact_ref", "artifactId": "art1"}],
        300,
        agent_id=alice,
    )

    history = await cc.build_history_for(alice, conv_id)

    assert history == [
        {"role": "assistant", "content": "[产物: My Doc (id=art1)]"}
    ]


async def test_other_agent_rendered_as_user_in_group(db, agents):
    alice = agents["alice"]
    orch = agents["orch"]
    conv_id = await _seed_conversation([alice, orch])
    # A message authored by orch, seen from alice's perspective.
    await _add_message(
        "o1",
        conv_id,
        "agent",
        [{"type": "text", "content": "from orch"}],
        100,
        agent_id=orch,
    )

    history = await cc.build_history_for(alice, conv_id)

    assert history == [
        {"role": "user", "content": "[Orchestrator] from orch"}
    ]


async def test_context_summary_prepended(db, agents):
    """Session Note (summary_type='session') is injected when ratio >= 0.50."""
    alice = agents["alice"]
    conv_id = await _seed_conversation([alice])
    now = now_ms()
    async with get_db() as session:
        from app.db.models import ContextSummary as CS
        sm = CS(
            id="cs_session",
            conversation_id=conv_id,
            summary="title: test\ncurrent_state: earlier stuff\nkey_decisions: []\n"
                    "files_touched: []\ncommands_run: []\nartifacts_produced: []\n"
                    "blockers: []\nopen_questions: []\nnext_steps: []\n"
                    "architecture_understanding: \"\"\n",
            covered_until_message_id="session",
            covered_until_created_at=50,
            source_message_count=3,
            token_estimate=10,
            created_at=now,
            summary_type="session",
            covers_up_to=50.0,
        )
        session.add(sm)
    # Large message after note → ratio >= 0.50 → Case C (Note + full text)
    big_content = "x" * 500_000
    await _add_message("u1", conv_id, "user", [{"type": "text", "content": big_content}], 100)

    history = await cc.build_history_for(alice, conv_id)

    # Session Note should be injected (first message)
    assert history[0]["role"] == "user"
    assert "earlier stuff" in history[0]["content"]
    assert {"role": "user", "content": big_content} in history


async def test_token_budget_drops_oldest_non_pinned(db, agents):
    alice = agents["alice"]
    conv_id = await _seed_conversation([alice], pinned=["pin1"])
    big = "x" * 400  # ~100 tokens
    await _add_message("pin1", conv_id, "user", [{"type": "text", "content": big}], 10)
    await _add_message("u2", conv_id, "user", [{"type": "text", "content": big}], 20)
    await _add_message("u3", conv_id, "user", [{"type": "text", "content": "tiny"}], 30)

    # Budget too small for both big messages; pinned survives, oldest non-pinned drops.
    history = await cc.build_history_for(
        alice, conv_id, BuildHistoryOptions(token_budget=120)
    )

    contents = [m["content"] for m in history]
    assert big in contents  # the pinned one is never dropped
    assert "tiny" in contents
    # Only one copy of `big` (the pinned), the non-pinned big u2 was dropped.
    assert contents.count(big) == 1


async def test_session_memory_yaml_injected_as_xml(db, agents):
    """Session Memory with valid YAML → injected as <session_note> XML."""
    alice = agents["alice"]
    conv_id = await _seed_conversation([alice])

    yaml_summary = (
        "title: 测试\n"
        "current_state: 正在测试\n"
        "key_decisions:\n"
        "  - \"[14:32] 决策A\"\n"
        "files_touched: []\n"
        "commands_run: []\n"
        "artifacts_produced: []\n"
        "blockers: []\n"
        "open_questions: []\n"
        "next_steps: []\n"
        "architecture_understanding: \"\"\n"
        "covers_up_to: 100.0\n"
    )

    now = now_ms()
    async with get_db() as session:
        from app.db.models import ContextSummary as CS

        sm = CS(
            id="cs_session_yaml",
            conversation_id=conv_id,
            summary=yaml_summary,
            covered_until_message_id="session",
            covered_until_created_at=100,
            source_message_count=3,
            token_estimate=50,
            created_at=now,
            summary_type="session",
            covers_up_to=100.0,
        )
        session.add(sm)

    big_content = "x" * 500_000
    await _add_message("u1", conv_id, "user", [{"type": "text", "content": big_content}], 200)

    history = await cc.build_history_for(alice, conv_id)

    found_xml = False
    for msg in history:
        if "<session_note" in msg.get("content", ""):
            found_xml = True
            assert "covers_up_to" in msg["content"]
            assert "<title>测试</title>" in msg["content"]
            break
    assert found_xml, "Expected <session_note> XML in injected history"


async def test_session_memory_plain_text_injected_as_session_memory(db, agents):
    """Session Memory with plain text (non-YAML) → injected as <session_memory> (legacy)."""
    alice = agents["alice"]
    conv_id = await _seed_conversation([alice])

    plain_summary = "This is a plain text summary, not YAML."

    now = now_ms()
    async with get_db() as session:
        from app.db.models import ContextSummary as CS

        sm = CS(
            id="cs_session_plain",
            conversation_id=conv_id,
            summary=plain_summary,
            covered_until_message_id="session",
            covered_until_created_at=100,
            source_message_count=3,
            token_estimate=50,
            created_at=now,
            summary_type="session",
            covers_up_to=100.0,
        )
        session.add(sm)

    big_content = "x" * 500_000
    await _add_message("u1", conv_id, "user", [{"type": "text", "content": big_content}], 200)

    history = await cc.build_history_for(alice, conv_id)

    found_legacy = False
    for msg in history:
        if "<session_memory" in msg.get("content", ""):
            found_legacy = True
            assert plain_summary in msg["content"]
            break
    assert found_legacy, "Expected <session_memory> legacy injection for plain text"
