"""Tests for the in-process event bus (phase 2)."""

import asyncio

from app.schemas.events import HeartbeatEvent
from app.services.event_bus import EventBus, _Subscriber


async def test_publish_reaches_subscriber():
    bus = EventBus()
    async with bus.subscribe() as queue:
        assert bus.subscriber_count == 1
        event = HeartbeatEvent(conversation_id="c1", timestamp=1)
        bus.publish(event)
        received = await asyncio.wait_for(queue.get(), timeout=1)
        assert received is event
    # Subscriber removed on context exit.
    assert bus.subscriber_count == 0


async def test_publish_fans_out_to_all_subscribers():
    bus = EventBus()
    async with bus.subscribe() as q1, bus.subscribe() as q2:
        assert bus.subscriber_count == 2
        event = HeartbeatEvent(conversation_id="c1", timestamp=2)
        bus.publish(event)
        assert (await asyncio.wait_for(q1.get(), timeout=1)) is event
        assert (await asyncio.wait_for(q2.get(), timeout=1)) is event


async def test_publish_with_no_subscribers_is_noop():
    bus = EventBus()
    bus.publish(HeartbeatEvent(conversation_id="c1", timestamp=3))
    assert bus.subscriber_count == 0


async def test_overflow_drops_oldest():
    bus = EventBus()
    async with bus.subscribe() as queue:
        small_sub = _Subscriber(user_id=None, queue=asyncio.Queue(maxsize=2))
        real_subs = list(bus._subscribers)
        for s in real_subs:
            bus._subscribers.discard(s)
        bus._subscribers.add(small_sub)

        e1 = HeartbeatEvent(conversation_id="c", timestamp=1)
        e2 = HeartbeatEvent(conversation_id="c", timestamp=2)
        e3 = HeartbeatEvent(conversation_id="c", timestamp=3)
        bus.publish(e1)
        bus.publish(e2)
        bus.publish(e3)

        first = small_sub.queue.get_nowait()
        second = small_sub.queue.get_nowait()
        assert (first.timestamp, second.timestamp) == (2, 3)


async def test_user_filtered_delivery():
    bus = EventBus()
    async with bus.subscribe(user_id="alice") as q_alice, bus.subscribe(user_id="bob") as q_bob:
        event = HeartbeatEvent(conversation_id="c1", timestamp=10)
        bus.publish(event, user_id="alice")

        received = await asyncio.wait_for(q_alice.get(), timeout=1)
        assert received is event
        assert q_bob.empty()


async def test_broadcast_reaches_all_users():
    bus = EventBus()
    async with bus.subscribe(user_id="alice") as q_alice, bus.subscribe(user_id="bob") as q_bob:
        event = HeartbeatEvent(conversation_id="c1", timestamp=20)
        bus.publish(event, user_id=None)

        assert (await asyncio.wait_for(q_alice.get(), timeout=1)) is event
        assert (await asyncio.wait_for(q_bob.get(), timeout=1)) is event
