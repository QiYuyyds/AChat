## Purpose

异步图谱构建任务系统：定义图谱构建任务的状态机、重试机制、并发控制和 PPR 检索增强的行为契约，使知识图谱构建在失败时可重试、状态可追踪、并发可控。

## ADDED Requirements

### Requirement: GraphBuildTask SHALL build graph asynchronously with state machine

After document indexing completes, a `GraphBuildTask` MUST be triggered asynchronously. The task manages the `graph_status` lifecycle on the `Document` record: `graph_pending` → `graph_building` → `graph_indexed` (success) or `error_graph` (failure). The task MUST NOT block the document ingest pipeline.

#### Scenario: Document indexed, graph build triggered
- **WHEN** a document finishes RAG indexing (PG + Milvus + ES)
- **THEN** `Document.graph_status` is set to `"graph_pending"`
- **AND** a `GraphBuildTask.build()` is scheduled via `asyncio.create_task()`
- **AND** the ingest API returns immediately without waiting for graph build

#### Scenario: Graph build succeeds
- **WHEN** the GraphBuildTask completes successfully
- **THEN** `Document.graph_status` transitions to `"graph_indexed"`
- **AND** entities and relations are persisted in Neo4j

#### Scenario: Graph build fails and retries
- **WHEN** the GraphBuildTask encounters an error during entity extraction
- **THEN** the task retries up to `MAX_EXTRACTION_ATTEMPTS` (3) times
- **AND** retries use exponential backoff (2s, 10s)
- **AND** after all retries fail, `Document.graph_status` is set to `"error_graph"`

### Requirement: GraphBuildTask SHALL batch-extract entities with concurrency control

The GraphBuildTask MUST process chunks in batches (adaptive batch size between 100 and 1000). LLM entity extraction within a batch MUST be concurrent with `asyncio.Semaphore` limiting to `rag_graph_concurrency` (default 5) concurrent LLM calls. Neo4j writes MUST be concurrent with `rag_graph_neo4j_concurrency` (default 8).

#### Scenario: Large document with 500 chunks
- **WHEN** a document with 500 chunks triggers graph build
- **THEN** chunks are processed in batches (e.g., 5 batches of ~100)
- **AND** within each batch, at most 5 LLM extraction calls run concurrently
- **AND** Neo4j MERGE operations run with at most 8 concurrent writers

### Requirement: KGStore SHALL support PPR (Personalized PageRank) retrieval

The KGStore search MUST support a PPR-based retrieval mode that starts from query-extracted entities and traverses the graph using Personalized PageRank scoring. The `graph_expand_depth` parameter controls traversal depth.

#### Scenario: PPR retrieval with depth 1
- **WHEN** `search_with_ppr(query_entities, top_k=10, expand_depth=1)` is called
- **THEN** entities matching the query are found in Neo4j
- **AND** their 1-hop neighbors are collected via PPR scoring
- **AND** associated `pg_id` values are returned ranked by PPR score

### Requirement: MilvusGraphVectorStore SHALL persist entity and triple vectors with BM25

The `MilvusGraphVectorStore` MUST create two independent Milvus Collections for graph entities and triples. Each Collection MUST have a dense `embedding` field (`FLOAT_VECTOR` + `COSINE` + `IVF_FLAT`) and a `content` field with `enable_analyzer=True` + `analyzer_params={"type":"chinese"}` + `Function(BM25)` + `content_sparse: SPARSE_FLOAT_VECTOR` + `SPARSE_INVERTED_INDEX`. The triple Collection MUST additionally have `source_id` and `target_id` fields for relationship traversal.

The `GraphBuildTask` MUST write extracted entities and triples to both Neo4j (for PPR graph traversal) and `MilvusGraphVectorStore` (for semantic + BM25 vector recall). The `GraphRetrieval` search MUST first recall entities/triples via Milvus vector search, then use Neo4j PPR to expand from the recalled entities.

#### Scenario: Entity vector recall during graph retrieval
- **WHEN** `GraphRetrieval.search(query, top_k=10, expand_depth=1)` is called
- **THEN** the query is embedded and searched against the entity Milvus Collection
- **AND** matching entities are used as seeds for Neo4j PPR traversal
- **AND** associated `pg_id` values are returned ranked by PPR score

#### Scenario: Triple BM25 search during graph retrieval
- **WHEN** a keyword-heavy query is searched against the triple Collection
- **THEN** the query text is passed directly to Milvus BM25 search (auto-tokenized)
- **AND** matching triples are returned with BM25 scores

### Requirement: DocumentLifecycleManager SHALL manage file status transitions

A `DocumentLifecycleManager` MUST enforce valid state transitions on `Document.status` and `Document.graph_status` using a state machine with optimistic concurrency control. Invalid transitions MUST be rejected.

#### Scenario: Valid status transition
- **WHEN** a document in `"parsing"` state transitions to `"parsed"`
- **THEN** the transition is accepted
- **AND** `Document.status` is updated to `"parsed"`

#### Scenario: Invalid status transition rejected
- **WHEN** a document in `"indexed"` state attempts to transition to `"parsing"`
- **THEN** the transition is rejected
- **AND** no status change occurs

#### Scenario: Optimistic concurrency conflict
- **WHEN** two concurrent operations attempt to transition the same document
- **AND** the first succeeds
- **THEN** the second fails because the expected current state no longer matches
- **AND** an error is returned
