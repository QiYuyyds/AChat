# Memory Search

## ADDED Requirements

### Requirement: node_search SHALL provide digest-only node-level search for dream integrate

A dedicated `node_search` function SHALL search only `digest/` directory files, aggregating multiple hits on the same file into a single node-level result. Each result SHALL include the file path, frontmatter (name + description), and the highest hit score. `node_search` SHALL NOT apply link expansion or agent_id filtering, as it is designed for cross-agent recall during dream integrate.

#### Scenario: dream integrate searches for existing digest nodes
- **WHEN** `node_search(query="React state management patterns")` is called
- **THEN** only files under `digest/` are searched
- **AND** multiple hits on the same file are aggregated into one result
- **AND** each result includes the file's frontmatter name and description
- **AND** results are sorted by combined BM25 + wikilink score

#### Scenario: node_search returns no results for novel abstraction
- **WHEN** `node_search(query="quantum entanglement debugging")` is called
- **AND** no digest files match the query
- **THEN** an empty list is returned
- **AND** dream integrate creates a new digest node (CREATE action)

## MODIFIED Requirements

### Requirement: Memory search SHALL use BM25 + wikilink expansion with RRF fusion and return score breakdown

Memory search SHALL combine SQLite FTS5 BM25 keyword matching with wikilink graph relation expansion, fused via Reciprocal Rank Fusion (RRF, k=60). BM25 weight SHALL be 0.7, wikilink expansion weight SHALL be 0.3.

Each search result SHALL include a `scores` dictionary with individual component scores (`bm25`, `wikilink`, `rrf`) to allow callers to understand hit provenance. Results SHALL also include an `expansion` field with outlink and inlink neighbor metadata (path, name, description) for each result, enabling callers to see related files without a follow-up query.

#### Scenario: Keyword-only search with score breakdown
- **WHEN** `search(query="React hooks debug")` is called
- **THEN** BM25 returns chunks matching keywords from `daily/` and `digest/` files
- **AND** wikilink expansion adds files linked from initial BM25 hits
- **AND** results are RRF-fused and sorted by combined score
- **AND** each result includes `scores: {"bm25": <float>, "wikilink": <float>, "rrf": <float>}`

#### Scenario: Search result includes link expansion metadata
- **WHEN** a search result for file A has outlinks to C and D
- **THEN** the result includes `expansion: {"outlinks": [{"path": "C", "name": "...", "description": "..."}, ...], "inlinks": [...]}`
- **AND** the caller can see related files without an additional query

#### Scenario: Search with agent_id filter
- **WHEN** `search(query="debug patterns", agent_id="ag_coder")` is called
- **THEN** results are filtered to include only global (agent_id=null) and ag_coder-scoped nodes
- **AND** agent-scoped nodes from other agents are excluded

#### Scenario: Search with bucket filter
- **WHEN** `search(query="how to refactor", bucket="procedure")` is called
- **THEN** results are restricted to `digest/procedure/` files only
