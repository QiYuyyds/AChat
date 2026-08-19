## Purpose

RAG 分块策略调度系统：定义 4 种 preset 和 dispatcher 的行为契约，使不同类型文档可以使用最适合的分块策略。

## ADDED Requirements

### Requirement: ChunkDispatcher SHALL route to preset-specific chunker by preset_id

The chunking system MUST provide a `chunk_markdown(content, preset_id, config)` function that dispatches to the appropriate preset parser. Valid preset IDs are `general`, `qa`, `semantic`, `separator`. An invalid preset_id MUST fall back to `general`.

#### Scenario: Chunk with general preset
- **WHEN** `chunk_markdown(content, "general", config)` is called
- **THEN** the content is split using the `RecursiveSplitter` (existing implementation)
- **AND** the result is a list of chunk strings

#### Scenario: Chunk with qa preset
- **WHEN** `chunk_markdown(content, "qa", config)` is called
- **THEN** the content is parsed for question-answer structures
- **AND** each identified QA pair becomes a chunk
- **AND** non-QA content is chunked using the general strategy as fallback

#### Scenario: Chunk with semantic preset
- **WHEN** `chunk_markdown(content, "semantic", config)` is called
- **THEN** the content is split by sentence boundaries and grouped by embedding clustering
- **AND** each chunk is augmented with heading context from its parent section

#### Scenario: Chunk with separator preset
- **WHEN** `chunk_markdown(content, "separator", config)` is called
- **THEN** the content is split at configured separator occurrences
- **AND** each segment between separators becomes a chunk

#### Scenario: Invalid preset_id falls back to general
- **WHEN** `chunk_markdown(content, "nonexistent", config)` is called
- **THEN** the content is chunked using the `general` preset

### Requirement: RAGEngine.ingest SHALL accept preset_id parameter

The `RAGEngine.ingest()` method MUST accept an optional `preset_id` parameter. When provided, it determines which chunking preset is used. When omitted, it defaults to `settings.rag_chunk_preset`.

#### Scenario: Document ingested with explicit preset
- **WHEN** `RAGEngine.ingest(doc, preset_id="qa", user_id=...)` is called
- **THEN** the document is chunked using the `qa` preset
- **AND** the `Document.chunk_preset` column is set to `"qa"`

#### Scenario: Document ingested with default preset
- **WHEN** `RAGEngine.ingest(doc, user_id=...)` is called without `preset_id`
- **THEN** the document is chunked using `settings.rag_chunk_preset` (default `"general"`)
