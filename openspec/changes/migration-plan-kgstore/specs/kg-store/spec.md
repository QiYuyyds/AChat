## ADDED Requirements

### Requirement: KGStore SHALL extract entities and relations from text via LLM

The Extractor SHALL accept an optional `llm_fn` callback with signature `(system_prompt: str, user_msg: str) -> str` and use it to extract named entities and typed relations from arbitrary text. The system prompt SHALL constrain entity types to Person, Organization, Location, Concept, Event, Product, Unknown and relation types to RELATES_TO, PART_OF, CAUSES, DESCRIBES, MENTIONS, WORKS_FOR, LOCATED_IN. The Extractor SHALL parse LLM output as JSON and sanitize results by deduplicating entity names and coercing invalid types to Unknown / RELATES_TO.

#### Scenario: LLM extracts entities from document chunk
- **WHEN** `Extractor.extract(text)` is called with a non-empty text and a valid `llm_fn`
- **THEN** it returns an `ExtractResult` containing sanitized `entities` and `relations` lists
- **AND** each entity has a non-empty `name` and a valid `type`
- **AND** each relation has non-empty `from_name`, `to_name`, and a valid `rel_type`

#### Scenario: LLM callback is not configured
- **WHEN** `Extractor` was constructed with `llm_fn=None`
- **AND** `extract(text)` is called
- **THEN** it returns an empty `ExtractResult` without raising an exception

#### Scenario: LLM output is unparseable
- **WHEN** the LLM returns output that cannot be parsed as JSON
- **THEN** the Extractor returns an empty `ExtractResult` and logs a warning
- **AND** no exception is propagated to the caller

#### Scenario: LLM call raises an exception
- **WHEN** the `llm_fn` callback raises an exception during execution
- **THEN** the Extractor returns an empty `ExtractResult` and logs a warning
- **AND** no exception is propagated to the caller

### Requirement: KGStore SHALL index document entities and relations into Neo4j

The KGStore SHALL provide an async `index_document(doc_hash, chunks)` method that iterates over chunks, extracts entities and relations via the Extractor, and writes them to Neo4j as `:Entity` nodes (with `name`, `type`, `doc_hash`, `chunk_id`, `pg_id` properties) and dynamically-typed relationship edges (with `doc_hash`, `chunk_id`, `pg_id` properties). All writes SHALL use MERGE for idempotency. The method SHALL be callable as a fire-and-forget async task without blocking the caller.

#### Scenario: Document with multiple chunks is indexed
- **WHEN** `index_document(doc_hash, chunks)` is called with 3 chunks containing extractable entities
- **THEN** Neo4j contains `:Entity` nodes with the given `doc_hash` and `pg_id` properties
- **AND** Neo4j contains relationship edges between extracted entities with the given `doc_hash`
- **AND** duplicate entity names across chunks are merged via MERGE

#### Scenario: Chunk with no extractable entities
- **WHEN** the Extractor returns an empty `ExtractResult` for a chunk
- **THEN** no entities or relations are written for that chunk
- **AND** processing continues with the next chunk without error

#### Scenario: Neo4j is not available
- **WHEN** `index_document` is called and the Neo4j driver is `None` or unreachable
- **THEN** the method returns without raising an exception
- **AND** no entities or relations are written

### Requirement: KGStore SHALL search via entity matching and multi-hop traversal

The KGStore SHALL provide an async `search(query_text, top_k)` method that extracts entities from the query text, matches them against `:Entity` nodes in Neo4j, and performs 1~2 hop subgraph traversal to find associated chunks. Results SHALL be returned as a list of dicts with `pg_id`, `content`, `score`, and `entities` keys, sorted by score descending, limited to `top_k`. The score SHALL be proportional to the number of seed entities matched and the node degree.

#### Scenario: Query matches known entities
- **WHEN** `search(query_text, top_k)` is called and the query contains entity names that exist in Neo4j
- **THEN** it returns a list of dicts with `pg_id` values of associated chunks
- **AND** each result has a positive `score` value
- **AND** the `entities` field lists the seed entity names that contributed to the match
- **AND** results are sorted by score descending and limited to `top_k`

#### Scenario: Query contains no known entities
- **WHEN** the Extractor returns no entities from the query text
- **THEN** `search` returns an empty list without executing any Cypher query

#### Scenario: APOC plugin is not available
- **WHEN** the `apoc.path.subgraphNodes` call raises an exception
- **THEN** the search degrades to `_search_direct` which matches entities by name only
- **AND** results contain only directly matched entity chunks (no multi-hop expansion)
- **AND** no exception is propagated to the caller

#### Scenario: Neo4j is not available
- **WHEN** `search` is called and the Neo4j driver is `None` or unreachable
- **THEN** it returns an empty list without raising an exception

### Requirement: KGStore SHALL delete document entities and relations by doc_hash

The KGStore SHALL provide an async `delete_document(doc_hash)` method that deletes all relationship edges with the given `doc_hash` and removes orphan `:Entity` nodes (nodes with no remaining relationships) that have the given `doc_hash`. The method SHALL be best-effort and SHALL NOT raise exceptions on Neo4j failures.

