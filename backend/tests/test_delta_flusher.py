"""Unit tests for the DeltaFlusher coalescer.

Tests cover:
- First delta of each key passes through immediately (first-token latency)
- Subsequent deltas within the window are buffered (feed returns None)
- Window elapsed returns merged event (deltas after the first of the window)
- Different (part_index, delta_type) keys have independent buffers
- Passthrough happens exactly once per key (event volume stays +1 per key)
- flush() drains all pending buffers in insertion order
- flush_for() drains only the specified key
- Text concatenation order is preserved
"""

from app.adapters._delta_flusher import DeltaFlusher

# ─── feed: first-delta passthrough ──────────────────────────────────────────


def test_first_delta_passes_through_immediately():
    """The first delta of a key is returned immediately (no 50ms window wait)."""
    flusher = DeltaFlusher(window_ms=50)

    result = flusher.feed("msg1", 0, "text.append", "abc", "conv1", timestamp=1000)

    assert result is not None
    assert result.delta == {"type": "text.append", "text": "abc"}
    assert result.message_id == "msg1"
    assert result.part_index == 0
    assert result.conversation_id == "conv1"
    assert result.timestamp == 1000


def test_second_delta_enters_window_returns_none():
    """After the passthrough, deltas within the window are buffered (return None)."""
    flusher = DeltaFlusher(window_ms=50)
    ts = 1000

    flusher.feed("msg1", 0, "text.append", "abc", "conv1", timestamp=ts)
    result2 = flusher.feed("msg1", 0, "text.append", "def", "conv1", timestamp=ts + 10)
    result3 = flusher.feed("msg1", 0, "text.append", "ghi", "conv1", timestamp=ts + 20)

    assert result2 is None
    assert result3 is None


def test_passthrough_happens_once_per_key():
    """After a window merge clears the buffer, the next delta buffers — no second passthrough."""
    flusher = DeltaFlusher(window_ms=50)
    ts = 1000

    # First delta passes through
    first = flusher.feed("msg1", 0, "text.append", "a", "conv1", timestamp=ts)
    assert first is not None
    # Window merge
    flusher.feed("msg1", 0, "text.append", "b", "conv1", timestamp=ts + 10)
    merged = flusher.feed("msg1", 0, "text.append", "c", "conv1", timestamp=ts + 60)
    assert merged is not None
    assert merged.delta["text"] == "bc"

    # Buffer cleared — next delta starts a new window (buffered), NOT passthrough
    assert flusher.feed("msg1", 0, "text.append", "d", "conv1", timestamp=ts + 70) is None


# ─── feed: window merge ──────────────────────────────────────────────────────


def test_feed_window_elapsed_returns_merged_event():
    """When the window elapses, feed returns the merged event (excluding the passthrough delta)."""
    flusher = DeltaFlusher(window_ms=50)
    ts = 1000

    first = flusher.feed("msg1", 0, "text.append", "abc", "conv1", timestamp=ts)
    flusher.feed("msg1", 0, "text.append", "def", "conv1", timestamp=ts + 10)

    # Window elapses (>= 50ms since the first buffered delta); "ghi" is included
    merged = flusher.feed("msg1", 0, "text.append", "ghi", "conv1", timestamp=ts + 60)

    assert first is not None
    assert first.delta["text"] == "abc"
    assert merged is not None
    assert merged.delta["type"] == "text.append"
    assert merged.delta["text"] == "defghi"
    assert merged.message_id == "msg1"
    assert merged.part_index == 0
    assert merged.conversation_id == "conv1"


def test_feed_starts_new_window_after_merge():
    """After a window-elapsed merge, the buffer is cleared; next delta starts a new window."""
    flusher = DeltaFlusher(window_ms=50)
    ts = 1000

    flusher.feed("msg1", 0, "text.append", "abc", "conv1", timestamp=ts)
    flusher.feed("msg1", 0, "text.append", "def", "conv1", timestamp=ts + 10)
    merged = flusher.feed("msg1", 0, "text.append", "ghi", "conv1", timestamp=ts + 60)
    assert merged is not None
    assert merged.delta["text"] == "defghi"

    # New window: "jkl" is buffered, returns None
    result = flusher.feed("msg1", 0, "text.append", "jkl", "conv1", timestamp=ts + 70)
    assert result is None

    # Next window elapse: merged event contains only "jkl" + "mno"
    merged2 = flusher.feed("msg1", 0, "text.append", "mno", "conv1", timestamp=ts + 120)
    assert merged2 is not None
    assert merged2.delta["text"] == "jklmno"


