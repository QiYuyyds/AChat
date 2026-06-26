## ADDED Requirements

### Requirement: Agents SHALL be able to search knowledge base via rag_search
AgentHub MUST provide a `rag_search` tool that searches the RAG knowledge base. The tool SHALL accept `query` (string) and `top_k` (integer) parameters and return a list of HybridResult objects with content, score, and source.

#### Scenario: Agent searches knowledge base
- **WHEN** an agent calls `rag_search` with `query="Python best practices"` and `top_k=3`
- **THEN** the tool delegates to `RAGService.search()` and returns up to 3 relevant chunks

#### Scenario: Knowledge base is empty
- **WHEN** `rag_search` is called but no documents have been ingested
- **THEN** an empty result list is returned

### Requirement: Agents SHALL be able to ingest documents via rag_ingest
AgentHub MUST provide a `rag_ingest` tool that ingests document text into the RAG knowledge base. The tool SHALL accept `doc_hash` (string) and `content` (string) parameters and return `{chunk_count: int}`.

#### Scenario: Agent ingests a document
- **WHEN** an agent calls `rag_ingest` with a document hash and text content
- **THEN** the document is split, embedded, and indexed across PG/Milvus/ES
- **AND** the tool returns the number of chunks created

### Requirement: Agents SHALL be able to recall memories via memory_recall
AgentHub MUST provide a `memory_recall` tool that recalls relevant long-term memories. The tool SHALL accept `query` (string), `top_k` (integer), and optional `categories` (list of strings) parameters and return a list of Item objects.

#### Scenario: Agent recalls relevant memories
- **WHEN** an agent calls `memory_recall` with `query="user preferences"` and `top_k=3`
- **THEN** the tool delegates to `memory_service.ltm.recall_by_filter()` and returns matching memories

#### Scenario: Memory recall with category filter
- **WHEN** `memory_recall` is called with `categories=["preference", "fact"]`
- **THEN** only memories matching those categories are returned
