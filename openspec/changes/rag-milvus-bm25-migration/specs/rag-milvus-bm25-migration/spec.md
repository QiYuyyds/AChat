## Purpose

Milvus native BM25 迁移：定义 Milvus Collection schema 变更、ES 完全移除、WeightedRanker 原生融合和图检索 RRF 后融合的行为契约，使 BM25 全文检索不再依赖 Elasticsearch。

## ADDED Requirements

### Requirement: Milvus Collection SHALL support dense + BM25 sparse dual-field schema

The Milvus Collection MUST be recreated with a schema that includes both a dense vector field (`embedding: FLOAT_VECTOR` + `COSINE` + `IVF_FLAT`) and a BM25 sparse field (`content: VARCHAR` with `enable_analyzer=True` + `analyzer_params={"type":"chinese"}` + `content_sparse: SPARSE_FLOAT_VECTOR` + `Function(BM25)` auto-generating sparse vectors from `content` + `SPARSE_INVERTED_INDEX` with `DAAT_MAXSCORE` algorithm).

The existing Collection MUST be dropped and recreated (Milvus does not support in-place schema changes for analyzer and Function fields). Existing PG `rag_chunks` data MUST be deleted (user-confirmed clean slate).

#### Scenario: Collection recreated with BM25 support
- **WHEN** the migration runs
- **THEN** the existing Milvus Collection is dropped
- **AND** a new Collection is created with `content` (enable_analyzer=True, analyzer_params=chinese) + `content_sparse` (SPARSE_FLOAT_VECTOR) + `Function(BM25)` + `embedding` (FLOAT_VECTOR, COSINE, IVF_FLAT)
- **AND** `SPARSE_INVERTED_INDEX` with `DAAT_MAXSCORE` is created on `content_sparse`
- **AND** `IVF_FLAT` with `COSINE` is created on `embedding`
- **AND** existing `rag_chunks` rows in PG are deleted

#### Scenario: Milvus version check
- **WHEN** the migration starts
- **THEN** the Milvus server version is checked
- **AND** if version < 2.4, the migration is aborted with a warning
- **AND** the system falls back to PG TF cosine search (from rag-retrieval-enhancement)

### Requirement: BM25 search SHALL use Milvus native BM25

The keyword search path MUST use Milvus native BM25 by passing the query text directly to `collection.search(anns_field=content_sparse, param={"metric_type":"BM25"})`. Milvus auto-tokenizes the query text and computes BM25 scores. The `bm25_drop_ratio_search` parameter (from RetrievalConfig or settings) controls the fraction of sparse terms to drop during search.

#### Scenario: Keyword search with Milvus BM25
- **WHEN** `search_mode="keyword"` is requested
- **THEN** the query text is passed directly to Milvus BM25 search
- **AND** results are ranked by BM25 score
- **AND** no Elasticsearch call is made

### Requirement: Hybrid search SHALL use Milvus WeightedRanker

The hybrid search path MUST use Milvus `collection.hybrid_search()` with `WeightedRanker(vector_weight, bm25_weight)` to fuse dense vector and BM25 sparse results natively within Milvus. The external RRF 3-way fusion (Milvus + ES + KG) MUST be replaced by this 2-way WeightedRanker fusion.

#### Scenario: Hybrid search with WeightedRanker
- **WHEN** `search_mode="hybrid"` is requested
- **THEN** two `AnnSearchRequest` are created: one for vector (anns_field=embedding, COSINE) and one for BM25 (anns_field=content_sparse, BM25)
- **AND** `collection.hybrid_search(reqs=[vector_req, bm25_req], rerank=WeightedRanker(vector_weight, bm25_weight))` is called
- **AND** no external RRF fusion is performed for the dense+sparse paths

### Requirement: Graph retrieval results SHALL be fused via RRF post-fusion

Graph retrieval results (from Neo4j PPR + Milvus entity/triple vector recall) MUST be fused with the Milvus `hybrid_search` results using RRF post-fusion: `fused_score(d) = 1.0/(rrf_k + rank_chunk(d)) + graph_weight/(rrf_k + rank_graph(d))`. The `rrf_k` defaults to 60 and `graph_weight` defaults to 1.0, both configurable via `RetrievalConfig`.

#### Scenario: Graph results fused with Milvus hybrid results
- **WHEN** `use_graph_retrieval=True` in RetrievalConfig
- **AND** graph retrieval returns results
- **THEN** the Milvus hybrid_search results and graph results are fused via RRF post-fusion
- **AND** the fused ranking is returned as the final results

#### Scenario: Graph retrieval disabled
- **WHEN** `use_graph_retrieval=False` in RetrievalConfig
- **OR** graph retrieval returns no results
- **THEN** only the Milvus hybrid_search results are returned
- **AND** no RRF post-fusion is performed

### Requirement: Elasticsearch SHALL be completely removed

All Elasticsearch-related code, configuration, Docker services, and environment variables MUST be removed. This includes: `_wire_es_to_rag()`, ES client initialization in `infra/factory.py`, `_fetch_es()` / `_search_keyword()` ES paths in `hybrid.py`, ES status in `infra/status.py`, `elasticsearch` service in `docker-compose.infra.yml`, `ES_*` environment variables in `.env.example`, `rag_bm25_analyzer` and `es_addresses` configuration items.

#### Scenario: Backend starts without Elasticsearch
- **WHEN** the backend starts
- **THEN** no Elasticsearch client is initialized
- **AND** no `_wire_es_to_rag()` call is made
- **AND** `infra.status` does not include an `elasticsearch` field
- **AND** the RAG system functions normally with Milvus (or PG fallback if Milvus unavailable)

### Requirement: RetrievalConfig SHALL support graph retrieval parameters

The `RetrievalConfig` dataclass MUST be extended with graph retrieval parameters: `graph_triple_top_k` (default 10), `graph_max_nodes` (default 10000), `graph_top_k` (default 20), `graph_weight` (default 1.0), `ppr_damping` (default 0.85). These parameters control the graph retrieval path and RRF post-fusion behavior.

#### Scenario: Custom graph weight in RetrievalConfig
- **WHEN** `RetrievalConfig(graph_weight=0.5, use_graph_retrieval=True)` is specified
- **THEN** the RRF post-fusion uses 0.5 for graph results
- **AND** 1.0 for Milvus hybrid results
