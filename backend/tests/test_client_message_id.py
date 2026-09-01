"""Regression tests: clientMessageId round-trip on send → message.added echo.

The sender's frontend inserts an optimistic temp_* message before POSTing. The
broadcast ``message.added`` event echoes the supplied ``clientMessageId`` so the
sender can claim the temp message at event-arrival time (no double render);
without the field (old clients) the event carries ``clientMessageId: None``.
"""

import asyncio

import pytest

from app.schemas.events import MessageAddedEvent, MessageRecord
from app.services import conversation_service as cs
from app.services.event_bus import event_bus


class _CapturingRunner:
    """Stub AgentRunner that records run() kwargs instead of executing.

    Keeps the subscriber queue clean of async run events so tests can drain it
    deterministically after the (synchronous) message.added publish.
    """

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


def _drain_messages_added(queue: asyncio.Queue) -> list[MessageAddedEvent]:
    events = []
    while True:
        try:
            event = queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        if isinstance(event, MessageAddedEvent):
            events.append(event)
    return events


async def test_send_message_echoes_client_message_id(db, agents, capturing_runner):
    """clientMessageId supplied with the send is echoed verbatim on the broadcast."""
    conv = await cs.create_conversation(mode="single", agent_ids=[agents["alice"]])
    async with event_bus.subscribe() as queue:
        await cs.send_message(
            conversation_id=conv.id,
            content="hello",
            client_message_id="temp_abc123",
        )
        added = _drain_messages_added(queue)

    assert len(added) == 1
    event = added[0]
    assert event.client_message_id == "temp_abc123"
    assert event.message.role == "user"
    # Wire format must use the camelCase alias for the SSE clients.
    wire = event.model_dump(by_alias=True)
    assert wire["clientMessageId"] == "temp_abc123"


async def test_send_message_without_client_message_id_is_none(db, agents, capturing_runner):
    """Callers that don't send a clientMessageId keep the old wire shape (None)."""
    conv = await cs.create_conversation(mode="single", agent_ids=[agents["alice"]])
    async with event_bus.subscribe() as queue:
        await cs.send_message(conversation_id=conv.id, content="hello")
        added = _drain_messages_added(queue)

    assert len(added) == 1
    assert added[0].client_message_id is None
    assert added[0].model_dump(by_alias=True)["clientMessageId"] is None


async def test_message_added_event_defaults_to_none():
    """Schema default: building the event without the field leaves it None."""
    event = MessageAddedEvent(
        conversation_id="conv_1",
        timestamp=1,
        message=MessageRecord(
            id="msg_1",
            conversation_id="conv_1",
            role="user",
            parts=[],
            status="complete",
            mentioned_agent_ids=[],
            created_at=1,
        ),
    )
    assert event.client_message_id is None


async def test_api_send_message_forwards_client_message_id(db, agents, api_client, monkeypatch):
    """API layer: POST /conversations/{id}/messages forwards clientMessageId down."""
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
        f"/api/conversations/{conv.id}/messages",
        json={"content": "hi", "clientMessageId": "temp_xyz"},
    )
    assert resp.status_code == 202, resp.text
    assert captured.get("client_message_id") == "temp_xyz"


async def test_api_send_message_without_client_message_id(db, agents, api_client, monkeypatch):
    """Old clients omitting clientMessageId still send fine (None end to end)."""
    captured: dict = {}

    async def _fake_send_message(**kwargs):
        captured.update(kwargs)

        class _Result:
            message_id = "msg_fake"
            run_ids = []
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
    assert captured.get("client_message_id") is None
