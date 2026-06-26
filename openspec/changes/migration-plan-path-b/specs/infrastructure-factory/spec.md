## ADDED Requirements

### Requirement: Infrastructure factory SHALL initialize all connections independently
`build_infrastructure(settings)` MUST attempt to connect each service (Milvus, Elasticsearch, Neo4j, Kafka) independently with try/except. A failure in one service SHALL NOT prevent other services from connecting.

#### Scenario: All services connect successfully
- **WHEN** all configured services are reachable
- **THEN** the Infrastructure object reports all connections as connected
- **AND** `status_summary()` shows all green checkmarks

#### Scenario: Milvus is unreachable
- **WHEN** Milvus host is configured but unreachable
- **THEN** `milvus_connected` is false
- **AND** other services still connect normally
- **AND** the application starts successfully

#### Scenario: Only PostgreSQL is available
- **WHEN** no external services are configured (empty host strings)
- **THEN** the Infrastructure object is returned with all optional connections disabled
- **AND** the application runs in PG-only mode

### Requirement: Infrastructure SHALL provide status summary
`status_summary()` MUST return a human-readable string showing connection status for all services.

#### Scenario: Status summary is printed on startup
- **WHEN** the application starts
- **THEN** the startup log includes a status line like "Milvus: ✅ | ES: ✅ | Neo4j: ⚠️ disconnected | Kafka: ⚠️ not configured"

### Requirement: Infrastructure SHALL support graceful degradation
Each service failure SHALL trigger a documented degradation path: Milvus → TF cosine memory search; ES → no full-text search; Neo4j → GraphMemory no-op; Kafka → InProcess EventBus only.

#### Scenario: Memory recall degrades when Milvus is unavailable
- **WHEN** Milvus is disconnected and memory recall is called
- **THEN** LTM falls back to in-memory cosine similarity computation
- **AND** results are still returned (with lower quality)

#### Scenario: RAG search degrades when Elasticsearch is unavailable
- **WHEN** ES is disconnected and RAG search is called
- **THEN** BM25 keyword path is skipped in RRF fusion
- **AND** results come from Milvus semantic path only

#### Scenario: GraphMemory is no-op when Neo4j is unavailable
- **WHEN** Neo4j is disconnected
- **THEN** all GraphMemory methods return empty results without raising exceptions
- **AND** consolidation skips graph centrality protection

### Requirement: Infrastructure connections SHALL use async clients
All service clients MUST use async-compatible drivers: pymilvus for Milvus, AsyncElasticsearch for ES, neo4j AsyncDriver for Neo4j.

#### Scenario: Async ES client performs search
- **WHEN** an ES search is executed
- **THEN** it uses `AsyncElasticsearch` and can be awaited within the async event loop
