"""Unit tests for the DeltaFlusher coalescer.

Tests cover:
- Within-window buffering returns None; window elapsed returns merged event
- Different (part_index, delta_type) keys have independent buffers
- flush() drains all pending buffers in insertion order
- flush_for() drains only the specified key
- Text concatenation order is preserved
"""

from app.adapters._delta_flusher import DeltaFlusher

# ─── feed: within-window buffering ──────────────────────────────────────────


def test_feed_within_window_returns_none():
    """Deltas within the window are buffered; feed returns None."""
    flusher = DeltaFlusher(window_ms=50)
    ts = 1000

    result1 = flusher.feed("msg1", 0, "text.append", "abc", "conv1", timestamp=ts)
    result2 = flusher.feed("msg1", 0, "text.append", "def", "conv1", timestamp=ts + 10)
    result3 = flusher.feed("msg1", 0, "text.append", "ghi", "conv1", timestamp=ts + 20)

    assert result1 is None
    assert result2 is None
    assert result3 is None


def test_feed_window_elapsed_returns_merged_event():
    """When the window elapses, feed returns the merged event (including current delta)."""
    flusher = DeltaFlusher(window_ms=50)
    ts = 1000

    flusher.feed("msg1", 0, "text.append", "abc", "conv1", timestamp=ts)
    flusher.feed("msg1", 0, "text.append", "def", "conv1", timestamp=ts + 10)

    # Window elapses (>= 50ms since first delta); current delta "ghi" is included
    merged = flusher.feed("msg1", 0, "text.append", "ghi", "conv1", timestamp=ts + 50)

    assert merged is not None
    assert merged.delta["type"] == "text.append"
    assert merged.delta["text"] == "abcdefghi"
    assert merged.message_id == "msg1"
    assert merged.part_index == 0
    assert merged.conversation_id == "conv1"


def test_feed_starts_new_window_after_merge():
    """After a window-elapsed merge, the buffer is cleared; next delta starts a new window."""
    flusher = DeltaFlusher(window_ms=50)
    ts = 1000

    flusher.feed("msg1", 0, "text.append", "abc", "conv1", timestamp=ts)
    merged = flusher.feed("msg1", 0, "text.append", "def", "conv1", timestamp=ts + 50)
    assert merged is not None
    assert merged.delta["text"] == "abcdef"

    # New window: "ghi" is buffered, returns None
    result = flusher.feed("msg1", 0, "text.append", "ghi", "conv1", timestamp=ts + 60)
    assert result is None

    # Next window elapse: merged event contains only "ghi" + "jkl"
    merged2 = flusher.feed("msg1", 0, "text.append", "jkl", "conv1", timestamp=ts + 110)
    assert merged2 is not None
    assert merged2.delta["text"] == "ghijkl"


# ─── independent buffers per key ─────────────────────────────────────────────


def test_different_keys_are_independent():
    """Deltas with different (part_index, delta_type) have independent buffers."""
    flusher = DeltaFlusher(window_ms=50)
    ts = 1000

    # Feed text.append for part 0 and thinking.append for part 1 — both within window
    r1 = flusher.feed("msg1", 0, "text.append", "hello", "conv1", timestamp=ts)
    r2 = flusher.feed("msg1", 1, "thinking.append", "world", "conv1", timestamp=ts + 10)
    assert r1 is None
    assert r2 is None

    # Elapse window for part 0 only (50ms since ts=1000); "!" is included in merge
    r3 = flusher.feed("msg1", 0, "text.append", "!", "conv1", timestamp=ts + 50)
    assert r3 is not None
    assert r3.delta["text"] == "hello!"
    assert r3.part_index == 0

    # Part 1 buffer is independent — window started at ts+10=1010
    # At ts+60=1060, 50ms has elapsed (1060-1010=50 >= 50) → merged event
    r4 = flusher.feed("msg1", 1, "thinking.append", "!", "conv1", timestamp=ts + 60)
    assert r4 is not None
    assert r4.delta["text"] == "world!"
    assert r4.part_index == 1


def test_different_part_indices_are_independent():
    """Deltas with different part_index but same delta_type are independent."""
    flusher = DeltaFlusher(window_ms=50)
    ts = 1000

    r1 = flusher.feed("msg1", 0, "text.append", "aaa", "conv1", timestamp=ts)
    r2 = flusher.feed("msg1", 2, "text.append", "bbb", "conv1", timestamp=ts + 5)
    assert r1 is None
    assert r2 is None

    # Flush both
    events = flusher.flush()
    assert len(events) == 2
    texts = {e.delta["text"] for e in events}
    assert texts == {"aaa", "bbb"}


# ─── flush() ─────────────────────────────────────────────────────────────────


def test_flush_returns_all_pending_and_clears():
    """flush() returns all pending merged events in insertion order and clears buffers."""
    flusher = DeltaFlusher(window_ms=50)
    ts = 1000

    flusher.feed("msg1", 0, "text.append", "first", "conv1", timestamp=ts)
    flusher.feed("msg1", 1, "thinking.append", "second", "conv1", timestamp=ts)

    events = flusher.flush()
    assert len(events) == 2
    assert events[0].delta["text"] == "first"
    assert events[0].part_index == 0
    assert events[1].delta["text"] == "second"
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

    for chunk in ["abc", "def", "ghi"]:
        flusher.feed("msg1", 0, "text.append", chunk, "conv1", timestamp=ts)
        ts += 10

    events = flusher.flush()
    assert len(events) == 1
    assert events[0].delta["text"] == "abcdefghi"


# ─── flush_for() ─────────────────────────────────────────────────────────────


def test_flush_for_returns_only_specified_key():
    """flush_for() returns only the buffer for the specified (part_index, delta_type)."""
    flusher = DeltaFlusher(window_ms=50)
    ts = 1000

    flusher.feed("msg1", 0, "text.append", "text-content", "conv1", timestamp=ts)
    flusher.feed("msg1", 1, "thinking.append", "thinking-content", "conv1", timestamp=ts)

    # Flush only the text part
    event = flusher.flush_for(0, "text.append")
    assert event is not None
    assert event.delta["text"] == "text-content"
    assert event.part_index == 0

    # The thinking buffer is still pending
    events = flusher.flush()
    assert len(events) == 1
    assert events[0].delta["text"] == "thinking-content"


def test_flush_for_nonexistent_key_returns_none():
    """flush_for() on a key with no buffer returns None."""
    flusher = DeltaFlusher(window_ms=50)
    assert flusher.flush_for(0, "text.append") is None


def test_flush_for_after_flush_returns_none():
    """flush_for() returns None after the key was already flushed."""
    flusher = DeltaFlusher(window_ms=50)
    flusher.feed("msg1", 0, "text.append", "abc", "conv1", timestamp=1000)

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

    # 200 chunks over 200ms with 50ms windows → ~4 merged events (far fewer than 200)
    assert merged_count < total_chunks
    assert merged_count <= 10  # generous upper bound


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
