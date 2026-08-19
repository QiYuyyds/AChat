"""Unit tests for LLMReranker — generation + failure fallback."""

import json

from app.rag.reranker import LLMReranker


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