# ─── independent buffers per key ─────────────────────────────────────────────


def test_different_keys_are_independent():
    """Keys pass through independently and have independent buffers."""
    flusher = DeltaFlusher(window_ms=50)
    ts = 1000

    # First delta of each key passes through immediately
    r1 = flusher.feed("msg1", 0, "text.append", "hello", "conv1", timestamp=ts)
    r2 = flusher.feed("msg1", 1, "thinking.append", "world", "conv1", timestamp=ts + 10)
    assert r1 is not None
    assert r1.delta["text"] == "hello"
    assert r1.part_index == 0
    assert r2 is not None
    assert r2.delta["text"] == "world"
    assert r2.part_index == 1

    # Subsequent deltas of both keys buffer independently
    assert flusher.feed("msg1", 0, "text.append", "A", "conv1", timestamp=ts + 20) is None
    assert flusher.feed("msg1", 1, "thinking.append", "C", "conv1", timestamp=ts + 30) is None

    # Elapse window for part 0 only (50ms since its buffer opened at ts+20);
    # merged event = buffered "A" + current "B"
    r3 = flusher.feed("msg1", 0, "text.append", "B", "conv1", timestamp=ts + 70)
    assert r3 is not None
    assert r3.delta["text"] == "AB"

    # Part 1 buffer is independent — window opened at ts+30=1030
    # At ts+80=1080, 50ms has elapsed (1080-1030=50 >= 50) → merged event
    r4 = flusher.feed("msg1", 1, "thinking.append", "D", "conv1", timestamp=ts + 80)
    assert r4 is not None
    assert r4.delta["text"] == "CD"


def test_different_part_indices_are_independent():
    """Deltas with different part_index but same delta_type are independent."""
    flusher = DeltaFlusher(window_ms=50)
    ts = 1000

    r1 = flusher.feed("msg1", 0, "text.append", "aaa", "conv1", timestamp=ts)
    r2 = flusher.feed("msg1", 2, "text.append", "bbb", "conv1", timestamp=ts + 5)
    assert r1 is not None
    assert r2 is not None

    # Both keys buffer their second delta
    assert flusher.feed("msg1", 0, "text.append", "1", "conv1", timestamp=ts + 10) is None
    assert flusher.feed("msg1", 2, "text.append", "2", "conv1", timestamp=ts + 15) is None

    # Flush both
    events = flusher.flush()
    assert len(events) == 2
    texts = {e.delta["text"] for e in events}
    assert texts == {"1", "2"}


# ─── flush() ─────────────────────────────────────────────────────────────────


def test_flush_returns_all_pending_and_clears():
    """flush() returns all pending merged events in insertion order and clears buffers."""
    flusher = DeltaFlusher(window_ms=50)
    ts = 1000

    # First delta of each key passes through (no buffer); second delta buffers
    flusher.feed("msg1", 0, "text.append", "first", "conv1", timestamp=ts)
    flusher.feed("msg1", 1, "thinking.append", "second", "conv1", timestamp=ts)
    flusher.feed("msg1", 0, "text.append", "first2", "conv1", timestamp=ts + 10)
    flusher.feed("msg1", 1, "thinking.append", "second2", "conv1", timestamp=ts + 10)

    events = flusher.flush()
    assert len(events) == 2
    assert events[0].delta["text"] == "first2"
    assert events[0].part_index == 0
    assert events[1].delta["text"] == "second2"
    assert events[1].part_index == 1

    # Buffers are cleared — second flush returns empty
    assert flusher.flush() == []


def test_flush_empty_returns_empty_list():
    """flush() on an empty flusher returns an empty list."""
    flusher = DeltaFlusher()
    assert flusher.flush() == []


