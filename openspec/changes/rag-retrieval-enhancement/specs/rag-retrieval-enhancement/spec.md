## Purpose

RAG 检索增强：定义 PG TF cosine fallback、PG embedding 向量 cosine fallback、查询参数化、ES analyzer 增强和并发控制的行为契约，使检索层在基础设施部分不可用时仍能提供降级检索能力。

## ADDED Requirements

### Requirement: HybridStore SHALL fall back to PG TF cosine when Milvus is unavailable

When Milvus is unavailable, the HybridStore MUST fall back to a PG-based TF cosine search path. The fallback MUST query `rag_chunks` rows that have embeddings, compute TF cosine similarity against the query, and return ranked results. A safety cap of 5000 rows MUST be applied to prevent performance degradation on large datasets.

> **Note**: BM25 full-text search is implemented as Milvus native BM25 by the `rag-milvus-bm25-migration` proposal. When Milvus is available, the keyword search path uses Milvus BM25 (not ES). The PG TF cosine fallback only activates when Milvus is unavailable.

#### Scenario: Milvus unavailable, no embed_fn
- **WHEN** Milvus is not configured and no embedding function is set
- **THEN** the search uses PG TF cosine as the sole retrieval path
- **AND** results are ranked by TF cosine similarity score

### Requirement: HybridStore SHALL fall back to PG embedding cosine when Milvus is unavailable

When Milvus is unavailable and an embedding function is configured, the HybridStore MUST fall back to a PG-based embedding vector cosine search path. The fallback MUST query `rag_chunks` rows with non-null `embedding` JSONB values, compute vector cosine similarity between the query embedding and each chunk's stored embedding, and return ranked results. A safety cap of 5000 rows MUST be applied. This fallback takes priority over the TF cosine fallback when `embed_fn` is available, as vector similarity provides better semantic matching than term-frequency matching.

#### Scenario: Milvus unavailable, embed_fn available
- **WHEN** Milvus is not configured but an embedding function is set
- **AND** `search_mode="vector"` is requested
- **THEN** the search uses PG embedding cosine as the semantic retrieval path
- **AND** results are ranked by vector cosine similarity score

#### Scenario: Milvus unavailable in hybrid mode, ES available
- **WHEN** Milvus is not configured but ES is available
- **AND** `search_mode="hybrid"` is requested
- **AND** an embedding function is set
- **THEN** the hybrid 2-way RRF fusion uses PG embedding cosine + ES BM25
- **AND** the semantic path source is attributed as `"pg_embedding"`

#### Scenario: Milvus unavailable, no embed_fn
- **WHEN** Milvus is not configured and no embedding function is set
- **AND** `search_mode="vector"` is requested
- **THEN** the search falls back to PG TF cosine (if chunks exist)
- **AND** results are ranked by TF cosine similarity score

### Requirement: RetrievalConfig SHALL parameterize search behavior

The search API MUST accept an optional `RetrievalConfig` that overrides default settings for `search_mode` (vector/keyword/hybrid), `final_top_k`, `vector_weight`, `bm25_weight`, `kg_weight`, `similarity_threshold`, and graph retrieval parameters. When not provided, the system MUST use default values from settings.

#### Scenario: Hybrid search with custom weights
- **WHEN** `HybridStore.search(query, top_k, retrieval_config=RetrievalConfig(vector_weight=0.8, bm25_weight=0.2))` is called
- **THEN** the RRF fusion uses 0.8 for vector path and 0.2 for keyword path
- **AND** the weights are renormalized to 1.0 across available paths

#### Scenario: Vector-only search mode
- **WHEN** `RetrievalConfig(search_mode="vector")` is specified
- **THEN** only the Milvus semantic search path is used (or PG embedding fallback if Milvus unavailable)
- **AND** ES BM25 and KG paths are skipped

### Requirement: HybridStore mode SHALL include tfidf and pg_embedding modes

The `mode()` method MUST return `"tfidf"` when Milvus is unavailable but PG TF cosine fallback is active. The `mode()` method MUST return `"pg_embedding"` when Milvus is unavailable but an embedding function is configured and PG has chunks with embeddings. The `_tfidf_ok()` method MUST check whether PG has any chunks (tracked via `_has_chunks` flag, updated on `index_chunks()` and on `RAGService.initialize()`), rather than returning a hardcoded `True`.

#### Scenario: Mode detection with no external infrastructure
- **WHEN** Milvus is not configured
- **AND** PG has chunks with embeddings
- **AND** no embedding function is set
- **THEN** `mode()` returns `"tfidf"`
- **AND** search returns TF cosine ranked results

#### Scenario: Mode detection with embed_fn but no Milvus
- **WHEN** Milvus is not configured
- **AND** PG has chunks with embeddings
- **AND** an embedding function is set
- **THEN** `mode()` returns `"pg_embedding"`
- **AND** search returns vector cosine ranked results

#### Scenario: No chunks in PG
- **WHEN** Milvus is not configured
- **AND** PG has no chunks
- **THEN** `mode()` returns `"unavailable"`
- **AND** `_tfidf_ok()` returns `False`

### Requirement: Search I/O SHALL be concurrency-limited

Async search operations that offload blocking I/O (Milvus queries) MUST be governed by an `asyncio.Semaphore` with configurable concurrency limit. The semaphore MUST be loop-scoped (one per event loop) to avoid cross-loop issues.

#### Scenario: High concurrent search load
- **WHEN** multiple concurrent search requests arrive
- **THEN** at most `rag_search_concurrency` (default 8) I/O operations execute simultaneously
- **AND** excess requests wait for semaphore release
