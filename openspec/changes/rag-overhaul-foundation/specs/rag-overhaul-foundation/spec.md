## Purpose

RAG 大改基石层：定义 DB schema 扩展、配置项变更、Query Rewriter 移除和 RAGEngine 签名变更的行为契约。后续分块预设、OCR 引擎、检索增强、图谱构建、评估系统等提案均依赖此基石层。

## ADDED Requirements

### Requirement: RAG search SHALL NOT perform query rewriting

The RAG search pipeline MUST NOT include any LLM-based query rewriting step. Multi-turn context resolution is the responsibility of the calling Agent, which has full conversation context and can construct more precise search terms than a Rewriter that only sees query + history fragments.

#### Scenario: Agent calls rag_search with a short query
- **WHEN** an Agent calls `rag_search` with a query like "那它呢？"
- **THEN** the RAG system passes the query directly to the hybrid retrieval pipeline without any rewriting
- **AND** no LLM call is made for query rewriting

#### Scenario: RAGService.search is called without history parameter
- **WHEN** `RAGService.search(query)` is called
- **THEN** the `history` parameter is not accepted (removed from signature)
- **AND** the query is used as-is for retrieval

### Requirement: RAGEngine query API SHALL remove history parameter

The `RAGEngine.query_with_history()` method MUST be renamed to `query()` and the `history` parameter MUST be removed. The `LLMRewriter` class and its import must be removed from the codebase.

#### Scenario: RAGEngine.query is called
- **WHEN** `RAGEngine.query(question, user_id=...)` is called
- **THEN** the question is passed directly to `HybridStore.search_multi()` without any rewriting step
- **AND** no `LLMRewriter` instance is referenced

### Requirement: Configuration SHALL remove rewrite settings and add foundation settings

The `rag_rewrite_enabled` and `rag_rewrite_num_queries` configuration items MUST be removed. New configuration items for chunking presets, concurrency control, graph auto-build, eval LLM, and Milvus BM25 MUST be added. Configuration default values MUST align with Fidi-Intelli: `rag_rrf_constant_k` = 60, `rag_semantic_weight` = 0.7, `rag_keyword_weight` = 0.3, `ocr_engine` = `"auto"`, `rag_graph_concurrency` = 5, `rag_graph_neo4j_concurrency` = 8, `rag_graph_retry_delays` = `"2.0,10.0"`.

The `rag_bm25_analyzer` configuration item is retained but marked for deletion by the `rag-milvus-bm25-migration` proposal (Milvus analyzer is fixed to `{"type":"chinese"}` in the Collection schema).

#### Scenario: Backend starts with new configuration
- **WHEN** the backend starts
- **THEN** `rag_chunk_preset` defaults to `"general"`
- **AND** `rag_embed_concurrency` defaults to `5`
- **AND** `rag_search_concurrency` defaults to `8`
- **AND** `rag_graph_auto_build` defaults to `True`
- **AND** `rag_rewrite_enabled` is not present
- **AND** `rag_rewrite_num_queries` is not present
- **AND** `rag_rrf_constant_k` defaults to `60`
- **AND** `rag_semantic_weight` defaults to `0.7`
- **AND** `rag_keyword_weight` defaults to `0.3`
- **AND** `ocr_engine` defaults to `"auto"`
- **AND** `rag_graph_concurrency` defaults to `5`
- **AND** `rag_graph_neo4j_concurrency` defaults to `8`
- **AND** `milvus_bm25_drop_ratio_search` defaults to `0.0`

### Requirement: DB migration SHALL be idempotent and run at startup

A migration script MUST be executed at backend startup (before RAGService initialization) that adds new columns to `documents` and `rag_chunks` tables. The migration MUST be idempotent (safe to run multiple times) and MUST backfill existing rows with default values.

#### Scenario: Migration runs on existing database
- **WHEN** the backend starts on a database that already has `documents` and `rag_chunks` tables
- **THEN** `documents.chunk_preset` column is added with `DEFAULT 'general'`
- **AND** `documents.graph_status` column is added with `DEFAULT NULL`
- **AND** `rag_chunks.chunk_token_count` column is added with `DEFAULT 0`
- **AND** `rag_chunks.start_char_pos` column is added
- **AND** `rag_chunks.end_char_pos` column is added
- **AND** existing `documents` rows have `chunk_preset` set to `'general'`
- **AND** existing `documents` rows with `status = 'active'` have `graph_status` set to `'graph_indexed'`

#### Scenario: Migration runs on fresh database
- **WHEN** the backend starts on a fresh database
- **THEN** the migration is a no-op (columns already exist from `create_all`)
- **AND** no errors are raised

### Requirement: rag_search tool SHALL NOT accept history parameter

The `rag_search` tool handler MUST NOT pass `history` to `RAGService.search()`. The tool's parameter schema remains `{query: string}` with no history parameter.

#### Scenario: Agent calls rag_search tool
- **WHEN** an Agent calls the `rag_search` tool with `{query: "some query"}`
- **THEN** the handler calls `_rag_service.search(query, user_id=ctx.user_id)` without any history argument

### Requirement: RAGEngine SHALL populate chunk metadata on ingest

The `RAGEngine.ingest()` method MUST compute and pass `chunk_token_count`, `start_char_pos`, and `end_char_pos` to `HybridStore.index_chunks()`. The token count MUST be computed using `nlp.count_tokens()`. The character positions MUST be determined by incrementally searching for each chunk's content within the source document. The `HybridStore.index_chunks()` method MUST accept `token_counts` and `char_positions` optional parameters and persist them to the corresponding `rag_chunks` columns.

#### Scenario: Document ingested with chunk metadata
- **WHEN** a document is ingested via `RAGEngine.ingest(doc, preset_id="general")`
- **THEN** each `rag_chunks` row has `chunk_token_count` set to the rune length of the chunk content
- **AND** `start_char_pos` and `end_char_pos` are set to the character positions of the chunk within the source document
- **AND** chunks that cannot be located in the source (e.g., overlap-modified) have `start_char_pos` and `end_char_pos` set to `NULL`
