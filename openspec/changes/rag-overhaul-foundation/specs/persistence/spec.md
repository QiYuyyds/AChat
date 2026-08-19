## Modified Requirements

### Requirement: RAG chunks table SHALL support extended metadata

The `rag_chunks` table MUST include `chunk_token_count INTEGER DEFAULT 0`, `start_char_pos INTEGER` (nullable), and `end_char_pos INTEGER` (nullable) columns to support downstream chunking presets and quality evaluation.

#### Scenario: Chunk with token count is persisted
- **WHEN** a chunk is persisted with `chunk_token_count = 150`
- **THEN** the `rag_chunks` row stores `chunk_token_count = 150`
- **AND** the value can be retrieved for retrieval and evaluation purposes

### Requirement: Documents table SHALL support chunk preset and graph status

The `documents` table MUST include `chunk_preset VARCHAR(32) DEFAULT 'general'` and `graph_status VARCHAR(16) DEFAULT NULL` columns. The `chunk_preset` column records which chunking preset was used for the document. The `graph_status` column tracks the knowledge graph build lifecycle independent of the document's `status` column.

#### Scenario: Document is created with default chunk preset
- **WHEN** a new document is created
- **THEN** `chunk_preset` defaults to `'general'`
- **AND** `graph_status` is `NULL`

#### Scenario: Document graph status transitions
- **WHEN** a document's graph build is triggered
- **THEN** `graph_status` transitions from `NULL` to `'graph_pending'`
- **AND** subsequently to `'graph_building'` then `'graph_indexed'` or `'error_graph'`
