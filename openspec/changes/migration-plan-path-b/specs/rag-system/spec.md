## ADDED Requirements

### Requirement: RecursiveSplitter SHALL split documents with Markdown protection
RecursiveSplitter MUST recursively split text using a delimiter stack (`\n\n`, `\n`, `。`, `！`, `？`, `；`, ` `, `""`) with configurable `chunk_size` and `chunk_overlap`. Markdown fenced code blocks SHALL be treated as atomic fragments that are not split.

#### Scenario: Chinese-English mixed text is split correctly
- **WHEN** a document with mixed Chinese and English paragraphs is split
- **THEN** chunks respect the `chunk_size` boundary
- **AND** adjacent chunks have `chunk_overlap` characters of prefix overlap

#### Scenario: Markdown code blocks are preserved
- **WHEN** a document contains fenced code blocks (``` ... ```)
- **THEN** the code block is kept as a single atomic chunk

### Requirement: LLMRewriter SHALL generate query variants
LLMRewriter MUST generate 3 rewritten query variants from the original query and conversation history. Output MUST be valid JSON `{"queries": [...]}`. On failure, it SHALL return the original query unchanged.

#### Scenario: Query is rewritten into 3 variants
- **WHEN** `rewrite(query, history)` is called with a valid LLM
- **THEN** 3 semantically equivalent but lexically different queries are returned

#### Scenario: Rewriter degrades on LLM failure
- **WHEN** the LLM returns invalid JSON or errors
- **THEN** the original query is returned as the sole result

### Requirement: LLMReranker SHALL score retrieval results via LLM
LLMReranker MUST assign relevance scores (0-10) to retrieval results using listwise LLM scoring. Output MUST be valid JSON `{"scores": [{"idx": N, "score": S}, ...]}`. Final score SHALL be `llm_score / 10.0`. On failure, it SHALL preserve the original RRF ranking order.

#### Scenario: Results are reranked by relevance
- **WHEN** `rerank(query, results, top_k)` is called
- **THEN** results are reordered by descending LLM relevance score

#### Scenario: Reranker degrades on LLM failure
- **WHEN** the LLM returns invalid JSON or errors
- **THEN** the original RRF ranking order is preserved

### Requirement: HybridStore SHALL perform three-way RRF fusion search
HybridStore MUST combine results from Milvus ANN (semantic), Elasticsearch BM25/IK (keyword), and Neo4j graph traversal using Reciprocal Rank Fusion: `score(d) = Σ weight_i / (k + rank_i(d))`. When a retrieval path fails, its weight SHALL be redistributed to remaining active paths.

#### Scenario: All three paths return results
- **WHEN** Milvus, ES, and Neo4j all return ranked results
- **THEN** RRF fusion combines them with `semantic_weight=0.7`, `keyword_weight=0.3`, and `kg_weight=0.3`
- **AND** the top-k results are returned with full content fetched from PostgreSQL

#### Scenario: One retrieval path fails
- **WHEN** Milvus is unavailable but ES and Neo4j return results
- **THEN** RRF skips the Milvus path and redistributes weights
- **AND** results are still returned from the remaining paths

#### Scenario: All retrieval paths fail
- **WHEN** Milvus, ES, and Neo4j are all unavailable
- **THEN** an empty result list is returned

### Requirement: HybridStore SHALL index documents with parent context
`index_with_parents()` MUST store chunks in PostgreSQL, insert embeddings into Milvus, and index text into Elasticsearch. Each chunk SHALL have an optional `parent_content` field providing surrounding context for retrieval augmentation.

#### Scenario: Document is indexed across all stores
- **WHEN** `ingest(doc_hash, text)` is called
- **THEN** chunks are stored in PostgreSQL `rag_chunks` table
- **AND** embeddings are inserted into Milvus collection
- **AND** text is indexed into Elasticsearch

#### Scenario: Retrieved chunks include parent context
- **WHEN** a chunk is retrieved via `search()`
- **THEN** the result includes both the chunk content and its parent content

### Requirement: RAGService SHALL orchestrate the full RAG pipeline
RAGService MUST assemble RecursiveSplitter + HybridStore + LLMRewriter + LLMReranker. `ingest()` SHALL split → embed → index. `search()` SHALL rewrite → multi-path retrieve → RRF fuse → rerank.

#### Scenario: End-to-end document ingestion and search
- **WHEN** a document is ingested via `ingest(doc_hash, text)`
- **AND** a query is searched via `search(query, top_k)`
- **THEN** relevant chunks from the ingested document are returned ranked by fused score
