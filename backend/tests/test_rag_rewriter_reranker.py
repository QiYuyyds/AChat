"""Unit tests for LLMRewriter and LLMReranker — generation + failure fallback."""

import json
import pytest
from app.rag.rewriter import LLMRewriter, HistoryMessage
from app.rag.reranker import LLMReranker


class TestLLMRewriter:
    def test_empty_query(self):
        rw = LLMRewriter(generate_fn=lambda s, u: "")
        assert rw.rewrite("", []) == []

    def test_no_generate_fn_fallback(self):
        rw = LLMRewriter(generate_fn=None)
        assert rw.rewrite("test query", []) == ["test query"]

    def test_single_query_mode(self):
        rw = LLMRewriter(generate_fn=lambda s, u: "", num_queries=1)
        assert rw.rewrite("test query", []) == ["test query"]

    def test_successful_rewrite(self):
        def mock_generate(system, user):
            return json.dumps({"queries": ["独立查询", "变体一", "变体二"]})

        rw = LLMRewriter(generate_fn=mock_generate, num_queries=3)
        results = rw.rewrite("原始问题", [HistoryMessage("user", "hello")])
        assert len(results) >= 1
        assert "独立查询" in results

    def test_generate_failure_fallback(self):
        """LLM failure should fallback to original query."""
        def mock_generate(system, user):
            raise RuntimeError("LLM unavailable")

        rw = LLMRewriter(generate_fn=mock_generate)
        results = rw.rewrite("original query", [])
        assert results == ["original query"]

    def test_malformed_json_fallback(self):
        def mock_generate(system, user):
            return "this is not json"

        rw = LLMRewriter(generate_fn=mock_generate)
        results = rw.rewrite("query", [])
        assert results == ["query"]

    def test_json_fence_stripping(self):
        """LLM sometimes wraps JSON in markdown code blocks."""
        def mock_generate(system, user):
            return '```json\n{"queries": ["q1", "q2", "q3"]}\n```'

        rw = LLMRewriter(generate_fn=mock_generate, num_queries=3)
        results = rw.rewrite("query", [])
        assert "q1" in results


class TestLLMReranker:
    class MockResult:
        def __init__(self, content, score=0.5, source=""):
            self.content = content
            self.score = score
            self.source = source

    def test_empty_results(self):
        rr = LLMReranker(generate_fn=lambda s, u: "")
        assert rr.rerank("query", [], 3) == []

    def test_single_result_no_llm(self):
        rr = LLMReranker(generate_fn=None)
        results = [self.MockResult("content")]
        out = rr.rerank("query", results, 3)
        assert len(out) == 1

    def test_successful_rerank(self):
        def mock_generate(system, user):
            return json.dumps({"scores": [{"idx": 0, "score": 3}, {"idx": 1, "score": 9}]})

        rr = LLMReranker(generate_fn=mock_generate)
        results = [self.MockResult("weak"), self.MockResult("strong")]
        out = rr.rerank("query", results, 2)
        assert len(out) == 2
        # Higher scored item should come first
        assert "strong" in out[0].content

    def test_rerank_failure_fallback(self):
        """LLM failure should preserve original order."""
        def mock_generate(system, user):
            raise RuntimeError("LLM unavailable")

        rr = LLMReranker(generate_fn=mock_generate)
        results = [self.MockResult("a"), self.MockResult("b")]
        out = rr.rerank("query", results, 2)
        assert len(out) == 2
        assert out[0].content == "a"

    def test_top_k_truncation(self):
        rr = LLMReranker(generate_fn=None)
        results = [self.MockResult(f"item{i}") for i in range(10)]
        out = rr.rerank("query", results, 3)
        assert len(out) == 3
