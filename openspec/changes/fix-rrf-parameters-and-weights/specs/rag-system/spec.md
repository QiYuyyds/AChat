## MODIFIED Requirements

### Requirement: HybridStore SHALL perform three-way RRF fusion search
HybridStore MUST combine results from Milvus ANN (semantic), Elasticsearch BM25/IK (keyword), and Neo4j graph traversal using weighted Reciprocal Rank Fusion: `score(d) = Σ weight_i / (k + rank_i(d) + 1)`. Weights SHALL be read from three explicit configuration parameters: `rag_semantic_weight`, `rag_keyword_weight`, and `kg_weight`. When a retrieval path fails, its weight SHALL be redistributed to remaining active paths via normalization to 1.0.

Default configuration values SHALL be: `rag_semantic_weight=0.5`, `rag_keyword_weight=0.5`, `kg_weight=0.0`, `rag_rrf_constant_k=30`.

#### Scenario: All three paths return results with default configuration
- **WHEN** Milvus, ES, and Neo4j all return ranked results with default config
- **THEN** RRF fusion combines them with `semantic_weight=0.5`, `keyword_weight=0.5`, and `kg_weight=0.0`
- **AND** the KG path contributes zero to RRF scores (weight=0)
- **AND** the top-k results are returned with full content fetched from PostgreSQL

#### Scenario: BM25 path has non-zero weight in default configuration
- **WHEN** the system starts with default configuration values
- **THEN** `rag_keyword_weight` is 0.5 (not 0.0)
- **AND** Elasticsearch BM25 results participate in RRF fusion with non-zero contribution

#### Scenario: One retrieval path fails
- **WHEN** Milvus is unavailable but ES and Neo4j return results
- **THEN** RRF skips the Milvus path and redistributes weights
- **AND** results are still returned from the remaining paths

#### Scenario: All retrieval paths fail
- **WHEN** Milvus, ES, and Neo4j are all unavailable
- **THEN** an empty result list is returned

#### Scenario: Weights are read from explicit configuration parameters
- **WHEN** `_search_hybrid` reads weight parameters
- **THEN** `raw_sem` is read from `self.settings.rag_semantic_weight`
- **AND** `raw_kw` is read from `self.settings.rag_keyword_weight`
- **AND** `raw_kg` is read from `self.settings.kg_weight`
- **AND** no implicit derivation (`1.0 - sem - kg`) is performed

### Requirement: KGStore SHALL NOT apply path weight to internal scoring
KGStore's `search()` method MUST compute entity match scores based solely on seed entity count and node degree: `score = seeds * 0.6 + degree * 0.01`. The score MUST NOT be multiplied by `kg_weight` or any path-level weight. Path-level weights SHALL only be applied in the RRF fusion layer (`HybridStore._search_hybrid`), not in individual retrieval path scoring.

#### Scenario: KGStore scores are independent of kg_weight
- **WHEN** `KGStore.search()` computes scores for retrieved entities
- **THEN** the score formula is `seeds * 0.6 + degree * 0.01`
- **AND** the score is not multiplied by `kg_weight`
- **AND** changing `kg_weight` in configuration does not affect KGStore's internal score values or result ordering

#### Scenario: KGStore direct match fallback uses fixed score
- **WHEN** APOC is unavailable and `_search_direct()` is used
- **THEN** all direct match results have a score of 1.0
- **AND** the score is not multiplied by `kg_weight`