#### Scenario: Document is deleted from KG
- **WHEN** `delete_document(doc_hash)` is called for a doc_hash that has entities and relations in Neo4j
- **THEN** all relationship edges with that `doc_hash` are deleted
- **AND** orphan `:Entity` nodes with that `doc_hash` (no remaining relationships) are deleted
- **AND** entity nodes still referenced by other documents are preserved

#### Scenario: Neo4j is not available during deletion
- **WHEN** `delete_document` is called and the Neo4j driver is `None` or unreachable
- **THEN** the method returns without raising an exception

### Requirement: KGStore SHALL degrade gracefully when LLM is unavailable

When the `llm_fn` callback is `None` or raises exceptions, the KGStore SHALL silently degrade: `index_document` SHALL skip all chunks (no entity extraction), `search` SHALL return an empty list (no query entity extraction). This degradation SHALL NOT affect Milvus or ES retrieval paths.

#### Scenario: RAG search with LLM unavailable
- **WHEN** `rag_search` is called and the KGStore's `llm_fn` is `None`
- **THEN** the KG search path returns an empty `_PathHits(ok=False)`
- **AND** the Milvus and ES search paths continue to function normally
- **AND** RRF fusion proceeds with only semantic and keyword weights

### Requirement: HybridStore SHALL wire KG search, index, and delete callbacks

The HybridStore SHALL accept three KG-related callbacks: `set_kg_backend(search_fn)` for the async search function, `set_kg_index_fn(fn)` for the async index function, and `set_kg_delete_fn(fn)` for the async delete function. The `index_chunks` method SHALL call the KG index function as a fire-and-forget task after PG/Milvus/ES indexing completes. The `_fetch_kg` method SHALL `await` the async KG search function. The `_kg_ok` method SHALL return `True` when the search function is injected.

#### Scenario: KG backend is wired and document is ingested
- **WHEN** `set_kg_backend`, `set_kg_index_fn` are called with valid callbacks
- **AND** `index_chunks` is called for a new document
- **THEN** a fire-and-forget task is created to call `kg_index_fn(doc_hash, chunk_refs)`
- **AND** the main `index_chunks` flow returns `pg_ids` without waiting for KG indexing to complete

#### Scenario: KG search is invoked during hybrid search
- **WHEN** `_search_hybrid` is called and `_kg_ok()` returns `True`
- **THEN** `_fetch_kg` awaits the async `_kg_search_fn(query, fetch_k)`
- **AND** KG hits participate in RRF fusion with `kg_weight` contribution

#### Scenario: KG backend is not wired
- **WHEN** `set_kg_backend` was never called
- **AND** `_search_hybrid` is invoked
- **THEN** `_fetch_kg` returns `_PathHits(ok=False)` immediately
- **AND** RRF fusion proceeds with only Milvus and ES paths

### Requirement: RAGService SHALL passthrough KG callbacks to HybridStore

The RAGService SHALL provide `set_kg_backend(search_fn)`, `set_kg_index_fn(fn)`, and `set_kg_delete_fn(fn)` methods that delegate to the underlying HybridStore. The `delete_by_doc_hash` method SHALL call the KG delete function with the document hash when available.

#### Scenario: KG callbacks are set on RAGService
- **WHEN** `rag_service.set_kg_backend(fn)` and `rag_service.set_kg_index_fn(fn)` are called
- **THEN** the underlying HybridStore receives the callbacks via its own setters

#### Scenario: Document deletion triggers KG cleanup
- **WHEN** `delete_by_doc_hash(doc_hash)` is called and `_kg_delete_fn` is set
- **THEN** the KG delete function is called with the `doc_hash` parameter
- **AND** any exception from the KG delete function is caught and logged without aborting ES/Milvus cleanup

### Requirement: main.py SHALL wire KGStore into RAGService at startup

The application startup SHALL create a KGStore instance with the shared Neo4j `AsyncDriver` and the existing `generate_fn` LLM callback, and wire its search/index/delete functions into the RAGService when the Neo4j driver and `generate_fn` are both available. The startup SHALL log `RAG: KG backend wired` on success.

#### Scenario: Neo4j and LLM are both available at startup
- **WHEN** `_infrastructure.neo4j_driver` is not `None`
- **AND** `generate_fn` is not `None`
- **THEN** `_wire_kg_to_rag` creates a KGStore and wires search/index/delete callbacks into RAGService
- **AND** the startup log contains `RAG: KG backend wired`

#### Scenario: Neo4j is not available at startup
- **WHEN** `_infrastructure.neo4j_driver` is `None`
- **THEN** KG wiring is skipped
- **AND** the startup log does NOT contain `RAG: KG backend wired`
- **AND** RAGService continues to initialize with Milvus and ES paths only

#### Scenario: LLM generate_fn is not available at startup
- **WHEN** `generate_fn` is `None` (no API key configured)
- **THEN** KG wiring is skipped because the Extractor requires an LLM callback
- **AND** RAGService continues to initialize without the KG path