def test_flush_preserves_concatenation_order():
    """Multiple deltas within one window are concatenated in feed order."""
    flusher = DeltaFlusher(window_ms=100)
    ts = 1000

    events: list = []
    for chunk in ["abc", "def", "ghi"]:
        result = flusher.feed("msg1", 0, "text.append", chunk, "conv1", timestamp=ts)
        if result is not None:
            events.append(result)
        ts += 10

    # First chunk passed through; the rest were buffered and merge on flush()
    assert events[0].delta["text"] == "abc"
    events.extend(flusher.flush())
    assert len(events) == 2
    assert events[1].delta["text"] == "defghi"


# ─── flush_for() ─────────────────────────────────────────────────────────────


def test_flush_for_returns_only_specified_key():
    """flush_for() returns only the buffer for the specified (part_index, delta_type)."""
    flusher = DeltaFlusher(window_ms=50)
    ts = 1000

    # First delta of each key passes through (no buffer); second delta buffers
    flusher.feed("msg1", 0, "text.append", "text-content", "conv1", timestamp=ts)
    flusher.feed("msg1", 1, "thinking.append", "thinking-content", "conv1", timestamp=ts)
    flusher.feed("msg1", 0, "text.append", "-more", "conv1", timestamp=ts + 10)
    flusher.feed("msg1", 1, "thinking.append", "-more", "conv1", timestamp=ts + 10)

    # Flush only the text part (buffer holds the post-passthrough delta)
    event = flusher.flush_for(0, "text.append")
    assert event is not None
    assert event.delta["text"] == "-more"
    assert event.part_index == 0

    # The thinking buffer is still pending
    events = flusher.flush()
    assert len(events) == 1
    assert events[0].delta["text"] == "-more"


def test_flush_for_nonexistent_key_returns_none():
    """flush_for() on a key with no buffer returns None."""
    flusher = DeltaFlusher(window_ms=50)
    assert flusher.flush_for(0, "text.append") is None


def test_flush_for_after_flush_returns_none():
    """flush_for() returns None after the key was already flushed."""
    flusher = DeltaFlusher(window_ms=50)
    flusher.feed("msg1", 0, "text.append", "abc", "conv1", timestamp=1000)
    flusher.feed("msg1", 0, "text.append", "def", "conv1", timestamp=1010)

    flusher.flush()
    assert flusher.flush_for(0, "text.append") is None


# ─── integration: coalescer reduces event count ──────────────────────────────


def test_coalescer_reduces_event_count():
    """Feeding many deltas within a short time produces far fewer merged events."""
    flusher = DeltaFlusher(window_ms=50)
    ts = 1000
    total_chunks = 200

    merged_count = 0
    for i in range(total_chunks):
        result = flusher.feed(
            "msg1", 0, "text.append", f"chunk{i} ", "conv1", timestamp=ts + i
        )
        if result is not None:
            merged_count += 1

    # Flush remaining
    merged_count += len(flusher.flush())

    # 200 chunks over 200ms with 50ms windows → passthrough + ~4 merged events
    # (far fewer than 200; exactly +1 event vs. the pre-passthrough flusher)
    assert merged_count < total_chunks
    assert merged_count <= 10  # generous upper bound


def test_coalescer_event_count_is_exactly_plus_one_per_key():
    """The passthrough adds exactly one event per key versus pure windowing."""
    flusher = DeltaFlusher(window_ms=50)
    ts = 1000
    total_chunks = 200

    count = 0
    for i in range(total_chunks):
        result = flusher.feed(
            "msg1", 0, "text.append", f"chunk{i} ", "conv1", timestamp=ts + i
        )
        if result is not None:
            count += 1
    count += len(flusher.flush())

    # Pure windowing over 200ms/50ms windows yields ~4 merges; the passthrough
    # contributes exactly +1 (the first delta).
    assert count == 5


def test_coalescer_preserves_final_content():
    """The concatenation of all merged events equals the concatenation of all inputs."""
    flusher = DeltaFlusher(window_ms=50)
    ts = 1000
    chunks = [f"line_{i}\n" for i in range(50)]
    expected = "".join(chunks)

    merged_texts: list[str] = []
    for i, chunk in enumerate(chunks):
        result = flusher.feed("msg1", 0, "text.append", chunk, "conv1", timestamp=ts + i)
        if result is not None:
            merged_texts.append(result.delta["text"])

    merged_texts.extend(e.delta["text"] for e in flusher.flush())

    actual = "".join(merged_texts)
    assert actual == expected
