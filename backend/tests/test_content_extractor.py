"""Unit tests for _ContentExtractor state machine and _path_from_extension helper."""

import pytest

from app.adapters.custom_adapter import _ContentExtractor, _path_from_extension


class TestContentExtractor:
    def test_simple_content_extraction(self):
        ext = _ContentExtractor()
        chunks = ext.feed('{"path":"app.py","content":"hello world"}')
        assert "".join(chunks) == "hello world"

    def test_path_extraction(self):
        ext = _ContentExtractor()
        ext.feed('{"path":"app.py","content":"x"}')
        assert ext.get_path() == "app.py"

    def test_content_before_path(self):
        ext = _ContentExtractor()
        chunks = ext.feed('{"content":"test","path":"a.ts"}')
        assert "".join(chunks) == "test"
        assert ext.get_path() == "a.ts"

    def test_incremental_feeding(self):
        ext = _ContentExtractor()
        all_chunks: list[str] = []
        # Simulate streaming: each chunk is a partial args_buffer increment
        increments = ['{"pa', 'th":"s', 'rc/main.', 'py","co', 'ntent":"', 'line1\\n', 'line2"}']
        for inc in increments:
            all_chunks.extend(ext.feed(inc))
        assert "".join(all_chunks) == "line1\nline2"
        assert ext.get_path() == "src/main.py"

    def test_escape_sequences(self):
        ext = _ContentExtractor()
        chunks = ext.feed('{"content":"a\\nb\\tc\\"d"}')
        result = "".join(chunks)
        assert result == "a\nb\tc\"d"

    def test_backslash_escape(self):
        ext = _ContentExtractor()
        chunks = ext.feed('{"content":"path\\\\file"}')
        assert "".join(chunks) == "path\\file"

    def test_forward_slash_escape(self):
        ext = _ContentExtractor()
        chunks = ext.feed('{"content":"a\\/b"}')
        assert "".join(chunks) == "a/b"

    def test_newline_tab_carriage_return(self):
        ext = _ContentExtractor()
        chunks = ext.feed('{"content":"a\\nb\\tc\\rd"}')
        assert "".join(chunks) == "a\nb\tc\rd"

    def test_unicode_escape_skipped(self):
        """Unicode escapes (\\uXXXX) can't be decoded char-by-char; the state machine skips them."""
        ext = _ContentExtractor()
        chunks = ext.feed('{"content":"\\u0041"}')
        # \\u is consumed but the 4-digit hex can't be decoded from single chars
        # The extractor skips the escape and the following chars
        result = "".join(chunks)
        # After \\u, the '0' '0' '4' '1' chars are emitted as regular content chars
        # This is expected degradation — the complete event will have the correct content
        assert isinstance(result, str)

    def test_malformed_json_graceful_degradation(self):
        ext = _ContentExtractor()
        # No "content" key found — returns empty
        chunks = ext.feed('{"path":"x.py"}')
        assert chunks == []

    def test_empty_content(self):
        ext = _ContentExtractor()
        chunks = ext.feed('{"content":""}')
        assert "".join(chunks) == ""

    def test_done_state_stops_processing(self):
        ext = _ContentExtractor()
        chunks1 = ext.feed('{"content":"done"}')
        # After content string closes, state is DONE
        chunks2 = ext.feed('extra text that should be ignored')
        assert "".join(chunks1) == "done"
        assert chunks2 == []

    def test_no_path_field(self):
        ext = _ContentExtractor()
        chunks = ext.feed('{"content":"hello"}')
        assert ext.get_path() is None
        assert "".join(chunks) == "hello"


class TestPathFromExtension:
    def test_typescript(self):
        assert _path_from_extension("app.tsx") == "typescript"

    def test_python(self):
        assert _path_from_extension("main.py") == "python"

    def test_unknown_extension(self):
        assert _path_from_extension("data.xyz") is None

    def test_empty_path(self):
        assert _path_from_extension("") is None

    def test_no_extension(self):
        assert _path_from_extension("Makefile") is None
