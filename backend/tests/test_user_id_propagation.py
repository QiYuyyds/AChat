"""Regression tests: top-level chat entry points must propagate user_id to runs.

Without user_id the PromptAssembler ProfileSource skips the profile slot
("missing user_id"), so agents never see the user's personal profile
(name / preferences) even though it is stored in user_preferences.
"""

import pytest

from app.services import conversation_service as cs


class _CapturingRunner:
    """Stub AgentRunner that records run() kwargs instead of executing."""

    def __init__(self):
        self.captured: list[dict] = []

    def run(self, **kwargs):
        self.captured.append(kwargs)

        class _Handle:
            run_id = "run_fake"

        return _Handle()

    def abort(self, run_id: str) -> bool:
        return True


@pytest.fixture
def capturing_runner(monkeypatch):
    runner = _CapturingRunner()
    monkeypatch.setattr(cs, "get_agent_runner", lambda: runner)
    return runner


async def test_send_message_propagates_user_id(db, agents, capturing_runner):
    conv = await cs.create_conversation(mode="single", agent_ids=[agents["alice"]])
    await cs.send_message(
        conversation_id=conv.id, content="hello", user_id="test_user_1"
    )
    assert capturing_runner.captured, "runner.run was not called"
    assert capturing_runner.captured[0]["user_id"] == "test_user_1"


async def test_send_message_defaults_to_none_user_id(db, agents, capturing_runner):
    """Callers that don't pass user_id keep working (backward compatible)."""
    conv = await cs.create_conversation(mode="single", agent_ids=[agents["alice"]])
    await cs.send_message(conversation_id=conv.id, content="hello")
    assert capturing_runner.captured[0]["user_id"] is None


async def test_regenerate_propagates_user_id(db, agents, capturing_runner):
    conv = await cs.create_conversation(mode="single", agent_ids=[agents["alice"]])
    await cs.send_message(conversation_id=conv.id, content="hello")
    capturing_runner.captured.clear()

    await cs.regenerate_latest_response(conv.id, user_id="test_user_1")
    assert capturing_runner.captured, "runner.run was not called on regenerate"
    assert capturing_runner.captured[0]["user_id"] == "test_user_1"


async def test_edit_and_resend_propagates_user_id(db, agents, capturing_runner):
    conv = await cs.create_conversation(mode="single", agent_ids=[agents["alice"]])
    sent = await cs.send_message(
        conversation_id=conv.id, content="hello", user_id="test_user_1"
    )
    capturing_runner.captured.clear()

    msgs = await cs.list_messages(conv.id)
    await cs.edit_and_resend_latest_user_message(
        conv.id, msgs[0].id, "edited", user_id="test_user_1"
    )
    assert capturing_runner.captured, "runner.run was not called on edit+resend"
    assert capturing_runner.captured[0]["user_id"] == "test_user_1"
    assert sent.run_ids  # sanity: original send produced a run


async def test_api_send_message_passes_authenticated_user(db, agents, api_client, monkeypatch):
    """API layer: POST /conversations/{id}/messages forwards user.id down."""
    captured: dict = {}

    class _Handle:
        run_id = "run_fake"

    async def _fake_send_message(**kwargs):
        captured.update(kwargs)

        class _Result:
            message_id = "msg_fake"
            run_ids = ["run_fake"]
            messages = []
            deploy = None

        return _Result()

    import app.api.conversations as conversations_api

    monkeypatch.setattr(
        conversations_api.conversation_service, "send_message", _fake_send_message
    )

    conv = await cs.create_conversation(mode="single", agent_ids=[agents["alice"]])
    resp = await api_client.post(
        f"/api/conversations/{conv.id}/messages", json={"content": "hi"}
    )
    assert resp.status_code == 202, resp.text
    assert captured.get("user_id") == "test_user_1"
