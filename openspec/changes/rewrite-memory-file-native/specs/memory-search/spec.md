# Memory Search

> 实现细节不明确时，查阅 ReMe 源码 `待融合项目/ReMe/reme/steps/index/search.py`。

## ADDED Requirements

### Requirement: Memory search SHALL use BM25 + wikilink expansion with RRF fusion

Memory search SHALL combine SQLite FTS5 BM25 keyword matching with wikilink graph relation expansion, fused via Reciprocal Rank Fusion (RRF, k=60). BM25 weight SHALL be 0.7, wikilink expansion weight SHALL be 0.3.

#### Scenario: Keyword-only search
- **WHEN** `search(query="React hooks debug")` is called
- **THEN** BM25 returns chunks matching keywords from `daily/` and `digest/` files
- **AND** wikilink expansion adds files linked from initial BM25 hits
- **AND** results are RRF-fused and sorted by combined score

#### Scenario: Search with agent_id filter
- **WHEN** `search(query="debug patterns", agent_id="ag_coder")` is called
- **THEN** results are filtered to include only global (agent_id=null) and ag_coder-scoped nodes
- **AND** agent-scoped nodes from other agents are excluded

#### Scenario: Search with bucket filter
- **WHEN** `search(query="how to refactor", bucket="procedure")` is called
- **THEN** results are restricted to `digest/procedure/` files only

### Requirement: BM25 index SHALL use SQLite FTS5 with dual tokenizer

The BM25 index SHALL use SQLite FTS5 with jieba tokenizer for Chinese content and simple tokenizer for English content. The index SHALL be stored in `<agenthub-data>/memory/metadata/bm25.db`.

#### Scenario: Chinese content is indexed
- **WHEN** a daily card with Chinese content is indexed
- **THEN** jieba segmentation is applied before FTS5 insertion
- **AND** Chinese keyword search returns the card

### Requirement: Wikilink graph SHALL support BFS expansion

The wikilink adjacency graph SHALL support 1-hop BFS expansion from seed files. The graph SHALL be stored in `<agenthub-data>/memory/metadata/wikilinks.db` (SQLite table with source_path and target_path columns).

#### Scenario: Wikilink expansion from search hits
- **WHEN** BM25 returns files A and B, and A has wikilinks to C and D
- **THEN** files C and D are added to the result set with wikilink expansion score
- **AND** expansion depth is limited to 1 hop
