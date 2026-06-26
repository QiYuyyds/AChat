"""Unit tests for ShortTerm memory — pure in-memory deque, no DB."""

import pytest
from app.memory.short_term import ShortTerm


class TestShortTerm:
    def test_add_and_get(self):
        stm = ShortTerm(max_turns=5)
        stm.add("user", "hello")
        stm.add("assistant", "hi there")
        turns = stm.get()
        assert len(turns) == 2
        assert turns[0]["role"] == "user"
        assert turns[0]["content"] == "hello"
        assert turns[1]["role"] == "assistant"
        assert turns[1]["content"] == "hi there"

    def test_clear(self):
        stm = ShortTerm(max_turns=5)
        stm.add("user", "msg1")
        stm.add("assistant", "msg2")
        assert stm.count() == 2
        stm.clear()
        assert stm.count() == 0
        assert stm.get() == []

    def test_sliding_window(self):
        """When max_turns is exceeded, oldest entries are evicted."""
        stm = ShortTerm(max_turns=2)
        stm.add("user", "msg1")
        stm.add("assistant", "resp1")
        stm.add("user", "msg2")
        stm.add("assistant", "resp2")
        # max_turns=2 → maxlen=4 (2*2), so all 4 fit
        assert stm.count() == 4

        stm.add("user", "msg3")
        # Now 5 entries but maxlen=4, so msg1 evicted
        turns = stm.get()
        assert len(turns) == 4
        assert turns[0]["role"] == "assistant"
        assert turns[0]["content"] == "resp1"

    def test_count(self):
        stm = ShortTerm(max_turns=10)
        assert stm.count() == 0
        stm.add("user", "a")
        assert stm.count() == 1
        stm.add("assistant", "b")
        assert stm.count() == 2

    def test_empty_get(self):
        stm = ShortTerm(max_turns=5)
        assert stm.get() == []

    def test_max_turns_one(self):
        """Edge case: max_turns=1 → only 2 entries (1 user + 1 assistant)."""
        stm = ShortTerm(max_turns=1)
        stm.add("user", "u1")
        stm.add("assistant", "a1")
        stm.add("user", "u2")
        turns = stm.get()
        assert len(turns) == 2
        assert turns[0]["role"] == "assistant"
        assert turns[0]["content"] == "a1"
        assert turns[1]["role"] == "user"
        assert turns[1]["content"] == "u2"
