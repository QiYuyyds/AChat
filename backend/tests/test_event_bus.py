"""Tests for the in-process event bus (phase 2 + SSE replay/resync)."""

import asyncio

from app.schemas.events import HeartbeatEvent
from app.services.event_bus import EventBus, _Subscriber


def _heartbeat(ts: int) -> HeartbeatEvent:
    return HeartbeatEvent(conversation_id="c1", timestamp=ts)


async def test_publish_reaches_subscriber():
    bus = EventBus()
    async with bus.subscribe() as sub:
        assert bus.subscriber_count == 1
        event = HeartbeatEvent(conversation_id="c1", timestamp=1)
        bus.publish(event)
        seq, received = await asyncio.wait_for(sub.queue.get(), timeout=1)
        assert received is event
        assert seq == 1
    # Subscriber removed on context exit.
    assert bus.subscriber_count == 0


async def test_publish_fans_out_to_all_subscribers():
    bus = EventBus()
    async with bus.subscribe() as s1, bus.subscribe() as s2:
        assert bus.subscriber_count == 2
        event = HeartbeatEvent(conversation_id="c1", timestamp=2)
        bus.publish(event)
        seq1, received1 = await asyncio.wait_for(s1.queue.get(), timeout=1)
        seq2, received2 = await asyncio.wait_for(s2.queue.get(), timeout=1)
        assert (seq1, seq2) == (1, 1)
        assert received1 is event and received2 is event


async def test_publish_with_no_subscribers_is_noop():
    bus = EventBus()
    bus.publish(HeartbeatEvent(conversation_id="c1", timestamp=3))
    assert bus.subscriber_count == 0


async def test_publish_assigns_monotonically_increasing_seq():
    """Every publish gets the next global seq, shared by queue and replay buffer."""
    bus = EventBus()
    async with bus.subscribe() as sub:
        events = [_heartbeat(ts) for ts in (1, 2, 3)]
        for e in events:
            bus.publish(e)

        seqs = []
        for _ in events:
            seq, _ = await asyncio.wait_for(sub.queue.get(), timeout=1)
            seqs.append(seq)
        assert seqs == [1, 2, 3]
        assert bus.last_seq == 3
        assert [seq for seq, _ in bus._replay] == [1, 2, 3]


async def test_replay_buffer_truncates_to_maxlen():
    """The replay buffer keeps only the most recent SSE_REPLAY_BUFFER entries."""
    bus = EventBus(replay_buffer_size=3)
    for ts in range(1, 6):  # 5 events → seqs 1..5
        bus.publish(_heartbeat(ts))

    assert [seq for seq, _ in bus._replay] == [3, 4, 5]

    # Client at seq 2 has a hole (seq 2 was truncated away) → unreplayable.
    assert bus.replay_since(2) is None
    # Client at seq 3 → replay 4..5; at seq 5 (caught up) → empty replay.
    assert [seq for seq, _ in bus.replay_since(3)] == [4, 5]
    assert bus.replay_since(5) == []


async def test_replay_since_unreplayable_ids_return_none():
    bus = EventBus(replay_buffer_size=3)
    for ts in range(1, 4):  # seqs 1..3
        bus.publish(_heartbeat(ts))

    # Gap beyond the window / unknown old id.
    assert bus.replay_since(0) is None
    # Ahead-of-current id (server restart reset the counter).
    assert bus.replay_since(99) is None
    # Empty buffer (nothing published / buffering disabled).
    assert EventBus(replay_buffer_size=0).replay_since(1) is None


async def test_overflow_drops_oldest():
    bus = EventBus()
    async with bus.subscribe():
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
        assert (first[1].timestamp, second[1].timestamp) == (2, 3)


async def test_overflow_marks_subscriber_for_resync():
    """Shedding events sets needs_resync; a healthy subscriber stays clean."""
    bus = EventBus()
    async with bus.subscribe() as healthy, bus.subscribe() as slow:
        slow.queue = asyncio.Queue(maxsize=1)

        bus.publish(_heartbeat(1))
        bus.publish(_heartbeat(2))  # slow subscriber overflows here

        assert slow.needs_resync is True
        assert healthy.needs_resync is False

        # The flag is consumed (cleared) by the SSE layer, not by the bus.
        seq, _ = await asyncio.wait_for(slow.queue.get(), timeout=1)
        assert seq == 2
        assert slow.needs_resync is True


async def test_publish_target_is_ignored_all_subscribers_receive():
    """user_id publish targets are retained for API compat but ignored.

    Single-user mode: the bus broadcasts every event to every subscriber
    (local user filtering removed with the dual-DB routing change).
    """
    bus = EventBus()
    async with bus.subscribe(user_id="alice") as s_alice, bus.subscribe(user_id="bob") as s_bob:
        event = HeartbeatEvent(conversation_id="c1", timestamp=10)
        bus.publish(event, user_id="alice")

        _, r_alice = await asyncio.wait_for(s_alice.queue.get(), timeout=1)
        _, r_bob = await asyncio.wait_for(s_bob.queue.get(), timeout=1)
        assert r_alice is event
        assert r_bob is event


async def test_broadcast_reaches_all_users():
    bus = EventBus()
    async with bus.subscribe(user_id="alice") as s_alice, bus.subscribe(user_id="bob") as s_bob:
        event = HeartbeatEvent(conversation_id="c1", timestamp=20)
        bus.publish(event, user_id=None)

        _, r_alice = await asyncio.wait_for(s_alice.queue.get(), timeout=1)
        _, r_bob = await asyncio.wait_for(s_bob.queue.get(), timeout=1)
        assert r_alice is event
        assert r_bob is event
