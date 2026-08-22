"""Tests for graph extractor phase 2: LLMGraphExtractor, GraphExtractorFactory,
normalize_extraction_result, and Extractor compatibility wrapper.

Covers:
- LLMGraphExtractor.extract with valid/markdown/malformed/empty/None/exception LLM output
- normalize_extraction_result: dedup, attribute merge, reference resolution, unresolved discard
- LLMGraphExtractor.extract_batch concurrent extraction
- GraphExtractorFactory register/create round-trip + unknown name ValueError
- Extractor compatibility wrapper produces ExtractResult
- LLMGraphExtractor with schema parameter
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.graph.extractor import Extractor
from app.graph.extractors.base import GraphExtractor
from app.graph.extractors.factory import GraphExtractorFactory
from app.graph.extractors.llm import (
    DEFAULT_TRIPLE_EXTRACTION_PROMPT,
    SCHEMA_INSTRUCTION,
    LLMGraphExtractor,
)
from app.graph.types import ChunkRef, Entity, ExtractResult, Relation

# ── Helpers ──

VALID_RELATIONS_OUTPUT = json.dumps({
    "relations": [
        {
            "source": {"text": "Apple", "label": "Organization", "attributes": [{"text": "Cupertino", "label": "Location"}]},
            "target": {"text": "iPhone", "label": "Product", "attributes": []},
            "text": "Apple makes iPhone",
            "label": "RELATES_TO",
        },
        {
            "source": {"text": "Steve Jobs", "label": "Person", "attributes": []},
            "target": {"text": "Apple", "label": "Organization", "attributes": [{"text": "CEO", "label": "Role"}]},
            "text": "Steve Jobs founded Apple",
            "label": "WORKS_FOR",
        },
    ]
}, ensure_ascii=False)


def _make_llm_fn(response: str):
    """Create a mock llm_fn that returns the given response."""
    fn = MagicMock(return_value=response)
    return fn


# ── 6.1.1: Test LLMGraphExtractor.extract with valid LLM output ──

class TestLLMGraphExtractorExtract:
    def test_extract_valid_output(self):
        fn = _make_llm_fn(VALID_RELATIONS_OUTPUT)
        extractor = LLMGraphExtractor({"llm_fn": fn})
        result = extractor.extract("Apple makes iPhone. Steve Jobs founded Apple.")
        assert isinstance(result, ExtractResult)
        assert len(result.entities) > 0
        assert len(result.relations) > 0
        # Check that entities include Apple, iPhone, Steve Jobs
        entity_names = {e.name for e in result.entities}
        assert "Apple" in entity_names
        assert "iPhone" in entity_names
        assert "Steve Jobs" in entity_names

    def test_extract_returns_normalized_result(self):
        """Extract should return entities deduplicated."""
        fn = _make_llm_fn(json.dumps({
            "relations": [
                {
                    "source": {"text": "React", "label": "Concept", "attributes": []},
                    "target": {"text": "Vue", "label": "Concept", "attributes": []},
                    "label": "RELATES_TO",
                },
                {
                    "source": {"text": "React", "label": "Concept", "attributes": []},
                    "target": {"text": "Angular", "label": "Concept", "attributes": []},
                    "label": "RELATES_TO",
                },
            ]
        }, ensure_ascii=False))
        extractor = LLMGraphExtractor({"llm_fn": fn})
        result = extractor.extract("React relates to Vue and Angular")
        # React should appear only once
        react_count = sum(1 for e in result.entities if e.name == "React")
        assert react_count == 1


# ── 6.1.2: Test LLMGraphExtractor.extract with markdown-wrapped JSON ──

    def test_extract_markdown_wrapped_json(self):
        wrapped = f"```json\n{VALID_RELATIONS_OUTPUT}\n```"
        fn = _make_llm_fn(wrapped)
        extractor = LLMGraphExtractor({"llm_fn": fn})
        result = extractor.extract("test text")
        assert len(result.entities) > 0
        assert len(result.relations) > 0


# ── 6.1.3: Test LLMGraphExtractor.extract with malformed JSON ──

    def test_extract_malformed_json_trailing_comma(self):
        malformed = '{"relations": [{"source": {"text": "A", "label": "Person"}, "target": {"text": "B", "label": "Person"}, "label": "RELATES_TO",},],}'
        fn = _make_llm_fn(malformed)
        extractor = LLMGraphExtractor({"llm_fn": fn})
        result = extractor.extract("test text")
        # json_repair should handle trailing comma
        assert isinstance(result, ExtractResult)

    def test_extract_malformed_json_single_quotes(self):
        malformed = "{'relations': [{'source': {'text': 'A', 'label': 'Person'}, 'target': {'text': 'B', 'label': 'Person'}, 'label': 'RELATES_TO'}]}"
        fn = _make_llm_fn(malformed)
        extractor = LLMGraphExtractor({"llm_fn": fn})
        result = extractor.extract("test text")
        # json_repair should handle single quotes
        assert isinstance(result, ExtractResult)


# ── 6.1.4: Test LLMGraphExtractor.extract with empty text ──

    def test_extract_empty_text(self):
        fn = _make_llm_fn(VALID_RELATIONS_OUTPUT)
        extractor = LLMGraphExtractor({"llm_fn": fn})
        result = extractor.extract("")
        assert isinstance(result, ExtractResult)
        assert len(result.entities) == 0
        assert len(result.relations) == 0

    def test_extract_whitespace_only_text(self):
        fn = _make_llm_fn(VALID_RELATIONS_OUTPUT)
        extractor = LLMGraphExtractor({"llm_fn": fn})
        result = extractor.extract("   \n\t  ")
        assert isinstance(result, ExtractResult)
        assert len(result.entities) == 0


# ── 6.1.5: Test LLMGraphExtractor.extract with llm_fn=None ──

    def test_extract_llm_fn_none(self):
        extractor = LLMGraphExtractor({"llm_fn": None})
        result = extractor.extract("some text")
        assert isinstance(result, ExtractResult)
        assert len(result.entities) == 0
        assert len(result.relations) == 0

    def test_extract_llm_fn_not_provided(self):
        extractor = LLMGraphExtractor({})
        result = extractor.extract("some text")
        assert isinstance(result, ExtractResult)
        assert len(result.entities) == 0


# ── 6.1.6: Test LLMGraphExtractor.extract with LLM exception ──

    def test_extract_llm_exception(self):
        def raising_fn(system, user):
            raise RuntimeError("LLM service unavailable")
        extractor = LLMGraphExtractor({"llm_fn": raising_fn})
        result = extractor.extract("some text")
        assert isinstance(result, ExtractResult)
        assert len(result.entities) == 0
        assert len(result.relations) == 0


# ── 6.1.7: Test normalize_extraction_result — entity deduplication ──

class TestNormalizeExtractionResult:
    def test_entity_deduplication_by_name_label(self):
        result = ExtractResult()
        result.entities.append(Entity(name="React", type="Concept"))
        result.entities.append(Entity(name="react", type="Concept"))
        result.entities.append(Entity(name="REACT", type="Concept"))
        normalized = GraphExtractor.normalize_extraction_result(result)
        # All three should deduplicate to one (normalized name is "react")
        react_count = sum(1 for e in normalized.entities if e.name.lower() == "react")
        assert react_count == 1

    def test_entity_deduplication_different_labels_kept(self):
        result = ExtractResult()
        result.entities.append(Entity(name="Apple", type="Organization"))
        result.entities.append(Entity(name="Apple", type="Product"))
        normalized = GraphExtractor.normalize_extraction_result(result)
        # Same name, different type → both kept
        assert len(normalized.entities) == 2


# ── 6.1.8: Test normalize_extraction_result — attribute merging ──

    def test_attribute_merging_union(self):
        result = ExtractResult()
        ent1 = Entity(name="Apple", type="Organization")
        ent1.attributes = [{"text": "Cupertino", "label": "Location"}]
        ent2 = Entity(name="apple", type="Organization")
        ent2.attributes = [{"text": "Tech Company", "label": "Industry"}]
        result.entities.append(ent1)
        result.entities.append(ent2)
        normalized = GraphExtractor.normalize_extraction_result(result)
        assert len(normalized.entities) == 1
        merged = normalized.entities[0]
        assert len(merged.attributes) == 2

    def test_attribute_merging_dedup_same(self):
        result = ExtractResult()
        ent1 = Entity(name="Apple", type="Organization")
        ent1.attributes = [{"text": "Cupertino", "label": "Location"}]
        ent2 = Entity(name="apple", type="Organization")
        ent2.attributes = [{"text": "Cupertino", "label": "Location"}]
        result.entities.append(ent1)
        result.entities.append(ent2)
        normalized = GraphExtractor.normalize_extraction_result(result)
        assert len(normalized.entities) == 1
        assert len(normalized.entities[0].attributes) == 1


# ── 6.1.9: Test normalize_extraction_result — relation reference resolution ──

    def test_relation_string_reference_resolution(self):
        """Relations with string source/target should resolve to entities."""
        result = ExtractResult()
        result.entities.append(Entity(name="React", type="Concept"))
        result.entities.append(Entity(name="Vue", type="Concept"))
        result.relations.append(Relation(
            from_name="React", to_name="Vue", rel_type="RELATES_TO",
        ))
        normalized = GraphExtractor.normalize_extraction_result(result)
        assert len(normalized.relations) == 1
        assert normalized.relations[0].from_name == "React"
        assert normalized.relations[0].to_name == "Vue"

    def test_relation_case_insensitive_reference(self):
        """Relations should match entities case-insensitively."""
        result = ExtractResult()
        result.entities.append(Entity(name="React", type="Concept"))
        result.entities.append(Entity(name="Vue", type="Concept"))
        result.relations.append(Relation(
            from_name="react", to_name="vue", rel_type="RELATES_TO",
        ))
        normalized = GraphExtractor.normalize_extraction_result(result)
        assert len(normalized.relations) == 1


# ── 6.1.10: Test normalize_extraction_result — unresolved relations discarded ──

    def test_unresolved_relations_discarded(self):
        """Relations with empty source/target should be discarded."""
        result = ExtractResult()
        result.relations.append(Relation(
            from_name="", to_name="Vue", rel_type="RELATES_TO",
        ))
        result.relations.append(Relation(
            from_name="React", to_name="", rel_type="RELATES_TO",
        ))
        result.relations.append(Relation(
            from_name="React", to_name="Vue", rel_type="RELATES_TO",
        ))
        normalized = GraphExtractor.normalize_extraction_result(result)
        assert len(normalized.relations) == 1
        assert normalized.relations[0].from_name == "React"
        assert normalized.relations[0].to_name == "Vue"

    def test_empty_result_unchanged(self):
        result = ExtractResult()
        normalized = GraphExtractor.normalize_extraction_result(result)
        assert len(normalized.entities) == 0
        assert len(normalized.relations) == 0


# ── 6.1.11: Test LLMGraphExtractor.extract_batch — concurrent extraction ──

class TestLLMGraphExtractorBatch:
    @pytest.mark.asyncio
    async def test_extract_batch_concurrent(self):
        fn = _make_llm_fn(VALID_RELATIONS_OUTPUT)
        extractor = LLMGraphExtractor({"llm_fn": fn, "concurrency_count": 3})
        chunks = [
            ChunkRef(id=i, pg_id=i, content=f"chunk {i}")
            for i in range(5)
        ]
        results = await extractor.extract_batch(chunks)
        assert len(results) == 5
        for r in results:
            assert isinstance(r, ExtractResult)

    @pytest.mark.asyncio
    async def test_extract_batch_empty_chunks(self):
        fn = _make_llm_fn(VALID_RELATIONS_OUTPUT)
        extractor = LLMGraphExtractor({"llm_fn": fn})
        results = await extractor.extract_batch([])
        assert results == []

    @pytest.mark.asyncio
    async def test_extract_batch_options_concurrency(self):
        fn = _make_llm_fn(VALID_RELATIONS_OUTPUT)
        extractor = LLMGraphExtractor({"llm_fn": fn, "concurrency_count": 1})
        chunks = [ChunkRef(id=0, pg_id=0, content="test")]
        results = await extractor.extract_batch(chunks, options={"concurrency": 2})
        assert len(results) == 1


# ── 6.1.12: Test GraphExtractorFactory register + create round-trip ──

class TestGraphExtractorFactory:
    def test_factory_create_llm(self):
        extractor = GraphExtractorFactory.create("llm", {"llm_fn": None})
        assert isinstance(extractor, LLMGraphExtractor)
        assert isinstance(extractor, GraphExtractor)

    def test_factory_register_and_create(self):
        class DummyExtractor(GraphExtractor):
            def __init__(self, options: dict):
                self.options = options

            def extract(self, text: str, options: dict | None = None) -> ExtractResult:
                return ExtractResult()

            async def extract_batch(self, chunks: list[ChunkRef], options: dict | None = None) -> list[ExtractResult]:
                return []

        GraphExtractorFactory.register("dummy", DummyExtractor)
        extractor = GraphExtractorFactory.create("dummy", {"key": "value"})
        assert isinstance(extractor, DummyExtractor)
        assert extractor.options == {"key": "value"}


# ── 6.1.13: Test GraphExtractorFactory.create with unknown name ──

    def test_factory_create_unknown_raises_valueerror(self):
        with pytest.raises(ValueError, match="Unknown extractor type"):
            GraphExtractorFactory.create("nonexistent", {})


# ── 6.1.14: Test Extractor compatibility wrapper ──

class TestExtractorCompatWrapper:
    def test_extractor_with_llm_fn(self):
        fn = _make_llm_fn(VALID_RELATIONS_OUTPUT)
        extractor = Extractor(fn)
        result = extractor.extract("test text")
        assert isinstance(result, ExtractResult)
        assert len(result.entities) > 0
        assert len(result.relations) > 0

    def test_extractor_with_none_llm_fn(self):
        extractor = Extractor(None)
        result = extractor.extract("test text")
        assert isinstance(result, ExtractResult)
        assert len(result.entities) == 0
        assert len(result.relations) == 0

    def test_extractor_empty_text(self):
        fn = _make_llm_fn(VALID_RELATIONS_OUTPUT)
        extractor = Extractor(fn)
        result = extractor.extract("")
        assert isinstance(result, ExtractResult)
        assert len(result.entities) == 0

    @pytest.mark.asyncio
    async def test_extractor_extract_batch(self):
        fn = _make_llm_fn(VALID_RELATIONS_OUTPUT)
        extractor = Extractor(fn)
        chunks = [ChunkRef(id=0, pg_id=0, content="test")]
        results = await extractor.extract_batch(chunks, concurrency=2)
        assert len(results) == 1
        assert isinstance(results[0], ExtractResult)

    @pytest.mark.asyncio
    async def test_extractor_extract_batch_empty(self):
        fn = _make_llm_fn(VALID_RELATIONS_OUTPUT)
        extractor = Extractor(fn)
        results = await extractor.extract_batch([], concurrency=2)
        assert results == []

    @pytest.mark.asyncio
    async def test_extractor_extract_batch_none_delegate(self):
        extractor = Extractor(None)
        results = await extractor.extract_batch(
            [ChunkRef(id=0, pg_id=0, content="test")], concurrency=2,
        )
        assert results == []


# ── 6.1.15: Test LLMGraphExtractor with schema parameter ──

class TestLLMGraphExtractorSchema:
    def test_schema_appears_in_prompt(self):
        captured_prompt = []

        def capturing_fn(system_prompt, user_text):
            captured_prompt.append(system_prompt)
            return VALID_RELATIONS_OUTPUT

        schema_text = "只抽取法律领域的实体：法院、案件、法条"
        extractor = LLMGraphExtractor({"llm_fn": capturing_fn, "schema": schema_text})
        extractor.extract("test text")
        assert len(captured_prompt) == 1
        assert schema_text in captured_prompt[0]
        assert SCHEMA_INSTRUCTION.format(schema=schema_text) in captured_prompt[0]

    def test_no_schema_no_schema_instruction(self):
        captured_prompt = []

        def capturing_fn(system_prompt, user_text):
            captured_prompt.append(system_prompt)
            return VALID_RELATIONS_OUTPUT

        extractor = LLMGraphExtractor({"llm_fn": capturing_fn})
        extractor.extract("test text")
        assert len(captured_prompt) == 1
        assert "抽取 Schema 约束" not in captured_prompt[0]

    def test_default_prompt_contains_entity_types(self):
        """Verify the default prompt lists all entity types."""
        assert "Person" in DEFAULT_TRIPLE_EXTRACTION_PROMPT
        assert "Organization" in DEFAULT_TRIPLE_EXTRACTION_PROMPT
        assert "Location" in DEFAULT_TRIPLE_EXTRACTION_PROMPT
        assert "Concept" in DEFAULT_TRIPLE_EXTRACTION_PROMPT
        assert "Event" in DEFAULT_TRIPLE_EXTRACTION_PROMPT
        assert "Product" in DEFAULT_TRIPLE_EXTRACTION_PROMPT
        assert "Unknown" in DEFAULT_TRIPLE_EXTRACTION_PROMPT

    def test_default_prompt_contains_relation_types(self):
        """Verify the default prompt lists all relation types."""
        assert "RELATES_TO" in DEFAULT_TRIPLE_EXTRACTION_PROMPT
        assert "PART_OF" in DEFAULT_TRIPLE_EXTRACTION_PROMPT
        assert "CAUSES" in DEFAULT_TRIPLE_EXTRACTION_PROMPT
        assert "DESCRIBES" in DEFAULT_TRIPLE_EXTRACTION_PROMPT
        assert "MENTIONS" in DEFAULT_TRIPLE_EXTRACTION_PROMPT
        assert "WORKS_FOR" in DEFAULT_TRIPLE_EXTRACTION_PROMPT
        assert "LOCATED_IN" in DEFAULT_TRIPLE_EXTRACTION_PROMPT

    def test_default_prompt_uses_relations_embedded_format(self):
        """Verify the prompt shows the relations-embedded format (not entities+relations flat)."""
        assert '"relations"' in DEFAULT_TRIPLE_EXTRACTION_PROMPT
        assert '"source"' in DEFAULT_TRIPLE_EXTRACTION_PROMPT
        assert '"target"' in DEFAULT_TRIPLE_EXTRACTION_PROMPT
        assert '"entities"' not in DEFAULT_TRIPLE_EXTRACTION_PROMPT
